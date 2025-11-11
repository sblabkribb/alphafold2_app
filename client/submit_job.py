#!/usr/bin/env python3

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
