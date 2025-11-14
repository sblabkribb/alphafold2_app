#!/usr/bin/env python3

import argparse
import json
import os
import time
from typing import Any, Dict

import requests


def submit(api_key: str, endpoint: str, payload: Dict[str, Any], verify: Any = True) -> Dict[str, Any]:
    url = f"https://api.runpod.ai/v2/{endpoint}/run"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json={"input": payload}, timeout=60, verify=verify)
    r.raise_for_status()
    return r.json()


def poll(api_key: str, endpoint: str, job_id: str, verify: Any = True, interval: int = 5, timeout: int = 600) -> Dict[str, Any]:
    url = f"https://api.runpod.ai/v2/{endpoint}/status/{job_id}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    start = time.time()
    while True:
        r = requests.get(url, headers=headers, timeout=30, verify=verify)
        r.raise_for_status()
        data = r.json()
        state = data.get("status") or data.get("state")
        if state in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "TIMED_OUT", "CANCELLED"}:
            return data
        if time.time() - start > timeout:
            raise TimeoutError("Polling timed out")
        time.sleep(interval)


def main():
    p = argparse.ArgumentParser(description="Control actions for AlphaFold RunPod endpoint")
    p.add_argument("action", choices=["status", "preload", "stop"], help="Action to perform")
    p.add_argument("--endpoint", default=os.environ.get("RUNPOD_ENDPOINT_ID"), help="RunPod endpoint ID")
    p.add_argument("--api-key", default=os.environ.get("RUNPOD_API_KEY"), help="RunPod API key")
    p.add_argument("--preset", default="reduced_dbs", help="DB preset for preload: reduced_dbs|full_dbs")
    p.add_argument("--verify", help="Path to CA bundle to verify TLS (omit to use default).")
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification (development only)")
    args = p.parse_args()

    if not args.endpoint or not args.api_key:
        raise SystemExit("Set RUNPOD_ENDPOINT_ID and RUNPOD_API_KEY or pass --endpoint/--api-key")

    payload: Dict[str, Any] = {"action": args.action}
    if args.action == "preload":
        payload.update({"preset": args.preset})

    # Resolve verify flag for requests
    verify = True
    if args.insecure:
        verify = False
    elif args.verify:
        verify = args.verify

    sub = submit(args.api_key, args.endpoint, payload, verify=verify)
    job_id = sub.get("id") or sub.get("jobId")
    if not job_id:
        print(json.dumps(sub, indent=2))
        return
    res = poll(args.api_key, args.endpoint, job_id, verify=verify)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
