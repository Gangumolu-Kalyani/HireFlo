#!/usr/bin/env python3
"""HireFlo — Production Smoke Test.

Polls the production Streamlit health endpoint and reports success or failure.

Usage
-----
    python scripts/smoke_test_prod.py [URL]

Arguments
---------
URL : str, optional
    Base URL of the production deployment.
    Default: value of the HIREFLO_PROD_URL environment variable.
    Example: https://hireflo.onrender.com

Exit codes
----------
0 — health endpoint returned HTTP 200 within the timeout
1 — health endpoint did not return HTTP 200 within the timeout
2 — bad arguments / configuration error

Design constraints
------------------
- No real candidate data is submitted.
- No LLM calls are made.
- No secrets are printed to stdout.
- Deterministic: same URL → same poll behaviour every run.
- Zero third-party dependencies: only stdlib urllib, os, sys, time, argparse.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request

# ── Configuration ──────────────────────────────────────────────────────────────

# Maximum time to wait for a healthy response (seconds).
_TIMEOUT_SECONDS: int = 300  # 5 minutes — allows for Render cold-start

# Seconds between poll attempts.
_POLL_INTERVAL_SECONDS: int = 10

# HTTP timeout for each individual request (seconds).
_REQUEST_TIMEOUT_SECONDS: int = 15

# Path appended to the base URL for the health check.
_HEALTH_PATH: str = "/_stcore/health"

# Expected HTTP status code for a healthy response.
_EXPECTED_STATUS: int = 200


# ── Main ───────────────────────────────────────────────────────────────────────


def build_url(base_url: str) -> str:
    """Return the full health check URL from a base URL.

    Strips trailing slashes and appends the health path.
    """
    return base_url.rstrip("/") + _HEALTH_PATH


def check_health(url: str) -> tuple[bool, int | None, str]:
    """Make a single HTTP GET to ``url``.

    Returns
    -------
    ok : bool
        True when the response status matches _EXPECTED_STATUS.
    status : int | None
        HTTP status code, or None on connection error.
    detail : str
        Human-readable status string for logging.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "hireflo-smoke-test/1.0"},
        )
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            ok = status == _EXPECTED_STATUS
            return ok, status, f"HTTP {status}"
    except urllib.error.HTTPError as exc:
        return False, exc.code, f"HTTP {exc.code} ({exc.reason})"
    except urllib.error.URLError as exc:
        return False, None, f"Connection error: {exc.reason}"
    except TimeoutError:
        return False, None, "Request timed out"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"Unexpected error: {type(exc).__name__}"


def poll_until_healthy(
    url: str,
    timeout: int = _TIMEOUT_SECONDS,
    interval: int = _POLL_INTERVAL_SECONDS,
) -> bool:
    """Poll ``url`` until a healthy response is received or the timeout expires.

    Parameters
    ----------
    url:
        Full health check URL.
    timeout:
        Maximum seconds to wait.
    interval:
        Seconds between polls.

    Returns
    -------
    True when the endpoint returns HTTP 200 within the timeout.
    """
    deadline = time.monotonic() + timeout
    max_attempts = timeout // interval
    attempt = 0

    print(f"🔍 Polling {url}")
    print(f"   Timeout  : {timeout}s")
    print(f"   Interval : {interval}s")
    print(f"   Max attempts: {max_attempts}")
    print()

    while time.monotonic() < deadline:
        attempt += 1
        ok, status, detail = check_health(url)
        elapsed = int(time.monotonic() - (deadline - timeout))

        print(f"  [{attempt:3d}]  {elapsed:3d}s elapsed  →  {detail}")

        if ok:
            print()
            print(f"✅  Production is healthy  (attempt {attempt}, {elapsed}s)")
            return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        sleep_for = min(interval, remaining)
        time.sleep(sleep_for)

    print()
    print(f"❌  Production health check FAILED after {timeout}s ({attempt} attempts)")
    print(f"   URL: {url}")
    print(f"   Expected: HTTP {_EXPECTED_STATUS}")
    return False


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="HireFlo production smoke test — polls /_stcore/health",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/smoke_test_prod.py https://hireflo.onrender.com
  HIREFLO_PROD_URL=https://hireflo.onrender.com python scripts/smoke_test_prod.py

Environment variables:
  HIREFLO_PROD_URL   Base URL of the production deployment (used when no URL argument given)
        """,
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Base URL of the production deployment (overrides HIREFLO_PROD_URL)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_TIMEOUT_SECONDS,
        help=f"Maximum seconds to wait for a healthy response (default: {_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=_POLL_INTERVAL_SECONDS,
        help=f"Seconds between poll attempts (default: {_POLL_INTERVAL_SECONDS})",
    )
    return parser.parse_args()


def main() -> int:
    """Entry point.  Returns the process exit code."""
    args = parse_args()

    # Resolve base URL: CLI arg > env var.
    base_url: str | None = args.url or os.environ.get("HIREFLO_PROD_URL")

    if not base_url:
        print(
            "❌  No production URL provided.\n"
            "    Pass it as a positional argument or set HIREFLO_PROD_URL.\n"
            "    Example: python scripts/smoke_test_prod.py https://hireflo.onrender.com",
            file=sys.stderr,
        )
        return 2

    # Strip accidental secrets from URL representation.
    # The URL should be a plain HTTPS URL — never contains a key.
    if "key=" in base_url.lower() or "token=" in base_url.lower():
        print(
            "❌  The URL appears to contain a secret parameter.\n"
            "    Pass a clean base URL (e.g. https://hireflo.onrender.com).",
            file=sys.stderr,
        )
        return 2

    health_url = build_url(base_url)
    ok = poll_until_healthy(health_url, timeout=args.timeout, interval=args.interval)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
