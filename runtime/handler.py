import argparse
import base64
import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Dict, Any, Iterable, List

import runpod
import requests

logger = logging.getLogger("alphafold.handler")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

_INITIALIZED = False
_FASTA_EXTENSIONS = (".fa", ".fasta", ".faa", ".fas")
_DEFAULT_ARCHIVE_PATTERNS = [
    "ranked_*.pdb",
    "relaxed_model_*_pred_0.pdb",
    "ranking_debug.json",
    "relax_metrics.json",
    "timings.json",
    "plddt.*",
    "pae*",
]


def _run_script(cmd: list[str]) -> str:
    try:
        out = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ).stdout
        return out
    except subprocess.CalledProcessError as exc:
        return exc.stdout or str(exc)


def _init_once():
    global _INITIALIZED
    if _INITIALIZED:
        return
    logger.info("[INIT] Starting storage bootstrap + diagnostics")
    logger.info(
        "[INIT] ENV ALPHAFOLD_DB_PATH=%s, RUNPOD_VOLUME_ROOT=%s",
        os.environ.get("ALPHAFOLD_DB_PATH", "/data/alphafold"),
        os.environ.get("RUNPOD_VOLUME_ROOT", "/runpod-volume"),
    )

    # Run bootstrap (idempotent)
    bootstrap_out = _run_script(["/bin/bash", "/app/bootstrap_db.sh", "--diagnose"])
    for line in bootstrap_out.splitlines():
        logger.info("%s", line)

    # Additional diagnosis snapshot
    diag_out = _run_script(["/bin/bash", "/app/diagnose_storage.sh"])
    for line in diag_out.splitlines():
        logger.info("%s", line)

    _INITIALIZED = True
    logger.info("[INIT] Initialization finished")


def _write_fasta_from_sequence(sequence: str, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with target_path.open("w", encoding="utf-8") as fasta_file:
        fasta_file.write(">query\n")
        fasta_file.write(sequence.strip() + "\n")
    return target_path


def _download_fasta(url: str, target_path: Path) -> Path:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(response.content)
    return target_path


def _is_fasta_file(path: Path) -> bool:
    return path.suffix.lower() in _FASTA_EXTENSIONS


def _copy_fasta_files(source_files: Iterable[Path], destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in source_files:
        if src.is_file() and _is_fasta_file(src):
            shutil.copy2(src, destination_dir / src.name)
            copied += 1
    if copied == 0:
        raise ValueError(f"No FASTA files found to copy into {destination_dir}")
    return destination_dir


def _safe_extract_tar_bytes(data: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
        dest_root = destination.resolve()
        for member in tar.getmembers():
            member_path = destination / member.name
            try:
                member_resolved = member_path.resolve()
            except FileNotFoundError:
                # Path may not exist yet; resolve parent
                member_resolved = (destination / Path(member.name).parent).resolve()
            if not str(member_resolved).startswith(str(dest_root)):
                raise ValueError("Uploaded archive contains unsafe paths.")
        tar.extractall(path=destination)


def _materialize_uploaded_inputs(input_payload: Dict[str, Any], working_dir: Path) -> None:
    upload = input_payload.get("input_archive")
    if not upload:
        return

    logger.info("Decoding uploaded FASTA archive (kind=%s)", upload.get("kind"))
    archive_b64 = upload.get("base64")
    if not archive_b64:
        raise ValueError("input_archive missing base64 payload")

    try:
        archive_bytes = base64.b64decode(archive_b64)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("Failed to decode base64 input archive") from exc

    extract_dir = working_dir / "uploaded_inputs"
    _safe_extract_tar_bytes(archive_bytes, extract_dir)

    kind = upload.get("kind")
    if kind == "fasta_dir":
        root_name = upload.get("root")
        candidate = extract_dir / root_name if root_name else extract_dir
        if not candidate.exists():
            sub_entries = sorted(extract_dir.iterdir())
            if len(sub_entries) == 1:
                candidate = sub_entries[0]
        if not candidate.exists():
            raise ValueError("Uploaded archive did not contain the expected FASTA directory")
        input_payload["fasta_dir"] = str(candidate)
    elif kind == "fasta_paths":
        fasta_files = [p for p in extract_dir.rglob("*") if p.is_file() and _is_fasta_file(p)]
        if not fasta_files:
            raise ValueError("Uploaded FASTA archive did not contain any FASTA files")
        ordered: List[Path] = []
        file_names = upload.get("file_names") or []
        if file_names:
            lookup = {p.name: p for p in fasta_files}
            for name in file_names:
                path = lookup.pop(name, None)
                if path:
                    ordered.append(path)
            ordered.extend(sorted(lookup.values()))
        else:
            ordered = sorted(fasta_files)
        input_payload["fasta_paths"] = [str(p) for p in ordered]
    else:
        raise ValueError(f"Unknown uploaded input kind: {kind}")

    input_payload.pop("input_archive", None)


def _prepare_fasta_inputs(input_payload: Dict[str, Any], working_dir: Path) -> Path:
    if "fasta_path" in input_payload:
        original = Path(input_payload["fasta_path"]).expanduser()
        if not original.is_file():
            raise FileNotFoundError(f"FASTA path not found: {original}")
        destination = working_dir / original.name
        shutil.copy2(original, destination)
        return destination

    if "fasta_paths" in input_payload:
        paths = input_payload["fasta_paths"]
        if not isinstance(paths, list) or not paths:
            raise ValueError("fasta_paths must be a non-empty list of file paths.")
        expanded = [Path(p).expanduser() for p in paths]
        missing = [str(p) for p in expanded if not p.is_file()]
        if missing:
            raise FileNotFoundError(f"FASTA paths not found: {', '.join(missing)}")
        return _copy_fasta_files(expanded, working_dir / "batch_fasta")

    if "fasta_dir" in input_payload:
        source_dir = Path(input_payload["fasta_dir"]).expanduser()
        if not source_dir.is_dir():
            raise NotADirectoryError(f"FASTA directory not found: {source_dir}")
        return _copy_fasta_files(sorted(source_dir.iterdir()), working_dir / source_dir.name)

    if "sequence_list" in input_payload:
        sequence_list = input_payload["sequence_list"]
        if not isinstance(sequence_list, list) or not sequence_list:
            raise ValueError("sequence_list must be a non-empty list of sequences.")
        batch_dir = working_dir / "batch_sequences"
        for idx, seq in enumerate(sequence_list, start=1):
            if not isinstance(seq, str) or not seq.strip():
                raise ValueError("Each sequence in sequence_list must be a non-empty string.")
            _write_fasta_from_sequence(seq, batch_dir / f"sequence_{idx}.fasta")
        return batch_dir

    if "sequence" in input_payload:
        sequence = input_payload["sequence"]
        if not sequence or not isinstance(sequence, str):
            raise ValueError("Sequence must be a non-empty string.")
        return _write_fasta_from_sequence(sequence, working_dir / "input.fasta")

    if "fasta_url" in input_payload:
        return _download_fasta(input_payload["fasta_url"], working_dir / "input.fasta")

    raise ValueError(
        "Input must include one of 'sequence', 'sequence_list', 'fasta_path', "
        "'fasta_paths', 'fasta_dir', or 'fasta_url'."
    )


def _archive_selected_outputs(target_dir: Path) -> str | None:
    pattern_str = os.environ.get("ARCHIVE_PATTERNS")
    if pattern_str:
        patterns = [p.strip() for p in pattern_str.split(",") if p.strip()]
    else:
        patterns = _DEFAULT_ARCHIVE_PATTERNS

    matches: List[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for match in target_dir.glob(pattern):
            rel = match.relative_to(target_dir)
            if rel not in seen:
                matches.append(match)
                seen.add(rel)

    if not matches:
        return None

    archive_path = target_dir / "results.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        for match in matches:
            tar.add(match, arcname=str(match.relative_to(target_dir)))

    archive_b64 = base64.b64encode(archive_path.read_bytes()).decode("ascii")
    archive_path.unlink(missing_ok=True)
    return archive_b64


def _prepare_archives(output_dir: Path) -> List[Dict[str, str]]:
    archives: List[Dict[str, str]] = []
    candidates = [p for p in sorted(output_dir.iterdir()) if p.is_dir()]
    if not candidates:
        candidates = [output_dir]
    for target in candidates:
        archive_b64 = _archive_selected_outputs(target)
        if archive_b64:
            archives.append(
                {
                    "name": f"{target.name}.tar.gz",
                    "base64": archive_b64,
                }
            )
    return archives


def _collect_outputs(output_dir: Path) -> Dict[str, Any]:
    files = []
    for file_path in sorted(output_dir.rglob("*")):
        if file_path.is_file():
            files.append(
                {
                    "name": str(file_path.relative_to(output_dir)),
                    "size": file_path.stat().st_size,
                }
            )
    archives: List[Dict[str, str]] = []
    if os.environ.get("RETURN_ARCHIVE", "1") == "1":
        archives = _prepare_archives(output_dir)
    result: Dict[str, Any] = {"files": files}
    if archives:
        result["archives"] = archives
        if len(archives) == 1:
            result["archive_base64"] = archives[0]["base64"]
    return result


def _spawn_background(cmd: list[str], env: Dict[str, str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab", buffering=0) as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, env=env)
    return proc.pid


def _db_status(db_root: Path) -> Dict[str, Any]:
    targets = [
        "bfd",
        "uniref90",
        "mgnify",
        "pdb_mmcif",
        "pdb70",
        "models",
        "params",
    ]
    sizes: Dict[str, Any] = {}
    for t in targets:
        p = db_root / t
        if p.exists():
            try:
                out = subprocess.check_output(["du", "-sh", str(p)], text=True).strip().split("\t")[0]
            except Exception:
                out = None
            sizes[t] = {"exists": True, "size": out}
        else:
            sizes[t] = {"exists": False, "size": None}
    procs = {}
    for name in ("download_all_data.sh", "aria2c", "rsync"):
        try:
            out = subprocess.check_output(["bash", "-lc", f"pgrep -fa {name} || true"], text=True)
        except Exception:
            out = ""
        procs[name] = [l.strip() for l in out.splitlines() if l.strip()]
    try:
        total = subprocess.check_output(["du", "-sh", str(db_root)], text=True).strip().split("\t")[0]
    except Exception:
        total = None
    return {"root": str(db_root), "total": total, "dirs": sizes, "procs": procs}


def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("Received event: %s", json.dumps(event)[:512])

    input_payload: Dict[str, Any] = event.get("input") or {}

    # Action mode: allow service-triggered DB bootstrap/preload and status checks
    action = input_payload.get("action")
    if action:
        ALPHAFOLD_DB_PATH = Path(os.environ.get("ALPHAFOLD_DB_PATH", "/data/alphafold"))
        RUNPOD_VOLUME_ROOT = Path(os.environ.get("RUNPOD_VOLUME_ROOT", "/runpod-volume"))
        data_dir = ALPHAFOLD_DB_PATH
        if RUNPOD_VOLUME_ROOT.exists():
            # In our bootstrap we symlink /data/alphafold -> /runpod-volume/alphafold
            pass

        if action in {"status", "diagnose"}:
            return {
                "status": "ok",
                "mode": action,
                "db": _db_status(data_dir),
            }

        if action == "stop":
            stopped = {}
            for name in ("download_all_data.sh", "bootstrap_db.sh", "aria2c"):
                try:
                    out = subprocess.check_output(["bash", "-lc", f"pgrep -fa {name} || true"], text=True)
                    pids = []
                    for line in out.splitlines():
                        if not line.strip():
                            continue
                        pid = int(line.split()[0])
                        subprocess.run(["kill", str(pid)], check=False)
                        pids.append(pid)
                    stopped[name] = pids
                except Exception as e:
                    stopped[name] = {"error": str(e)}
            return {"status": "ok", "mode": "stop", "stopped": stopped}

        if action == "preload":
            preset = input_payload.get("preset") or os.environ.get("DB_AUTO_PRESET", "reduced_dbs")
            allow_download = input_payload.get("allow_download", True)
            tar_opts = input_payload.get("tar_options") or os.environ.get("TAR_OPTIONS", "--no-same-owner --skip-old-files")
            log_path = Path(input_payload.get("log_path") or (data_dir / ("bootstrap_" + preset + ".log")))
            env = os.environ.copy()
            if allow_download:
                env["ALLOW_DB_AUTO_DOWNLOAD"] = "1"
            env["DB_AUTO_PRESET"] = preset
            env["TAR_OPTIONS"] = tar_opts
            # Ensure single downloader via flock
            cmd = [
                "bash",
                "-lc",
                f"flock -n {str(data_dir)}/.dl.lock -c '/app/bootstrap_db.sh --diagnose'",
            ]
            pid = _spawn_background(cmd, env, log_path)
            return {
                "status": "started",
                "mode": "preload",
                "preset": preset,
                "pid": pid,
                "log": str(log_path),
                "db": _db_status(data_dir),
            }

        return {"status": "error", "message": f"Unknown action: {action}"}

    with tempfile.TemporaryDirectory(prefix="alphafold-input-") as working_dir_str:
        working_dir = Path(working_dir_str)
        _materialize_uploaded_inputs(input_payload, working_dir)
        fasta_input = _prepare_fasta_inputs(input_payload, working_dir)

        output_parent = Path(
            input_payload.get("output_dir")
            or os.environ.get("ALPHAFOLD_OUTPUT", "/outputs")
        )
        output_parent.mkdir(parents=True, exist_ok=True)
        job_output_dir = Path(
            tempfile.mkdtemp(prefix="job-", dir=str(output_parent))
        )

        env = os.environ.copy()
        if "model_preset" in input_payload:
            env["MODEL_PRESET"] = input_payload["model_preset"]
        if "db_preset" in input_payload:
            env["DB_PRESET"] = input_payload["db_preset"]
        if "max_template_date" in input_payload:
            env["MAX_TEMPLATE_DATE"] = input_payload["max_template_date"]
        if "alphafold_extra_flags" in input_payload:
            env["ALPHAFOLD_EXTRA_FLAGS"] = input_payload["alphafold_extra_flags"]

        command = [
            "/bin/bash",
            "/app/run_alphafold.sh",
            str(fasta_input),
            str(job_output_dir),
        ]

        logger.info("Running command: %s", " ".join(command))
        try:
            subprocess.run(
                command,
                check=True,
                env=env,
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("Alphafold execution failed: %s", exc.stdout.decode("utf-8"))
            return {
                "status": "error",
                "message": "Alphafold execution failed.",
                "details": exc.stdout.decode("utf-8"),
            }

    outputs = _collect_outputs(job_output_dir)
    job_dir_str = str(job_output_dir)
    should_cleanup = os.environ.get("PRESERVE_JOB_OUTPUT", "0") != "1"
    if should_cleanup:
        try:
            shutil.rmtree(job_output_dir, ignore_errors=True)
        except Exception as cleanup_error:  # pragma: no cover
            logger.warning("Failed to remove job output dir %s: %s", job_dir_str, cleanup_error)

    return {
        "status": "success",
        "output_dir": job_dir_str,
        "output_parent": str(output_parent),
        **outputs,
    }


def _self_test():
    """Basic validation used during build-time."""
    run_alphafold = Path("/app/run_alphafold.sh")
    if not run_alphafold.exists():
        raise SystemExit("run_alphafold.sh missing.")
    print("Self-test passed. Runtime scripts are present.")


def _run_local(fasta_path: str):
    event = {"input": {"fasta_path": fasta_path}}
    result = handler(event)
    print(json.dumps(result, indent=2))


def _idle_forever():
    """Keep the pod process alive when RUN_MODE=pod."""
    logger.info("[POD] RUN_MODE=pod; entering idle loop. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("[POD] Idle loop interrupted; exiting.")


def main():
    parser = argparse.ArgumentParser(description="RunPod Alphafold handler entrypoint")
    parser.add_argument("--self-test", action="store_true", help="Run internal checks and exit.")
    parser.add_argument("--local", metavar="FASTA_PATH", help="Execute handler locally with a fasta file.")
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        return

    if args.local:
        _run_local(args.local)
        return

    # Initialize storage and log mount status before starting serverless
    try:
        _init_once()
    except Exception as e:
        logger.warning("[INIT] Bootstrap failed: %s", e)

    run_mode = os.environ.get("RUN_MODE", "serverless").lower()
    if run_mode == "pod":
        _idle_forever()
        return

    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
