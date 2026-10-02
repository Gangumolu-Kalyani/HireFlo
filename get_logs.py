#!/usr/bin/env python3
"""Download raw logs for a GitHub Actions job and grep for failure context."""
import urllib.request
import urllib.error

repo = "Gangumolu-Kalyani/HireFlo"
job_id = 110912152608

url = f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs"
req = urllib.request.Request(
    url,
    headers={
        "User-Agent": "hireflo-monitor",
        "Accept": "application/vnd.github.v3+json",
    }
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        content = r.read().decode("utf-8", errors="replace")
        # Print last 100 lines which usually contain the failure
        lines = content.splitlines()
        print(f"Total log lines: {len(lines)}")
        print("=== Last 80 lines ===")
        for line in lines[-80:]:
            print(line)
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.reason}")
    # GitHub redirects log downloads — follow the redirect
    if e.code == 302:
        location = e.headers.get("Location")
        print(f"Redirect to: {location}")
        req2 = urllib.request.Request(location, headers={"User-Agent": "hireflo-monitor"})
        with urllib.request.urlopen(req2, timeout=30) as r2:
            content = r2.read().decode("utf-8", errors="replace")
            lines = content.splitlines()
            print(f"Total log lines: {len(lines)}")
            print("=== Last 80 lines ===")
            for line in lines[-80:]:
                print(line)
