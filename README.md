# HireFlo — AI Recruitment Agent

An AI-powered recruitment agent (LangGraph + OpenRouter) that parses a job description and resumes,
scores candidates against a JD-based rubric with evidence, ranks them, and proposes interviews —
**always behind a human approval gate**.

> Status: **Phase 2 — basic recruitment agent building blocks.** No autonomous agent loop yet.

## What Phase 2 implements

- **Pydantic models** (`app/models/`): `JobDescription`, `CandidateProfile`, `ScoringCriterion`,
  `ScoreEvidence` (score 0–5, evidence required), `CandidateScore`.
- **LLM service** (`app/services/llm_service.py`): one small interface,
  `structured_call(system_prompt, user_content, schema)`, that talks to OpenRouter via LangChain's
  `ChatOpenAI` and returns a validated Pydantic object. The API key comes only from settings and is
  never printed.
- **Resume parser** (`parse_resume`) and **job description parser** (`parse_job_description`).
  Both treat input as **untrusted data**:
  - the text is wrapped in `<resume>…</resume>` / `<job_description>…</job_description>`
    delimiters and any fake closing tags inside it are escaped;
  - the system prompt tells the model never to follow instructions found inside the data;
  - empty or oversized (> 50,000 chars) input is rejected before any LLM call.

```
Resume text ──► resume_parser ──► LLMService ──► CandidateProfile
JD text     ──► job_parser    ──► LLMService ──► JobDescription
```

Business logic never calls LangChain directly — it goes through `LLMService`, so tests can swap in
a fake and LangGraph nodes can reuse it later.

## Prerequisites

- Windows with WSL2 (Ubuntu) — all commands below run **inside WSL**
- Python 3.11+ (`python3 --version`)
- `python3-venv` (`sudo apt install python3.12-venv`)
- Docker Desktop with WSL integration enabled (needed from Phase 8)

## Setup

```bash
# 1. Open an Ubuntu (WSL) terminal and go to the project
cd /mnt/c/HireFlo

# 2. Create (first time only) and activate the virtual environment
python3 -m venv .venv
source .venv/bin/activate          # your prompt now starts with (.venv); `deactivate` to leave

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
nano .env                          # set OPENROUTER_API_KEY=sk-or-...
```

`.env` is gitignored — never commit it. Get a key at https://openrouter.ai/keys.

## Running tests and lint

```bash
pytest -v          # all tests use a fake LLM: no API key or internet needed
ruff check .       # lint
```

## Trying the parsers against the real LLM

Requires `OPENROUTER_API_KEY` in `.env` (this makes real, billed API calls):

```bash
python -m app.services.job_parser tests/data/sample_job.txt
python -m app.services.resume_parser tests/data/sample_resume.txt
```

Or from Python:

```python
from app.services.resume_parser import parse_resume
profile = parse_resume(open("tests/data/sample_resume.txt").read())
print(profile.model_dump_json(indent=2))
```

## Project structure

```
app/
  config.py              settings from env / .env
  models/                Pydantic models (job, candidate, scoring)
  services/              llm_service, resume_parser, job_parser
tests/
  data/                  fictional sample JD and resume
  conftest.py            FakeLLMService + fixtures
  test_*.py
.env.example             environment variable template
requirements.txt
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `development` / `test` / `production` |
| `LOG_LEVEL` | `INFO` | Logging level |
| `OPENROUTER_API_KEY` | — | OpenRouter API key (required for real LLM calls) |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter endpoint |
| `LLM_MODEL` | `anthropic/claude-sonnet-5.5` | Model ID on OpenRouter (must support tool calling) |
| `AGENT_MAX_ITERATIONS` | `10` | Hard cap on agent loop steps (1–50) |

## Roadmap

1. ✅ Project foundation
2. ✅ Basic recruitment agent building blocks
3. Agent tools
4. LangGraph state and workflow
5. Guardrails and human approval
6. Streamlit UI
7. Tests
8. Docker
9. GitHub Actions CI
10. Container registry
11. CD / deployment
12. Monitoring / observability
