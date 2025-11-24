#!/usr/bin/env python3

"""Submit AlphaFold jobs to a RunPod Serverless endpoint."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import tarfile
import time
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import certifi
import requests


def _read_sequence_from_fasta(fasta_path: Path) -> str:
    if not fasta_path.is_file():
        raise FileNotFoundError(f"FASTA not found: {fasta_path}")
    seq_lines = []
    with fasta_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith(">"):
                continue
            seq_lines.append(line)
    if not seq_lines:
        raise ValueError(f"No sequence content found in {fasta_path}")
    return "".join(seq_lines)


def _combine_with_certifi(user_ca_path: str) -> str:
    tmp = tempfile.NamedTemporaryFile(prefix="cafile-", suffix=".pem", delete=False)
    with open(user_ca_path, "rb") as f_in, open(certifi.where(), "rb") as f_cert, open(tmp.name, "wb") as f_out:
        f_out.write(f_in.read())
        f_out.write(b"\n")
        f_out.write(f_cert.read())
    return tmp.name


def _resolve_verify(ca_bundle: Optional[str], append_certifi: bool) -> Any:
    if ca_bundle:
        return _combine_with_certifi(ca_bundle) if append_certifi else ca_bundle
    for key in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        path = os.environ.get(key)
        if path:
            return _combine_with_certifi(path) if append_certifi else path
    return True


def build_payload(args: argparse.Namespace) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    payload: Dict[str, Any] = {}
    upload_spec: Optional[Dict[str, Any]] = None

    if args.fasta_dir:
        dir_path = Path(args.fasta_dir).expanduser()
        payload["fasta_dir"] = str(dir_path)
        upload_spec = {
            "kind": "fasta_dir",
            "path": dir_path,
            "archive_name": f"{dir_path.name}.tar.gz",
        }
    elif args.fasta_path:
        paths = [Path(p).expanduser() for p in args.fasta_path]
        payload["fasta_paths"] = [str(p) for p in paths]
        upload_spec = {
            "kind": "fasta_paths",
            "paths": paths,
            "archive_name": "fasta_inputs.tar.gz",
        }
    elif args.sequence_file:
        payload["sequence"] = _read_sequence_from_fasta(Path(args.sequence_file))
    elif args.sequence:
        payload["sequence"] = args.sequence.strip()
    elif args.fasta_url:
        payload["fasta_url"] = args.fasta_url
    else:
        raise SystemExit("Provide --sequence-file, --sequence, or --fasta-url")

    if args.model_preset:
        payload["model_preset"] = args.model_preset
    if args.db_preset:
        payload["db_preset"] = args.db_preset
    if args.max_template_date:
        payload["max_template_date"] = args.max_template_date
    if args.extra_flags:
        payload["alphafold_extra_flags"] = args.extra_flags
    return payload, upload_spec


def _create_upload_archive(upload_spec: Dict[str, Any]) -> Dict[str, Any]:
    kind = upload_spec["kind"]
    buffer = io.BytesIO()
    archive_name = upload_spec.get("archive_name") or "fasta_inputs.tar.gz"

    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        if kind == "fasta_dir":
            dir_path: Path = upload_spec["path"]
            if not dir_path.is_dir():
                raise FileNotFoundError(f"FASTA directory not found: {dir_path}")
            tar.add(str(dir_path), arcname=dir_path.name)
            upload_meta = {"root": dir_path.name}
        elif kind == "fasta_paths":
            paths = upload_spec["paths"]
            missing = [str(p) for p in paths if not p.is_file()]
            if missing:
                raise FileNotFoundError(f"FASTA file(s) not found: {', '.join(missing)}")
            for path in paths:
                tar.add(str(path), arcname=path.name)
            upload_meta = {"file_names": [p.name for p in paths]}
        else:
            raise ValueError(f"Unsupported upload spec kind: {kind}")

    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    upload_payload: Dict[str, Any] = {
        "kind": kind,
        "archive_name": archive_name,
        "base64": encoded,
    }
    upload_payload.update(upload_meta)
    return upload_payload


def maybe_attach_upload(payload: Dict[str, Any], upload_spec: Optional[Dict[str, Any]]) -> None:
    if not upload_spec:
        return
    upload_payload = _create_upload_archive(upload_spec)
    payload.pop("fasta_dir", None)
    payload.pop("fasta_paths", None)
    payload["input_archive"] = upload_payload


def submit_job(api_key: str, endpoint_id: str, payload: Dict[str, Any], verify: Any) -> str:
    url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = requests.post(url, headers=headers, json={"input": payload}, timeout=60, verify=verify)
    response.raise_for_status()
    data = response.json()
    job_id = data.get("id") or data.get("jobId")
    if not job_id:
        raise RuntimeError(f"Unexpected submit response: {json.dumps(data)[:500]}")
    return job_id


def poll_job(api_key: str, endpoint_id: str, job_id: str, verify: Any, interval: int, timeout: int) -> Dict[str, Any]:
    url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    start = time.time()
    while True:
        response = requests.get(url, headers=headers, timeout=30, verify=verify)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status") or payload.get("state")
        print(f"[poll] status={status}")
        if status in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return payload
        if time.time() - start > timeout:
            raise TimeoutError(f"Polling timed out after {timeout}s")
        time.sleep(interval)


def save_archives(output: Dict[str, Any], destination: Path) -> None:
    archives = output.get("archives") or []
    if not archives:
        archive_b64 = output.get("archive_base64")
        if not archive_b64:
            print("[!] No archive provided in response, skipping download.")
            return
        archives = [{"name": destination.name, "base64": archive_b64}]

    if len(archives) == 1:
        target_path = destination
        data = base64.b64decode(archives[0]["base64"])
        target_path.write_bytes(data)
        print(f"[+] Saved archive to {target_path}")
        return

    if destination.suffix:
        target_dir = destination.with_suffix("")
    else:
        target_dir = destination
    target_dir.mkdir(parents=True, exist_ok=True)

    for archive in archives:
        name = archive.get("name") or f"{time.time_ns()}.tar.gz"
        data = base64.b64decode(archive["base64"])
        path = target_dir / name
        path.write_bytes(data)
        print(f"[+] Saved archive to {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Submit AlphaFold job to RunPod Serverless endpoint")
    parser.add_argument("--sequence-file", help="Path to FASTA file to read")
    parser.add_argument("--sequence", help="Raw amino acid sequence")
    parser.add_argument("--fasta-url", help="Public FASTA URL")
    parser.add_argument("--fasta-dir", help="Directory containing multiple FASTA files")
    parser.add_argument("--fasta-path", action="append", dest="fasta_path", help="FASTA file path (repeatable)")
    parser.add_argument("--endpoint", default=os.environ.get("RUNPOD_ENDPOINT_ID"), help="RunPod endpoint ID")
    parser.add_argument("--api-key", default=os.environ.get("RUNPOD_API_KEY"), help="RunPod API key")
    parser.add_argument("--model-preset", default="monomer")
    parser.add_argument("--db-preset", default="full_dbs")
    parser.add_argument("--max-template-date", default="2020-05-14")
    parser.add_argument("--extra-flags", help="Extra flags passed to run_alphafold")
    parser.add_argument("--poll-interval", type=int, default=10, help="Polling interval seconds")
    parser.add_argument("--timeout", type=int, default=144000, help="Total poll timeout seconds")
    parser.add_argument("--save-archive", type=Path, default=Path("alphafold_results.tar.gz"))
    parser.add_argument("--ca-bundle", help="CA bundle for TLS inspection environments")
    parser.add_argument("--append-certifi", action="store_true", help="Append certifi bundle to provided CA")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification (development only)")
    parser.add_argument("--async", dest="do_async", action="store_true", help="Submit and print job id without polling")
    parser.add_argument("--status", help="Poll an existing job id and print its final result")
    parser.add_argument(
        "--upload-inputs",
        action="store_true",
        help="Package local FASTA files or directories into the submission payload for remote execution",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    api_key = args.api_key or os.environ.get("RUNPOD_API_KEY")
    endpoint_id = args.endpoint or os.environ.get("RUNPOD_ENDPOINT_ID")
    if not api_key or not endpoint_id:
        raise SystemExit("RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID must be provided (flags or env vars)")

    verify: Any
    if args.insecure or os.environ.get("INSECURE") == "1":
        verify = False
    else:
        verify = _resolve_verify(args.ca_bundle, append_certifi=args.append_certifi)

    if args.status:
        result = poll_job(api_key, endpoint_id, args.status, verify, args.poll_interval, args.timeout)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        output = result.get("output") or {}
        if output:
            save_archives(output, args.save_archive)
        return

    payload, upload_spec = build_payload(args)
    if args.upload_inputs:
        if upload_spec:
            maybe_attach_upload(payload, upload_spec)
        else:
            print(
                "[!] --upload-inputs was provided but there were no local FASTA paths or directories to upload.",
                file=sys.stderr,
            )
    print(json.dumps({"input": payload}, indent=2, ensure_ascii=False))

    job_id = submit_job(api_key, endpoint_id, payload, verify)
    print(json.dumps({"submitted_job_id": job_id}, ensure_ascii=False))

    if args.do_async:
        return

    result = poll_job(api_key, endpoint_id, job_id, verify, args.poll_interval, args.timeout)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    output = result.get("output") or {}
    if output:
        save_archives(output, args.save_archive)


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
