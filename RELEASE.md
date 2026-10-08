# HireFlo — Release & Rollback Runbook

This document is the authoritative reference for releasing, verifying,
and rolling back HireFlo in production.

---

## Contents

1. [Versioning strategy](#1-versioning-strategy)
2. [Image tagging strategy](#2-image-tagging-strategy)
3. [Release procedure](#3-release-procedure)
4. [Deployment verification](#4-deployment-verification)
5. [Rollback procedure](#5-rollback-procedure)
6. [Emergency rollback (fast path)](#6-emergency-rollback-fast-path)
7. [Post-rollback recovery](#7-post-rollback-recovery)
8. [Release notes format](#8-release-notes-format)
9. [CI/CD quick reference](#9-cicd-quick-reference)

---

## 1. Versioning strategy

HireFlo uses **Semantic Versioning** ([semver.org](https://semver.org)):

```
MAJOR.MINOR.PATCH
```

| Segment | Increment when… |
|---|---|
| MAJOR | Breaking change to the API or user-visible behaviour |
| MINOR | New feature, backward-compatible |
| PATCH | Bug fix or security patch, backward-compatible |

Git tags are the **single source of truth** for versions.  A release tag
must point to a specific commit and must never be moved after creation.

### Tag format

```
v1.0.0
v1.0.1
v1.1.0
v2.0.0
```

### Creating a release tag

```bash
# 1. Make sure you are on the commit you want to release
git log --oneline -5

# 2. Create an annotated tag
git tag -a v1.0.0 -m "Release v1.0.0"

# 3. Push the tag — this triggers the release workflow
git push origin v1.0.0
```

### Development tags (pre-release testing)

Use `v0.x.y` tags to test the release pipeline without a real production
release:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

---

## 2. Image tagging strategy

For every release tag `vMAJOR.MINOR.PATCH`, three GHCR tags are created:

| Tag | Example | Mutable? | Used for |
|---|---|---|---|
| `vMAJOR.MINOR.PATCH` | `v1.0.0` | ❌ No — immutable | Human-readable release reference |
| `MAJOR.MINOR` | `1.0` | ❌ No — set once per minor | Minor-version convenience tag |
| `sha-<short>` | `sha-b43fc5b` | ❌ No — always immutable | Production deploy reference |

For `main` branch pushes (not release tags), two additional tags are updated:

| Tag | Example | Mutable? | Used for |
|---|---|---|---|
| `latest` | `latest` | ✅ Yes — moves on every push | Local dev / convenience |
| `sha-<short>` | `sha-3e879bb` | ❌ No | SHA pin for that commit |

### Immutability rule

> **A released version tag (`v1.0.0`) and its SHA tag (`sha-<commit>`)
> must never be overwritten or reused for a different image.**

Render production deployments always use an immutable tag (`sha-<short>`),
never `:latest` alone.  This guarantees that a rollback always reproduces
the exact image that ran previously.

---

## 3. Release procedure

### Prerequisites

- All feature work merged to `main`.
- `main` CI pipeline green (lint ✅  tests ✅  security scans ✅  Docker ✅).
- GitHub Secrets configured:
  - `RENDER_DEPLOY_HOOK_URL` — Render deploy hook URL.
  - `HIREFLO_PROD_URL` — production URL (e.g. `https://hireflo.onrender.com`).
    Used for post-deploy health verification.  Optional — a warning is emitted
    if unset, but the release proceeds.

### Step-by-step

```bash
# 1. Confirm main is clean and green
git checkout main
git pull origin main
git log --oneline -3

# 2. Run local checks before tagging
source .venv/bin/activate
ruff check .
pytest -v

# 3. Create and push the release tag
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```

GitHub Actions (`release.yml`) automatically:

1. ✅  Runs Ruff
2. ✅  Runs Pytest
3. ✅  Runs Gitleaks (full history)
4. ✅  Runs pip-audit (production deps)
5. ✅  Builds Docker image
6. ✅  Runs Trivy (CRITICAL/HIGH fail)
7. ✅  Runs smoke test (Streamlit health check)
8. ✅  Verifies non-root execution
9. ✅  Pushes `v1.0.0`, `1.0`, `sha-<short>` to GHCR
10. ✅  Triggers Render deploy (immutable SHA tag)
11. ✅  Polls `/_stcore/health` for up to 5 minutes
12. ✅  Creates GitHub Release with release notes

### Monitoring the release

```bash
# Watch the workflow from the terminal
python monitor_ci.py
```

Or go to:

```
https://github.com/Gangumolu-Kalyani/HireFlo/actions/workflows/release.yml
```

### Verifying the release images

```bash
# After the workflow completes, pull and verify:
docker pull ghcr.io/gangumolu-kalyani/hireflo:v1.0.0
docker pull ghcr.io/gangumolu-kalyani/hireflo:sha-<short>

# Run locally to confirm startup
docker run --rm -p 8501:8501 \
  -e APP_ENV=test \
  -e OPENROUTER_API_KEY=dummy \
  ghcr.io/gangumolu-kalyani/hireflo:v1.0.0
```

---

## 4. Deployment verification

### Automated (release.yml)

The release workflow polls `/_stcore/health` for up to 5 minutes after
triggering the Render deploy hook.  A `200` response marks the deploy as
healthy.  The GitHub Release is only created after this step passes.

### Manual verification

```bash
# Set the production URL
PROD_URL="https://hireflo.onrender.com"

# Quick one-shot check
curl -sf "${PROD_URL}/_stcore/health" && echo "✅ healthy" || echo "❌ unhealthy"

# Or use the smoke test script (polls for up to 5 minutes)
python scripts/smoke_test_prod.py "${PROD_URL}"
```

### Interpreting results

| HTTP status | Meaning |
|---|---|
| `200` | Application is running and healthy |
| `503` | Container started but Streamlit not ready — wait |
| `000` | No response — Render is still pulling the image or cold-starting |
| `4xx` | Unexpected — check Render logs |

---

## 5. Rollback procedure

HireFlo uses immutable SHA tags.  Rolling back is re-deploying a
previous known-good image — no rebuild required.

### Step 1 — Identify the previous release

```bash
# List recent GitHub releases
# https://github.com/Gangumolu-Kalyani/HireFlo/releases

# Or list available GHCR tags
# https://github.com/Gangumolu-Kalyani/HireFlo/pkgs/container/hireflo
```

Note the previous version tag (e.g. `v1.0.0`) and its commit SHA.

### Step 2 — Trigger Render to deploy the previous image

#### Option A — Render Dashboard (recommended for non-urgent rollbacks)

1. Go to `https://dashboard.render.com`.
2. Select the **hireflo** service.
3. Click the **Deploys** tab.
4. Find the last known-good deploy.
5. Click **Rollback to this deploy**.

Render re-runs the previous deploy immediately using its stored image reference.

#### Option B — Deploy hook (recommended for scripted rollbacks)

```bash
# Replace <short-sha> with the commit SHA of the version you want
PREV_SHA="b43fc5b"
IMAGE_OWNER="gangumolu-kalyani"

IMG_URL="ghcr.io%2F${IMAGE_OWNER}%2Fhireflo%3Asha-${PREV_SHA}"

# Never echo RENDER_DEPLOY_HOOK_URL — source it from a secret store
curl "${RENDER_DEPLOY_HOOK_URL}&imgURL=${IMG_URL}"
```

#### Option C — Version tag (human-readable)

```bash
# Roll back to the exact v1.0.0 release image
IMG_URL="ghcr.io%2Fgangumolu-kalyani%2Fhireflo%3Av1.0.0"
curl "${RENDER_DEPLOY_HOOK_URL}&imgURL=${IMG_URL}"
```

### Step 3 — Verify health after rollback

```bash
python scripts/smoke_test_prod.py https://hireflo.onrender.com
```

Expected output:

```
✅  Production is healthy  (attempt N, Xs)
```

### Step 4 — Communicate the rollback

Update the relevant GitHub Issue or incident record.  Note:

- Version rolled back from
- Version rolled back to
- Reason
- Time of rollback

---

## 6. Emergency rollback (fast path)

If production is completely down and speed is critical:

```bash
# 1. Get the previous deploy URL from Render Dashboard → Deploys
# 2. Click "Rollback to this deploy" — no CLI required
# 3. Render restarts the service with the previous image in ~30 s
# 4. Monitor: watch the Render service status turn green
# 5. Verify: curl https://hireflo.onrender.com/_stcore/health
```

Render keeps a full deploy history.  You can roll back to any previous
successful deploy without touching GHCR or GitHub.

---

## 7. Post-rollback recovery

After a rollback:

1. **Root-cause the issue** in the broken version.
2. **Fix on a feature branch**, not on `main` directly.
3. **Run `pytest -v` and `ruff check .`** locally.
4. **Open a PR** — the CI pipeline validates the fix.
5. **Merge to `main`** — triggers the standard CI + GHCR + Render deploy.
6. **Tag a new patch version** once confident:
   ```bash
   git tag v1.0.1 && git push origin v1.0.1
   ```
7. **Verify** production is healthy with the new version.

Do **not** rebuild and overwrite an existing release tag.  Create a new
patch version instead.  `v1.0.0` must always point to the same commit.

---

## 8. Release notes format

```markdown
# HireFlo v1.0.0

## Changes

- Summary of what changed in this release.
- Bug fix: description.
- Feature: description.

## DevOps

- Docker image published to GHCR:
  - `ghcr.io/gangumolu-kalyani/hireflo:v1.0.0`
  - `ghcr.io/gangumolu-kalyani/hireflo:sha-<short>`
- Render deployment verified (/_stcore/health → 200)

## Security

| Check | Tool | Result |
|---|---|---|
| Lint | Ruff | ✅ Passed |
| Tests | Pytest | ✅ Passed |
| Secret scan | Gitleaks | ✅ Passed |
| Dependency scan | pip-audit | ✅ Passed |
| Image scan | Trivy | ✅ Passed |
| Smoke test | Streamlit health | ✅ Passed |

## Deployment

Image: `ghcr.io/gangumolu-kalyani/hireflo:v1.0.0`
Commit: `<full-sha>`
Deployed: <ISO timestamp>
```

---

## 9. CI/CD quick reference

| Event | Workflow | Triggers | Deploys? | Creates Release? |
|---|---|---|---|---|
| Push to `main` | `ci.yml` | lint, test, security, Docker, GHCR push | ✅ SHA tag | ❌ No |
| Pull request | `ci.yml` | lint, test, security, Docker build | ❌ No | ❌ No |
| `v*.*.*` tag | `release.yml` | full pipeline + GHCR push + health verify | ✅ SHA tag | ✅ Yes |

### Secrets required

| Secret | Used in | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | both workflows | GHCR auth (auto-provided) |
| `RENDER_DEPLOY_HOOK_URL` | `ci.yml`, `release.yml` | Trigger Render deploy |
| `HIREFLO_PROD_URL` | `release.yml` | Post-deploy health poll |

### Useful commands

```bash
# Local development
source .venv/bin/activate
ruff check .
pytest -v

# Local Docker
docker build -t hireflo:local .
docker run --rm -p 8501:8501 -e APP_ENV=test -e OPENROUTER_API_KEY=dummy hireflo:local

# Production smoke test
python scripts/smoke_test_prod.py https://hireflo.onrender.com

# Monitor CI
python monitor_ci.py

# Create a release
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
```
