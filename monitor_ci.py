#!/usr/bin/env python3
import urllib.request
import json
import sys
import time

repo = "Gangumolu-Kalyani/HireFlo"
target_sha = "3e879bb"

def get_runs():
    url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=5"
    req = urllib.request.Request(url, headers={"User-Agent": "hireflo-monitor"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["workflow_runs"]

def get_jobs(run_id):
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs"
    req = urllib.request.Request(url, headers={"User-Agent": "hireflo-monitor"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["jobs"]

print(f"Polling GitHub Actions for commit {target_sha}...")
for attempt in range(30):
    try:
        runs = get_runs()
        matching = [r for r in runs if r["head_sha"].startswith(target_sha)]
        if not matching:
            print(f"  [{attempt+1}/30] No run found yet for {target_sha}, waiting 20s...")
            time.sleep(20)
            continue
        run = matching[0]
        run_id = run["id"]
        status = run["status"]
        conclusion = run["conclusion"]
        print(f"  [{attempt+1}/30] Run {run_id}: status={status} conclusion={conclusion}")
        if status in ("completed",):
            jobs = get_jobs(run_id)
            print(f"\nRun completed with conclusion: {conclusion}")
            for j in jobs:
                print(f"  Job: {j['name']:30s}  status={j['status']:12s}  conclusion={j['conclusion']}")
            sys.exit(0 if conclusion == "success" else 1)
        elif status in ("queued", "in_progress", "waiting", "requested", "pending"):
            jobs = get_jobs(run_id)
            for j in jobs:
                print(f"    Job: {j['name']:30s}  status={j['status']:12s}  conclusion={j['conclusion']}")
            time.sleep(20)
        else:
            print(f"  Unknown status: {status}")
            time.sleep(20)
    except Exception as e:
        print(f"  API error: {e}")
        time.sleep(20)

print("Timeout waiting for run to complete")
sys.exit(1)
