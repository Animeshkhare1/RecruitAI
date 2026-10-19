"""
schemas.py - shared data contracts for RecruitAI (owned by User 1).

Both users import from this file so everybody uses the same keys, statuses,
task names and scoring rules. Nothing here runs code or holds state: it only
contains documented constants.

NOTE FOR VIVA: the *job requirements* come from a real Amazon posting
(see job_research.md). The *numeric weights* are our own prototype design.
They are NOT Amazon's hiring criteria.

--------------------------------------------------------------------------
1. JOB CONFIGURATION (dict)  - built in dataset.get_sample_job()
--------------------------------------------------------------------------
{
  "job_id": str,                      # e.g. "JOB001"
  "company": str,                     # real company, from the posting
  "job_title": str,                   # exact title, from the posting
  "source_url": str,                  # official posting URL
  "access_date": str,                 # date the posting was opened (YYYY-MM-DD)
  "required_skills": [str, ...],      # normalized names of explicit requirements
  "preferred_skills": [str, ...],     # reported only, NEVER scored
  "minimum_experience_years": number, # stated in posting, reported only, NOT scored
  "education_requirements": [str, ...],  # empty list = posting states none
  "skill_weights": {skill: points, ..., "relevant_project": points},  # sum to 100
  "skill_equivalents": {criterion: [skill, ...]}   # OPTIONAL extension (see below)
}

"skill_equivalents" is an optional, additive key. Some posting lines say
"one or more scripting languages (e.g. Python, KornShell)". Instead of
pretending the employer demanded Python only, the criterion
"Scripting Language" is satisfied by any listed example. If the key is absent,
it is treated as {} and every criterion must match by its own name.

--------------------------------------------------------------------------
2. CANDIDATE INPUT (dict)  - synthetic data only
--------------------------------------------------------------------------
{
  "candidate_id": str, "name": str, "education": str,
  "skills": [str, ...], "experience_years": number,
  "projects": [str, ...], "certifications": [str, ...]
}

--------------------------------------------------------------------------
3. PARSED CANDIDATE (dict)  - returned by parse_candidate()
--------------------------------------------------------------------------
{
  "candidate_id": str | None, "name": str | None, "education": str | None,
  "skills": [canonical skill, ...], "experience_years": number | None,
  "projects": [str, ...], "certifications": [str, ...],
  "missing_fields": [...],            # REQUIRED fields absent or empty
  "parsing_status": "success" | "partial" | "failed",
  # extra keys (additive; User 2 may ignore them):
  "missing_optional_fields": [...],   # OPTIONAL fields absent or empty
  "invalid_fields": [...],            # fields present but with a bad value
  "parsing_notes": [str, ...]         # human-readable messages
}

--------------------------------------------------------------------------
4. MATCH RESULT (dict)  - returned by match_candidate()
--------------------------------------------------------------------------
{
  "candidate_id": str | None, "job_id": str | None,
  "matched_required_skills": [...], "missing_required_skills": [...],
  "matched_preferred_skills": [...],
  "criterion_scores": {criterion: points, ...},
  "match_score": number (0-100) | None,   # None when no score could be computed
  "explanation": str,
  "matching_status": "success" | "partial" | "skipped" | "failed"
}

--------------------------------------------------------------------------
5. PLAN (dict)  - returned by plan_workflow()
--------------------------------------------------------------------------
{
  "plan_id": str, "status": "ready" | "invalid_input",
  "tasks": [task name, ...],           # empty when status != "ready"
  "candidate_count": int, "errors": [...],
  # extra keys (additive):
  "warnings": [...], "job_id": str | None, "candidate_ids": [...],
  "task_functions": {task name: function name}
}
"""

# ---------------------------------------------------------------- scoring ---
MAX_SCORE = 100                     # the weights must add up to this
PROJECT_CRITERION = "relevant_project"   # key used in skill_weights / criterion_scores
MAX_EXPERIENCE_YEARS = 60           # anything above is treated as invalid input

# ----------------------------------------------------------- job fields -----
JOB_TEXT_FIELDS = ["job_id", "company", "job_title", "source_url", "access_date"]
JOB_LIST_FIELDS = ["required_skills", "preferred_skills", "education_requirements"]

# -------------------------------------------------------- candidate fields --
# Required = the matcher cannot score properly without it.
# Optional = nice to have; missing optional data is reported but is not an error.
# (Education is optional because the researched posting states no education requirement.)
REQUIRED_CANDIDATE_FIELDS = ["candidate_id", "name", "skills", "experience_years"]
OPTIONAL_CANDIDATE_FIELDS = ["education", "projects", "certifications"]

# -------------------------------------------------------------- statuses ----
PLAN_READY = "ready"
PLAN_INVALID = "invalid_input"

PARSE_SUCCESS = "success"
PARSE_PARTIAL = "partial"     # identifiable, but some required data missing/invalid
PARSE_FAILED = "failed"       # not identifiable (no candidate_id, or not a dict)

MATCH_SUCCESS = "success"
MATCH_PARTIAL = "partial"     # scored, but from an incomplete record
MATCH_SKIPPED = "skipped"     # candidate could not be parsed -> no score
MATCH_FAILED = "failed"       # the job configuration is invalid -> no score

# ------------------------------------------------------------ workflow ------
# The Planner returns these task names; User 2's workflow runs them in order.
WORKFLOW_TASKS = [
    "parse_candidates",
    "match_candidates",
    "audit_candidates",
    "validate_results",
    "generate_report",
]

TASK_TO_FUNCTION = {
    "parse_candidates": "parse_candidate",
    "match_candidates": "match_candidate",
    "audit_candidates": "audit_candidate",
    "validate_results": "validate_decision",
    "generate_report": "generate_report",
}

# ------------------------------------------------- skill normalization -------
# canonical name -> accepted spellings (all lowercase).
# Only skills that appear in the researched Amazon posting are listed.
# Unknown skills are NOT dropped; they are kept with cleaned spacing.
SKILL_ALIASES = {
    # required
    "SQL": ["sql", "structured query language"],
    "ETL": ["etl", "etl pipeline", "etl pipelines", "extract transform load"],
    "Data Modeling": ["data modeling", "data modelling"],
    "Data Warehousing": ["data warehousing", "data warehouse", "data warehouses"],
    "Python": ["python"],
    "KornShell": ["kornshell", "korn shell", "ksh"],
    # preferred
    "Hadoop": ["hadoop"],
    "Hive": ["hive", "apache hive"],
    "Spark": ["spark", "apache spark"],
    "EMR": ["emr", "amazon emr", "aws emr"],
    "Informatica": ["informatica"],
    "ODI": ["odi", "oracle data integrator"],
    "SSIS": ["ssis"],
    "BODI": ["bodi"],
    "DataStage": ["datastage", "data stage"],
}
