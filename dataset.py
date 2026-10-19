"""
dataset.py - the job configuration and the synthetic candidates.

REAL DATA:       the job requirements (Amazon "Data Engineer I", Job ID 2829886).
                 Source and access date are stored inside the job dictionary.
PROTOTYPE DATA:  the scoring weights (our own design, NOT Amazon's criteria).
SYNTHETIC DATA:  every candidate below is fictional. No real people, no contact
                 details, and no age/gender/religion/caste/photo/marital status.

Nothing runs when this file is imported.
"""

from __future__ import annotations

from copy import deepcopy

SYNTHETIC_DATA_NOTICE = (
    "SYNTHETIC DEMONSTRATION DATA: all candidate profiles are fictional and were "
    "created only for testing RecruitAI."
)

# Each candidate was designed against the job below. Expected scores with the
# prototype weights (SQL 25, ETL 20, Data Modeling 15, Data Warehousing 15,
# Scripting Language 15, relevant_project 10):
#   A=100  B=75  C=85  D=90  E=50  F=10  G=65  H=60
_JOB = {
    "job_id": "JOB001",
    "company": "Amazon",                       # Amazon.com Services LLC
    "job_title": "Data Engineer I",            # exact title on the posting
    "source_url": "https://amazon.jobs/en/jobs/2829886/data-engineer-i",
    "access_date": "2026-10-14",
    # Explicit employer requirements, written as normalized skill names:
    "required_skills": [
        "SQL",
        "ETL",
        "Data Modeling",
        "Data Warehousing",
        "Scripting Language",
    ],
    "preferred_skills": ["Hadoop", "Hive", "Spark", "EMR", "ETL Tool"],
    # "1+ years of data engineering experience" (reported only, not scored)
    "minimum_experience_years": 1,
    # The posting states no education requirement.
    "education_requirements": [],
    # PROTOTYPE-DEFINED weights (not published by Amazon). They sum to 100.
    "skill_weights": {
        "SQL": 25,
        "ETL": 20,
        "Data Modeling": 15,
        "Data Warehousing": 15,
        "Scripting Language": 15,
        "relevant_project": 10,
    },
    # The posting lists examples ("e.g., Python, KornShell") for these lines.
    "skill_equivalents": {
        "Scripting Language": ["Python", "KornShell"],
        "ETL Tool": ["Informatica", "ODI", "SSIS", "BODI", "DataStage"],
    },
}

_CANDIDATES = [
    # A - matches every configured criterion (spellings are deliberately varied
    #     to show normalization: "PYTHON", "data warehouse", "Data Modelling").
    {
        "candidate_id": "C001",
        "name": "Synthetic Candidate A",
        "education": "B.Tech",
        "skills": ["sql", "PYTHON", "ETL", "Data Modelling", "data warehouse", "Apache Spark"],
        "experience_years": 2,
        "projects": ["Retail Data Warehouse with ETL Pipelines using Python and SQL"],
        "certifications": ["Cloud Fundamentals Certificate"],
    },
    # B - missing one required skill (SQL)
    {
        "candidate_id": "C002",
        "name": "Synthetic Candidate B",
        "education": "B.Sc",
        "skills": ["Python", "ETL", "Data Modeling", "Data Warehousing"],
        "experience_years": 1,
        "projects": ["Sales ETL pipeline built in Python"],
        "certifications": [],
    },
    # C - missing a different required skill (Data Warehousing)
    {
        "candidate_id": "C003",
        "name": "Synthetic Candidate C",
        "education": "B.Tech",
        "skills": ["SQL", "ETL", "Python", "Data Modeling"],
        "experience_years": 1.5,
        "projects": ["Customer Analytics ETL using SQL and Python"],
        "certifications": [],
    },
    # D - all required skills, but no relevant project
    {
        "candidate_id": "C004",
        "name": "Synthetic Candidate D",
        "education": "M.Sc",
        "skills": ["SQL", "ETL", "Python", "Data Modeling", "Data Warehousing"],
        "experience_years": 3,
        "projects": ["Campus Fest Poster Design", "Library Book Inventory Spreadsheet"],
        "certifications": [],
    },
    # E - preferred (big-data) skills, but missing ETL, Data Modeling, Data Warehousing
    {
        "candidate_id": "C005",
        "name": "Synthetic Candidate E",
        "education": "B.Tech",
        "skills": ["SQL", "Python", "Hadoop", "Hive", "Spark", "EMR"],
        "experience_years": 1,
        "projects": ["Spark Log Analysis on EMR using Python"],
        "certifications": [],
    },
    # F - incomplete information: no "skills" key and an invalid experience value
    {
        "candidate_id": "C006",
        "name": "Synthetic Candidate F",
        "education": "B.Sc",
        "experience_years": "about two",
        "projects": ["Student ETL Pipeline Project"],
        "certifications": [],
    },
    # G - several matching skills but limited experience (below the stated 1+ years)
    {
        "candidate_id": "C007",
        "name": "Synthetic Candidate G",
        "education": "B.Tech",
        "skills": ["SQL", "Python", "Data Modeling"],
        "experience_years": 0.5,
        "projects": ["Student Marks Database Design and Data Modeling in SQL"],
        "certifications": [],
    },
    # H - a different combination: KornShell scripting + Informatica (an ETL tool)
    {
        "candidate_id": "C008",
        "name": "Synthetic Candidate H",
        "education": "B.Sc",
        "skills": ["etl", "Data Warehouse", "Korn Shell", "Informatica"],
        "experience_years": 2,
        "projects": ["Nightly ETL job scheduled with Korn shell scripts"],
        "certifications": [],
    },
]


def get_sample_job() -> dict:
    """Return a fresh copy of the job configuration (safe to modify in tests)."""
    return deepcopy(_JOB)


def get_sample_candidates() -> list:
    """Return fresh copies of the 8 synthetic candidates (safe to modify)."""
    return deepcopy(_CANDIDATES)
