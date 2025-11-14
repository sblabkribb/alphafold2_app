import argparse
import base64
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any

import runpod
import requests

logger = logging.getLogger("alphafold.handler")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

_INITIALIZED = False


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


def _prepare_fasta(input_payload: Dict[str, Any], working_dir: Path) -> Path:
    if "fasta_path" in input_payload:
        original = Path(input_payload["fasta_path"]).expanduser()
        if not original.is_file():
            raise FileNotFoundError(f"FASTA path not found: {original}")
        destination = working_dir / original.name
        shutil.copy2(original, destination)
        return destination

    if "sequence" in input_payload:
        sequence = input_payload["sequence"]
        if not sequence or not isinstance(sequence, str):
            raise ValueError("Sequence must be a non-empty string.")
        return _write_fasta_from_sequence(sequence, working_dir / "input.fasta")

    if "fasta_url" in input_payload:
        return _download_fasta(input_payload["fasta_url"], working_dir / "input.fasta")

    raise ValueError("Input must include one of 'sequence', 'fasta_path', or 'fasta_url'.")


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
    archive_b64 = None
    if os.environ.get("RETURN_ARCHIVE", "1") == "1":
        archive_path = shutil.make_archive(
            base_name=str(output_dir / "results"),
            format="gztar",
            root_dir=output_dir,
        )
        archive_file = Path(archive_path)
        archive_b64 = base64.b64encode(archive_file.read_bytes()).decode("ascii")
        archive_file.unlink(missing_ok=True)
    return {"files": files, "archive_base64": archive_b64}


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
        fasta_path = _prepare_fasta(input_payload, working_dir)

        output_dir = Path(
            input_payload.get("output_dir")
            or os.environ.get("ALPHAFOLD_OUTPUT", "/outputs")
        )
        output_dir.mkdir(parents=True, exist_ok=True)

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
            str(fasta_path),
            str(output_dir),
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

    outputs = _collect_outputs(output_dir)
    return {
        "status": "success",
        "output_dir": str(output_dir),
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

    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
