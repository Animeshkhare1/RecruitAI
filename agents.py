"""
agents.py - User 1's three agents for RecruitAI.

    plan_workflow(job, candidates)        Planner Agent
    parse_candidate(candidate)            Resume Parser Agent
    match_candidate(parsed_candidate, job)  Job Matcher Agent

HONEST DESCRIPTION: all three are plain deterministic Python functions.
No LLM, no internet, no database, no Streamlit. Same input -> same output.
The parser reads structured dictionaries; it does NOT read PDF/DOCX resumes.
Nothing here hires, rejects or shortlists anyone.

Importing this file runs nothing (the demo at the bottom needs `python agents.py`).
"""

from __future__ import annotations

import math
import re

from schemas import (
    JOB_LIST_FIELDS,
    JOB_TEXT_FIELDS,
    MATCH_FAILED,
    MATCH_PARTIAL,
    MATCH_SKIPPED,
    MATCH_SUCCESS,
    MAX_EXPERIENCE_YEARS,
    MAX_SCORE,
    OPTIONAL_CANDIDATE_FIELDS,
    PARSE_FAILED,
    PARSE_PARTIAL,
    PARSE_SUCCESS,
    PLAN_INVALID,
    PLAN_READY,
    PROJECT_CRITERION,
    REQUIRED_CANDIDATE_FIELDS,
    SKILL_ALIASES,
    TASK_TO_FUNCTION,
    WORKFLOW_TASKS,
)

# spelling -> canonical name. Built once from the constant SKILL_ALIASES and never changed.
_ALIAS_LOOKUP = {
    spelling: canonical
    for canonical, spellings in SKILL_ALIASES.items()
    for spelling in spellings
}


# =========================================================================
# Small helpers (shared by the three agents)
# =========================================================================
def _is_number(value) -> bool:
    """True for real, finite numbers. Booleans are NOT numbers here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_text_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def _fmt(number) -> str:
    """40.0 -> '40', 12.5 -> '12.5' (keeps explanations tidy)."""
    return str(int(number)) if float(number).is_integer() else str(number)


def normalize_skill(raw) -> str | None:
    """
    Turn a raw skill string into its canonical name.
      "python" -> "Python", "data modelling" -> "Data Modeling", "Korn Shell" -> "KornShell"
    Unknown skills are kept (with cleaned spaces), never invented or dropped.
    Returns None if the value is not a usable string.
    """
    if not isinstance(raw, str):
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip())
    if not cleaned:
        return None
    key = cleaned.lower()
    for candidate_key in (key, key.replace("-", " ").replace("_", " ")):
        if candidate_key in _ALIAS_LOOKUP:
            return _ALIAS_LOOKUP[candidate_key]
    return cleaned


def _skill_key(skill) -> str:
    """Lowercase canonical form used only for comparing skills."""
    return (normalize_skill(skill) or "").lower()


def _spellings(skill: str) -> list:
    """All lowercase spellings that count as a mention of this skill."""
    canonical = normalize_skill(skill) or skill
    return SKILL_ALIASES.get(canonical, [canonical.lower()])


def _find_match(criterion: str, candidate_keys: set, equivalents: dict):
    """
    Return the name that satisfied the criterion, or None.
    A criterion is satisfied by its own name or by any listed equivalent.
    """
    for name in [criterion] + list(equivalents.get(criterion, [])):
        if _skill_key(name) in candidate_keys:
            return name
    return None


def _project_is_relevant(projects: list, job: dict) -> bool:
    """
    Documented project rule: a project is relevant if its text mentions any
    required skill (or one of its equivalents) as a whole word, ignoring case.
    """
    equivalents = job.get("skill_equivalents") or {}
    names = []
    for skill in job["required_skills"]:
        names.append(skill)
        names.extend(equivalents.get(skill, []))
    for text in projects:
        if not isinstance(text, str):
            continue
        lowered = text.lower()
        for name in names:
            for spelling in _spellings(name):
                pattern = r"(?<![a-z0-9])" + re.escape(spelling) + r"(?![a-z0-9])"
                if re.search(pattern, lowered):
                    return True
    return False


# =========================================================================
# Job configuration validation (used by the Planner AND the Matcher)
# =========================================================================
def validate_job_config(job) -> list:
    """Return a list of error messages. An empty list means the job config is valid."""
    if not isinstance(job, dict) or not job:
        return ["Job configuration is missing or empty."]

    errors = []

    # Traceable source metadata
    for field in JOB_TEXT_FIELDS:
        value = job.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"Missing or empty job field: '{field}'.")
    url = job.get("source_url")
    if isinstance(url, str) and url.strip() and not url.strip().lower().startswith(("http://", "https://")):
        errors.append("'source_url' must start with http:// or https://.")

    # Skill lists
    required = job.get("required_skills")
    if not _is_text_list(required) or not required:
        errors.append("'required_skills' must be a non-empty list of text values.")
    elif len({_skill_key(s) for s in required}) != len(required):
        errors.append("'required_skills' contains duplicates.")
    if not _is_text_list(job.get("preferred_skills")):
        errors.append("'preferred_skills' must be a list of text values (it may be empty).")
    if not isinstance(job.get("education_requirements"), list):
        errors.append("'education_requirements' must be a list (it may be empty).")

    minimum = job.get("minimum_experience_years")
    if not _is_number(minimum) or minimum < 0:
        errors.append("'minimum_experience_years' must be a number >= 0.")

    equivalents = job.get("skill_equivalents", {})
    if not isinstance(equivalents, dict) or not all(_is_text_list(v) for v in equivalents.values()):
        errors.append("'skill_equivalents' must map each criterion to a list of text values.")

    # Weights
    weights = job.get("skill_weights")
    if not isinstance(weights, dict) or not weights:
        errors.append("'skill_weights' must be a non-empty dictionary.")
        return errors
    bad_values = [k for k, v in weights.items() if not _is_number(v) or v < 0]
    if bad_values:
        errors.append(f"Weights must be numbers >= 0. Bad weights for: {bad_values}.")
    else:
        total = sum(weights.values())
        if abs(total - MAX_SCORE) > 1e-9:
            errors.append(f"Skill weights sum to {_fmt(total)}, but they must sum to {MAX_SCORE}.")

    # Criteria must be compatible with the job
    if _is_text_list(required):
        for skill in required:
            if skill not in weights:
                errors.append(f"Required skill '{skill}' has no weight.")
        allowed = set(required) | {PROJECT_CRITERION}
        for key in weights:
            if key not in allowed:
                errors.append(
                    f"Weight '{key}' is not a required skill of this job "
                    "(preferred skills are reported but never scored)."
                )
    return errors


# =========================================================================
# AGENT 1: PLANNER
# =========================================================================
def plan_workflow(job: dict, candidates: list) -> dict:
    """
    Planner Agent. Checks the inputs and builds an executable task plan.

    If anything is invalid the plan has status "invalid_input", an empty task
    list and a list of errors. The Planner never hires, rejects or shortlists.
    """
    errors = validate_job_config(job)
    warnings = []
    candidate_ids = []

    if not isinstance(candidates, list) or len(candidates) == 0:
        errors.append("No candidates were provided (expected a non-empty list).")
        candidate_count = 0
    else:
        candidate_count = len(candidates)
        seen = set()
        for position, record in enumerate(candidates, start=1):
            cid = record.get("candidate_id") if isinstance(record, dict) else None
            if not isinstance(record, dict):
                warnings.append(f"Candidate #{position} is not a dictionary; the parser will report it as failed.")
            elif cid in (None, ""):
                warnings.append(f"Candidate #{position} has no candidate_id; the parser will report it as failed.")
            elif cid in seen:
                warnings.append(f"Duplicate candidate_id '{cid}' (candidate #{position}).")
            seen.add(cid)
            candidate_ids.append(cid)

    job_id = job.get("job_id") if isinstance(job, dict) else None
    ready = not errors
    return {
        "plan_id": f"PLAN-{job_id or 'UNKNOWN'}-{candidate_count}C",
        "status": PLAN_READY if ready else PLAN_INVALID,
        "tasks": list(WORKFLOW_TASKS) if ready else [],
        "candidate_count": candidate_count,
        "errors": errors,
        "warnings": warnings,
        "job_id": job_id,
        "candidate_ids": candidate_ids,
        "task_functions": dict(TASK_TO_FUNCTION) if ready else {},
    }


# =========================================================================
# AGENT 2: RESUME PARSER
# =========================================================================
def _mark_missing(parsed: dict, field: str) -> None:
    key = "missing_fields" if field in REQUIRED_CANDIDATE_FIELDS else "missing_optional_fields"
    parsed[key].append(field)


def _read_text(candidate: dict, field: str, parsed: dict):
    value = candidate.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        _mark_missing(parsed, field)
        return None
    if not isinstance(value, str):
        parsed["invalid_fields"].append(field)
        return None
    return value.strip()


def _read_text_list(candidate: dict, field: str, parsed: dict, normalizer=None) -> list:
    value = candidate.get(field)
    if value is None:
        _mark_missing(parsed, field)
        return []
    if not isinstance(value, (list, tuple)):
        parsed["invalid_fields"].append(field)
        return []
    cleaned, seen, dropped = [], set(), False
    for item in value:
        if isinstance(item, str) and item.strip():
            text = normalizer(item) if normalizer else item.strip()
            if text.lower() not in seen:
                seen.add(text.lower())
                cleaned.append(text)
        else:
            dropped = True
    if dropped:
        parsed["invalid_fields"].append(field)
    if not cleaned:
        _mark_missing(parsed, field)
    return cleaned


def parse_candidate(candidate: dict) -> dict:
    """
    Resume Parser Agent. Reads one structured candidate dictionary.

    - Normalizes skill names. Never invents missing information.
    - Missing REQUIRED fields go to "missing_fields"; missing OPTIONAL fields go
      to "missing_optional_fields"; bad values go to "invalid_fields".
    - Fields not in the schema (e.g. an "age" key) are ignored on purpose.
    - Never raises an exception for bad candidate data.
    """
    parsed = {
        "candidate_id": None,
        "name": None,
        "education": None,
        "skills": [],
        "experience_years": None,
        "projects": [],
        "certifications": [],
        "missing_fields": [],
        "parsing_status": PARSE_FAILED,
        "missing_optional_fields": [],
        "invalid_fields": [],
        "parsing_notes": [],
    }

    if not isinstance(candidate, dict):
        parsed["missing_fields"] = list(REQUIRED_CANDIDATE_FIELDS)
        parsed["missing_optional_fields"] = list(OPTIONAL_CANDIDATE_FIELDS)
        parsed["parsing_notes"].append("Candidate record is not a dictionary, so nothing could be parsed.")
        return parsed

    # candidate_id (string or integer accepted)
    raw_id = candidate.get("candidate_id")
    if isinstance(raw_id, (str, int)) and not isinstance(raw_id, bool) and str(raw_id).strip():
        parsed["candidate_id"] = str(raw_id).strip()
    else:
        parsed["missing_fields"].append("candidate_id")

    parsed["name"] = _read_text(candidate, "name", parsed)
    parsed["education"] = _read_text(candidate, "education", parsed)
    parsed["skills"] = _read_text_list(candidate, "skills", parsed, normalizer=normalize_skill)
    parsed["projects"] = _read_text_list(candidate, "projects", parsed)
    parsed["certifications"] = _read_text_list(candidate, "certifications", parsed)

    # experience_years: a real number from 0 to MAX_EXPERIENCE_YEARS
    experience = candidate.get("experience_years")
    if experience is None:
        parsed["missing_fields"].append("experience_years")
    elif _is_number(experience) and 0 <= experience <= MAX_EXPERIENCE_YEARS:
        parsed["experience_years"] = experience
    else:
        parsed["invalid_fields"].append("experience_years")

    # status + notes
    if parsed["candidate_id"] is None:
        parsed["parsing_status"] = PARSE_FAILED
        parsed["parsing_notes"].append("candidate_id is missing, so this record cannot be tracked.")
    elif parsed["missing_fields"] or parsed["invalid_fields"]:
        parsed["parsing_status"] = PARSE_PARTIAL
    else:
        parsed["parsing_status"] = PARSE_SUCCESS
    if parsed["missing_fields"]:
        parsed["parsing_notes"].append(f"Missing required fields: {parsed['missing_fields']}.")
    if parsed["invalid_fields"]:
        parsed["parsing_notes"].append(f"Invalid values in fields: {parsed['invalid_fields']}.")
    return parsed


# =========================================================================
# AGENT 3: JOB MATCHER
# =========================================================================
def _empty_match(parsed_candidate, job, status: str, explanation: str) -> dict:
    return {
        "candidate_id": parsed_candidate.get("candidate_id") if isinstance(parsed_candidate, dict) else None,
        "job_id": job.get("job_id") if isinstance(job, dict) else None,
        "matched_required_skills": [],
        "missing_required_skills": [],
        "matched_preferred_skills": [],
        "criterion_scores": {},
        "match_score": None,
        "explanation": explanation,
        "matching_status": status,
    }


def match_candidate(parsed_candidate: dict, job: dict) -> dict:
    """
    Job Matcher Agent. Deterministic, explainable scoring.

    For each required skill: award its configured weight if the candidate has the
    skill (or a listed equivalent), else 0. The project criterion awards its
    weight if any project text mentions a required skill. Preferred skills are
    REPORTED but never scored. Experience is mentioned in the explanation but not scored.
    The score is a screening aid, not a hiring decision and not a performance prediction.
    """
    job_errors = validate_job_config(job)
    if job_errors:
        return _empty_match(
            parsed_candidate, job, MATCH_FAILED,
            "Matching not performed because the job configuration is invalid: " + " ".join(job_errors),
        )
    if not isinstance(parsed_candidate, dict) or parsed_candidate.get("parsing_status") == PARSE_FAILED:
        return _empty_match(
            parsed_candidate, job, MATCH_SKIPPED,
            "Matching skipped because the candidate record could not be parsed. "
            "This says nothing about the candidate's suitability; it needs human review.",
        )

    equivalents = job.get("skill_equivalents") or {}
    weights = job["skill_weights"]
    skills = parsed_candidate.get("skills") or []
    candidate_keys = {_skill_key(s) for s in skills if isinstance(s, str)}

    scores, matched, missing, parts = {}, [], [], []
    for skill in job["required_skills"]:
        hit = _find_match(skill, candidate_keys, equivalents)
        if hit:
            scores[skill] = weights[skill]
            matched.append(skill)
            via = f" (via {hit})" if _skill_key(hit) != _skill_key(skill) else ""
            parts.append(f"{skill} matched{via}: {_fmt(weights[skill])} points.")
        else:
            scores[skill] = 0
            missing.append(skill)
            parts.append(f"{skill} not matched: 0 points.")

    project_weight = weights.get(PROJECT_CRITERION, 0)
    if _project_is_relevant(parsed_candidate.get("projects") or [], job):
        scores[PROJECT_CRITERION] = project_weight
        parts.append(f"Relevant project criterion matched: {_fmt(project_weight)} points.")
    else:
        scores[PROJECT_CRITERION] = 0
        parts.append("Relevant project criterion not matched: 0 points.")

    total = max(0, min(MAX_SCORE, sum(scores.values())))
    parts.append(f"Total: {_fmt(total)}/{MAX_SCORE}.")

    # Reported but NOT scored
    preferred = [s for s in job["preferred_skills"] if _find_match(s, candidate_keys, equivalents)]
    if preferred:
        parts.append(f"Preferred skills found (reported only, not scored): {', '.join(preferred)}.")
    else:
        parts.append("No preferred skills found (preferred skills are never scored).")

    minimum = job["minimum_experience_years"]
    experience = parsed_candidate.get("experience_years")
    if minimum > 0:
        if _is_number(experience):
            relation = "meets" if experience >= minimum else "is below"
            parts.append(
                f"Experience note (not scored): candidate lists {_fmt(experience)} year(s), "
                f"which {relation} the posting's stated {_fmt(minimum)}+ year(s)."
            )
        else:
            parts.append("Experience note (not scored): candidate experience is unavailable.")

    status = MATCH_SUCCESS
    if parsed_candidate.get("parsing_status") == PARSE_PARTIAL:
        status = MATCH_PARTIAL
        problems = list(parsed_candidate.get("missing_fields", [])) + list(parsed_candidate.get("invalid_fields", []))
        parts.append(
            f"Note: the candidate record is incomplete ({', '.join(problems)}). The score reflects only "
            "the information provided and is not evidence that the candidate lacks those qualifications."
        )

    return {
        "candidate_id": parsed_candidate.get("candidate_id"),
        "job_id": job["job_id"],
        "matched_required_skills": matched,
        "missing_required_skills": missing,
        "matched_preferred_skills": preferred,
        "criterion_scores": scores,
        "match_score": total,
        "explanation": " ".join(parts),
        "matching_status": status,
    }


# =========================================================================
# Optional console demo: `python agents.py` (never runs on import)
# =========================================================================
if __name__ == "__main__":
    from dataset import SYNTHETIC_DATA_NOTICE, get_sample_candidates, get_sample_job

    demo_job = get_sample_job()
    demo_candidates = get_sample_candidates()
    demo_plan = plan_workflow(demo_job, demo_candidates)
    print(SYNTHETIC_DATA_NOTICE)
    print(f"Plan {demo_plan['plan_id']}: {demo_plan['status']}, tasks={demo_plan['tasks']}")
    if demo_plan["status"] == PLAN_READY:
        for record in demo_candidates:
            parsed = parse_candidate(record)
            result = match_candidate(parsed, demo_job)
            print(f"{result['candidate_id']}  score={result['match_score']}  "
                  f"parse={parsed['parsing_status']}  match={result['matching_status']}")
