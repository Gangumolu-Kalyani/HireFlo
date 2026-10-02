#!/usr/bin/env python3
import urllib.request
import json

repo = "Gangumolu-Kalyani/HireFlo"
run_id = 37029406468  # the failing run

def api(path):
    url = f"https://api.github.com/repos/{repo}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "hireflo-monitor"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

jobs = api(f"actions/runs/{run_id}/jobs")["jobs"]
for job in jobs:
    if job["conclusion"] == "failure":
        print(f"=== FAILED JOB: {job['name']} (id={job['id']}) ===")
        for step in job["steps"]:
            status = step.get("conclusion", step.get("status"))
            print(f"  Step: {step['name']:50s}  {status}")
        print()
        # Try to get logs URL
        print(f"Logs URL: https://github.com/{repo}/actions/runs/{run_id}/job/{job['id']}")
