"""One-shot evaluation script for Aarav Sharma vs Junior AI/ML Engineer JD."""
import sys

from app.agent.runner import run_recruitment
from app.models import ScoringCriterion

sys.stdout.reconfigure(encoding="utf-8")

RESUME = """Aarav Sharma
Hyderabad, India
aarav.sharma@example.com | +91-90000-12345
GitHub: github.com/aarav-sharma

SUMMARY
Computer Science graduate with strong Python and machine-learning skills and hands-on
experience building AI applications, REST APIs, and Dockerized services. Experienced in
data preprocessing, model evaluation, Git-based development, and LLM integrations.

EDUCATION
B.Tech in Computer Science and Engineering
XYZ Institute of Technology | 2024-2028
CGPA: 8.9/10

TECHNICAL SKILLS
Python, SQL, Git, pandas, NumPy, scikit-learn, FastAPI, REST APIs, Docker,
GitHub Actions, Machine Learning, NLP, LLM APIs, CI/CD.

PROJECTS
AI Candidate Screening System
- Built a Python application for resume parsing and candidate scoring.
- Developed REST endpoints for processing candidate data.
- Used scikit-learn for model experimentation and evaluation.
- Added validation and automated tests.

ML Prediction API
- Developed a FastAPI service exposing a machine-learning model.
- Containerized the application with Docker.
- Added GitHub Actions for linting and automated tests.

EXPERIENCE
AI Engineering Intern | DataWorks Labs | May 2026-Jul 2026
- Cleaned and analyzed datasets using pandas.
- Built baseline ML models and evaluated precision and recall.
- Integrated an LLM API into an internal prototype.
- Collaborated through Git pull requests and code reviews.

ACHIEVEMENTS
- Participated in multiple hackathons.
- Built and presented AI/ML prototypes in team projects."""

JD = """JOB DESCRIPTION - Junior AI/ML Engineer

Company: HireFlo Technologies
Location: Hyderabad / Hybrid
Employment Type: Full-time

ABOUT THE ROLE
We are looking for a Junior AI/ML Engineer to help build intelligent recruitment
and data-driven applications.

RESPONSIBILITIES
- Develop and maintain Python-based AI/ML applications.
- Build data preprocessing and machine-learning pipelines.
- Work with REST APIs and integrate AI/LLM services.
- Analyze model performance and improve reliability.
- Write clean, tested, maintainable Python code.
- Collaborate with software engineers and product teams.
- Document technical work and participate in code reviews.

REQUIRED SKILLS
- Strong Python programming.
- Good understanding of machine learning fundamentals.
- pandas, NumPy, and scikit-learn.
- SQL and REST APIs.
- Git and software development practices.
- Problem-solving and communication skills.

PREFERRED SKILLS
- FastAPI or Flask.
- Docker.
- LLMs or prompt engineering.
- Basic cloud deployment and CI/CD.

EDUCATION
Bachelor's degree in Computer Science, Information Technology, Data Science,
or a related field.

EXPERIENCE
0-2 years of relevant experience. Strong projects and internships are considered.

WHAT WE VALUE
Technical curiosity, responsible AI use, teamwork, clear communication,
and willingness to learn."""

RUBRIC = [
    ScoringCriterion(
        name="Python & ML Skills",
        weight=0.30,
        description="Proficiency in Python, ML libraries (pandas, NumPy, scikit-learn), and ML fundamentals",
    ),
    ScoringCriterion(
        name="API & Backend Development",
        weight=0.20,
        description="Experience with REST APIs, FastAPI or Flask",
    ),
    ScoringCriterion(
        name="DevOps & CI/CD",
        weight=0.10,
        description="Docker, GitHub Actions, CI/CD pipelines",
    ),
    ScoringCriterion(
        name="LLM & AI Integration",
        weight=0.20,
        description="Experience with LLM APIs and AI application development",
    ),
    ScoringCriterion(
        name="Software Engineering Practices",
        weight=0.20,
        description="Git, testing, code reviews, clean code",
    ),
]


def sep(title=""):
    width = 60
    if title:
        print(f"\n{'=' * 3} {title} {'=' * (width - len(title) - 5)}")
    else:
        print("=" * width)


thread_id, state = run_recruitment(
    RESUME,
    RUBRIC,
    job_description=JD,
    interview_week="2026-W41",
)

sep("FINAL STATUS")
print(state["final_status"])

sep("CANDIDATE PROFILE")
profile = state.get("candidate_profile") or {}
print(f"Name   : {profile.get('name')}")
print(f"Email  : {profile.get('email')}")
print(f"Skills : {profile.get('skills')}")
print(f"Exp    : {profile.get('experience_years')} years")

sep("SCORE")
score = state.get("candidate_score") or {}
print(f"Weighted Score : {score.get('weighted_score')}")
print(f"Recommendation : {score.get('recommendation')}")
print()
for ev in score.get("scores") or []:
    print(f"  [{ev.get('score')}/5] {ev.get('criterion')}")
    print(f"         Evidence  : {ev.get('evidence')}")
    print(f"         Reasoning : {ev.get('reasoning')}")
    print()

sep("AVAILABILITY")
avail = state.get("availability") or {}
print(f"Candidate : {avail.get('candidate')}")
print(f"Week      : {avail.get('week')}")
print(f"Slots     : {avail.get('available_slots')}")

sep("INTERVIEW PROPOSAL")
proposal = state.get("interview_proposal") or {}
print(f"Candidate : {proposal.get('candidate')}")
print(f"Slot      : {proposal.get('slot')}")
print(f"Status    : {proposal.get('status')}")

sep("GUARDRAILS")
print(f"Guardrail flags : {state.get('guardrail_flags', [])}")
print(f"Fairness flags  : {state.get('fairness_flags', [])}")

sep("TRAJECTORY")
for entry in state.get("trajectory") or []:
    detail = entry.get("detail", "")
    detail_str = f" | {detail}" if detail else ""
    print(f"  Step {entry.get('step'):>2} | {entry.get('node'):<30} | {entry.get('status')}{detail_str}")

sep()
print(f"Thread ID : {thread_id}")
