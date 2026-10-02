#!/usr/bin/env python3
import urllib.request
import json

repo = "Gangumolu-Kalyani/HireFlo"
job_id = 110912152608

url = f"https://api.github.com/repos/{repo}/check-runs/{job_id}/annotations"
req = urllib.request.Request(url, headers={
    "User-Agent": "hireflo-monitor",
    "Accept": "application/vnd.github.v3+json",
})
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        annotations = json.loads(r.read())
        print(f"Annotations count: {len(annotations)}")
        for a in annotations:
            print(f"  [{a.get('annotation_level')}] {a.get('path')}:{a.get('start_line')}")
            print(f"  Message: {a.get('message','')[:300]}")
            print()
except Exception as e:
    print(f"Error: {e}")

# Also check the run's check suite
url2 = f"https://api.github.com/repos/{repo}/actions/runs/37029406468"
req2 = urllib.request.Request(url2, headers={
    "User-Agent": "hireflo-monitor",
    "Accept": "application/vnd.github.v3+json",
})
with urllib.request.urlopen(req2, timeout=10) as r:
    run = json.loads(r.read())
    print(f"\nRun title: {run.get('display_title')}")
    print(f"Run URL: {run.get('html_url')}")
    print(f"Conclusion: {run.get('conclusion')}")
    print(f"Head SHA: {run.get('head_sha')}")
    print(f"Trigger: {run.get('event')}")
