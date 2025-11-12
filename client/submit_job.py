#!/usr/bin/env python3
#!/usr/bin/env python3

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any
import tempfile

import requests
import certifi


def _read_sequence_from_fasta(fasta_path: Path) -> str:
    if not fasta_path.is_file():
        raise FileNotFoundError(f"FASTA not found: {fasta_path}")
    seq_lines = []
    with fasta_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith(">"):
                continue
            seq_lines.append(line)
    if not seq_lines:
        raise ValueError("No sequence content found in FASTA file.")
    return "".join(seq_lines)


def _combine_with_certifi(user_ca_path: str) -> str:
    """Create a temp CA bundle that is user CA + certifi bundle."""
    tmp = tempfile.NamedTemporaryFile(prefix="cafile-", suffix=".pem", delete=False)
    with open(user_ca_path, "rb") as f_in, open(certifi.where(), "rb") as f_cert, open(tmp.name, "wb") as f_out:
        f_out.write(f_in.read())
        f_out.write(b"\n")
        f_out.write(f_cert.read())
    return tmp.name


def _resolve_verify(ca_bundle_arg: Optional[str], append_certifi: bool = False) -> Any:
    if ca_bundle_arg:
        return _combine_with_certifi(ca_bundle_arg) if append_certifi else ca_bundle_arg
    # Respect common envs for corp proxies
    for key in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        if os.environ.get(key):
            path = os.environ[key]
            return _combine_with_certifi(path) if append_certifi else path
    return True  # default certifi bundle


def submit_job(api_key: str, endpoint_id: str, payload: Dict[str, Any], verify: Any) -> str:
    url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json={"input": payload}, timeout=60, verify=verify)
    resp.raise_for_status()
    data = resp.json()
    job_id = data.get("id") or data.get("jobId")
    if not job_id:
        raise RuntimeError(f"Unexpected submit response: {json.dumps(data)[:500]}")
    return job_id


def poll_job(api_key: str, endpoint_id: str, job_id: str, verify: Any, interval: int = 5, timeout: int = 3600) -> Dict[str, Any]:
    url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    start = time.time()
    while True:
        resp = requests.get(url, headers=headers, timeout=30, verify=verify)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status") or data.get("state")
        if status in {"COMPLETED", "COMPLETED_WITH_ERRORS"}:
            return data
        if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
            return data
        if time.time() - start > timeout:
            raise TimeoutError(f"Polling timed out after {timeout}s. Last: {json.dumps(data)[:500]}")
        time.sleep(interval)


def build_payload_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if args.sequence_file:
        payload["sequence"] = _read_sequence_from_fasta(Path(args.sequence_file))
    elif args.sequence:
        payload["sequence"] = args.sequence.strip()
    elif args.fasta_url:
        payload["fasta_url"] = args.fasta_url
    else:
        raise SystemExit("Provide --sequence-file, --sequence or --fasta-url")

    if args.model_preset:
        payload["model_preset"] = args.model_preset
    if args.db_preset:
        payload["db_preset"] = args.db_preset
    if args.max_template_date:
        payload["max_template_date"] = args.max_template_date
    if args.extra_flags:
        payload["alphafold_extra_flags"] = args.extra_flags
    return payload


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Submit AlphaFold2 job to Runpod endpoint")
    p.add_argument("--sequence-file", help="Path to FASTA file to read and send as raw sequence")
    p.add_argument("--sequence", help="Raw amino acid sequence string")
    p.add_argument("--fasta-url", help="Public URL to a FASTA file")
    p.add_argument("--endpoint", default=os.environ.get("RUNPOD_ENDPOINT_ID"), help="Runpod endpoint ID (or set RUNPOD_ENDPOINT_ID)")
    p.add_argument("--api-key", default=os.environ.get("RUNPOD_API_KEY"), help="Runpod API key (or set RUNPOD_API_KEY)")
    p.add_argument("--ca-bundle", help="Custom CA bundle path for TLS inspection environments")
    p.add_argument("--model-preset", default="monomer", help="Alphafold model preset")
    p.add_argument("--db-preset", default="full_dbs", help="Database preset")
    p.add_argument("--max-template-date", help="Max template release date, e.g., 2023-12-01")
    p.add_argument("--extra-flags", help="Extra flags string to pass to Alphafold runner")
    p.add_argument("--async", dest="do_async", action="store_true", help="Submit and print job id without polling")
    p.add_argument("--status", help="Poll status of an existing job id and print the final result JSON")
    p.add_argument("--append-certifi", action="store_true", help="Append certifi bundle to the provided CA bundle for proxy chains")
    return p.parse_args()


def main():
    args = parse_args()
    if not args.api_key:
        raise SystemExit("RUNPOD_API_KEY not set. Use --api-key or export env.")
    if not args.endpoint:
        raise SystemExit("RUNPOD_ENDPOINT_ID not set. Use --endpoint or export env.")

    verify = _resolve_verify(args.ca_bundle, append_certifi=args.append_certifi)

    # Poll only mode
    if args.status:
        result = poll_job(args.api_key, args.endpoint, args.status, verify)
        print(json.dumps(result, ensure_ascii=False))
        return

    payload = build_payload_from_args(args)
    job_id = submit_job(args.api_key, args.endpoint, payload, verify)
    print(json.dumps({"submitted_job_id": job_id}, ensure_ascii=False))

    if args.do_async:
        return

    result = poll_job(args.api_key, args.endpoint, job_id, verify)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

import requests


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if args.sequence:
        payload["sequence"] = args.sequence
    elif args.sequence_file:
        fasta_text = Path(args.sequence_file).read_text(encoding="utf-8")
        sequence_lines = [
            line.strip()
            for line in fasta_text.splitlines()
            if line and not line.startswith(">")
        ]
        if not sequence_lines:
            raise SystemExit(f"No sequence content found in {args.sequence_file}")
        payload["sequence"] = "".join(sequence_lines)
    elif args.fasta_url:
        payload["fasta_url"] = args.fasta_url
    else:
        raise SystemExit("Either --sequence, --sequence-file, or --fasta-url must be provided.")

    if args.model_preset:
        payload["model_preset"] = args.model_preset
    if args.db_preset:
        payload["db_preset"] = args.db_preset
    if args.max_template_date:
        payload["max_template_date"] = args.max_template_date
    if args.extra_flags:
        payload["alphafold_extra_flags"] = args.extra_flags
    return payload


def submit_job(api_key: str, endpoint_id: str, payload: Dict[str, Any]) -> str:
    url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    response = requests.post(url, headers=headers, json={"input": payload}, timeout=30)
    response.raise_for_status()
    task_id = response.json()["id"]
    print(f"[+] Submitted job: {task_id}")
    return task_id


def poll_job(api_key: str, endpoint_id: str, task_id: str, interval: int = 10) -> Dict[str, Any]:
    url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{task_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    while True:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        print(f"[poll] status={status}")
        if status == "COMPLETED":
            return payload
        if status in {"FAILED", "CANCELLED"}:
            raise RuntimeError(f"Job {task_id} ended with status {status}: {json.dumps(payload)}")
        time.sleep(interval)


def save_archive(output: Dict[str, Any], destination: Path) -> None:
    archive_b64 = output.get("archive_base64")
    if not archive_b64:
        print("[!] No archive provided in response, skipping download.")
        return
    binary = base64.b64decode(archive_b64)
    destination.write_bytes(binary)
    print(f"[+] Saved archive to {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit Alphafold job to RunPod Serverless endpoint.")
    parser.add_argument("--sequence", help="FASTA sequence content.")
    parser.add_argument("--sequence-file", help="Path to FASTA file to submit.")
    parser.add_argument("--fasta-url", help="URL to FASTA file.")
    parser.add_argument("--model-preset", default="monomer", help="Alphafold model preset.")
    parser.add_argument("--db-preset", default="full_dbs", help="Database preset.")
    parser.add_argument("--max-template-date", default="2020-05-14", help="Maximum template release date.")
    parser.add_argument("--extra-flags", help="Additional flags passed to Alphafold.")
    parser.add_argument("--poll-interval", type=int, default=10, help="Polling interval in seconds.")
    parser.add_argument("--save-archive", type=Path, default=Path("alphafold_results.tar.gz"), help="Output archive path.")
    args = parser.parse_args()

    api_key = os.environ.get("RUNPOD_API_KEY")
    endpoint_id = os.environ.get("RUNPOD_ENDPOINT_ID")
    if not api_key or not endpoint_id:
        raise SystemExit("RUNPOD_API_KEY and RUNPOD_ENDPOINT_ID environment variables are required.")

    payload = build_payload(args)
    print(json.dumps({"input": payload}, indent=2))

    task_id = submit_job(api_key, endpoint_id, payload)
    status_payload = poll_job(api_key, endpoint_id, task_id, args.poll_interval)

    output = status_payload.get("output") or {}
    print(json.dumps(output, indent=2))

    save_archive(output, args.save_archive)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
