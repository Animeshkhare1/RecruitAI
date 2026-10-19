"""
agents_part2.py - User 2's three components for RecruitAI.

    audit_candidate(match_result, parsed_candidate, job)                 Bias Auditor Agent
    validate_decision(audit_result, match_result, parsed_candidate, job) Decision Validator Agent
    generate_report(results)                                             Report Generator

WHY A SEPARATE FILE: agents.py belongs to User 1 (Planner, Parser, Matcher).
Keeping our code here means we never edit each other's files. To merge later,
copy this file's functions into agents.py - nothing else has to change.

HONEST DESCRIPTION: these are plain deterministic Python functions (no LLM, no
internet, no database). They support a human recruiter; they never hire, reject
or shortlist anyone.

IMPORTANT FOR THE VIVA - what the two "checking" agents can and cannot claim:
  * The Bias Auditor runs a SMALL set of fairness-related checks. An audit status of
    "passed" means ONLY that those implemented checks found no issue. It does NOT
    prove the evaluation is fair or free of discrimination.
  * The Decision Validator does NOT trust the Matcher. It recomputes the score from
    the parsed candidate and the job configuration with its own code (it does not
    import the Matcher's functions) and compares the two results.

--------------------------------------------------------------------------
AUDIT RESULT (dict) - returned by audit_candidate()
--------------------------------------------------------------------------
{
  "candidate_id": str | None,
  "audit_status": "passed" | "warning" | "review_required" | "error",
  "warnings":      [str, ...],   # non-critical findings
  "review_issues": [str, ...],   # serious findings (set the status to review_required)
  "checks": {"scoring_criteria", "criteria_consistency", "missing_information",
             "sensitive_data_usage"}  -> each "passed" | "warning" | "review_required",
  "qualification_evidence": {criterion: "demonstrated" | "not_demonstrated"
                                         | "unknown_missing_information"},
  "notes": [str, ...],           # information only (e.g. optional fields missing)
  "explanation": str
}
Audit status meanings:
  passed           the implemented checks found no issue (NOT a fairness guarantee)
  warning          non-critical issue(s); the result may continue with a visible warning
  review_required  serious issue(s) (e.g. an inappropriate scoring criterion, or a score
                   that does not follow the configured rules); a human must look
  error            the audit could not run (an input was not a dictionary)

--------------------------------------------------------------------------
VALIDATION RESULT (dict) - returned by validate_decision()
--------------------------------------------------------------------------
{
  "candidate_id": str | None,
  "validation_status": "validated" | "validated_with_warnings"
                       | "review_required" | "validation_failed",
  "checks": {"candidate_identity", "score_range", "score_recalculation",
             "criteria_consistency", "missing_information", "bias_audit_outcome"}
            -> each "passed" | "warning" | "review_required" | "failed" | "not_run",
  "recalculated_score": number | None,
  "recalculated_criterion_scores": {criterion: points},
  "score_difference": number | None,   # supplied score minus recalculated score
  "errors": [str, ...],                # HARD failures  -> validation_failed
  "warnings": [str, ...],              # visible, non-blocking
  "review_reasons": [str, ...],        # why a human must look (review_required)
  "human_review_required": bool,
  "explanation": str
}
These are VALIDATION outcomes about the pipeline, not hiring decisions. A low score
never makes a candidate "unqualified".
"""

from __future__ import annotations

import math
import re

from schemas import (
    MAX_SCORE,
    PARSE_FAILED,
    PARSE_PARTIAL,
    PROJECT_CRITERION,
    REQUIRED_CANDIDATE_FIELDS,
    SKILL_ALIASES,
)

# ============================================================== constants ====
# Rounding tolerance (in score points) used when comparing two scores.
# The prototype scores are whole numbers, so 0.01 only guards against float noise.
SCORE_TOLERANCE = 0.01

# Audit statuses
AUDIT_PASSED = "passed"
AUDIT_WARNING = "warning"
AUDIT_REVIEW = "review_required"
AUDIT_ERROR = "error"
AUDIT_STATUSES = (AUDIT_PASSED, AUDIT_WARNING, AUDIT_REVIEW, AUDIT_ERROR)

# Validation statuses
VAL_VALIDATED = "validated"
VAL_WARNINGS = "validated_with_warnings"
VAL_REVIEW = "review_required"
VAL_FAILED = "validation_failed"
VAL_NOT_RUN = "not_run"          # used by the workflow/report when the validator never ran
VALIDATION_STATUSES = (VAL_VALIDATED, VAL_WARNINGS, VAL_REVIEW, VAL_FAILED)

# Evidence labels (Bias Auditor, check 3)
EVIDENCE_DEMONSTRATED = "demonstrated"
EVIDENCE_NOT_DEMONSTRATED = "not_demonstrated"
EVIDENCE_UNKNOWN = "unknown_missing_information"

# Words that must never be part of a scoring criterion or of the scoring input.
# Matching is done on whole words of a field name ("marital_status" -> "marital").
SENSITIVE_TOKENS = {
    "age", "dob", "birth", "birthday", "gender", "sex", "religion", "caste",
    "marital", "marriage", "photo", "photograph", "picture", "image",
    "ethnicity", "race", "nationality", "disability", "pregnancy", "pregnant",
}

# Keys the Parser is documented to return (schemas.py section 3). Anything else
# in a parsed candidate is "unexpected".
DOCUMENTED_PARSED_KEYS = {
    "candidate_id", "name", "education", "skills", "experience_years", "projects",
    "certifications", "missing_fields", "parsing_status", "missing_optional_fields",
    "invalid_fields", "parsing_notes",
}

SCREENING_NOTE = (
    "Screening aid only. Not a hiring decision and not a prediction of job performance."
)


# ============================================================== helpers ======
def _is_number(value) -> bool:
    """True for real, finite numbers. Booleans are NOT numbers here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fmt(number) -> str:
    """40.0 -> '40', 12.5 -> '12.5'."""
    if not _is_number(number):
        return str(number)
    return str(int(number)) if float(number).is_integer() else str(round(number, 4))


def _text_list(value) -> list:
    """Return value as a list of strings (anything else -> empty list)."""
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return []


def _field_tokens(name: str) -> set:
    """'Marital_Status' -> {'marital', 'status'}"""
    return {t for t in re.split(r"[^a-z0-9]+", str(name).lower()) if t}


def _is_sensitive_name(name) -> bool:
    return isinstance(name, str) and bool(_field_tokens(name) & SENSITIVE_TOKENS)


def _first_id(*dicts):
    """First usable candidate_id found in the given dictionaries (else None)."""
    for d in dicts:
        if isinstance(d, dict):
            cid = d.get("candidate_id")
            if isinstance(cid, str) and cid.strip():
                return cid
    return None


# ----- independent skill normalisation (own code; NOT imported from agents.py) -----
_ALIAS_TO_CANONICAL = {
    spelling: canonical
    for canonical, spellings in SKILL_ALIASES.items()
    for spelling in spellings
}


def _canonical(raw) -> str:
    """Canonical lowercase form of a skill, used only for comparing skills."""
    if not isinstance(raw, str):
        return ""
    cleaned = re.sub(r"\s+", " ", raw.strip()).lower()
    if not cleaned:
        return ""
    for key in (cleaned, cleaned.replace("-", " ").replace("_", " ")):
        if key in _ALIAS_TO_CANONICAL:
            return _ALIAS_TO_CANONICAL[key].lower()
    return cleaned


def _spellings(skill: str) -> list:
    """All lowercase spellings that count as a mention of this skill."""
    for canonical, spellings in SKILL_ALIASES.items():
        if _canonical(skill) == canonical.lower():
            return list(spellings)
    return [str(skill).lower()]


# ============================================================================
#  COMPONENT A: BIAS AUDITOR AGENT
# ============================================================================
def scan_input_for_sensitive_fields(raw_candidate) -> list:
    """
    Look at the RAW candidate record (before parsing) and list field names that
    refer to sensitive characteristics (age, gender, photo, ...).

    Why this exists: the Parser deliberately drops unknown fields, so the Auditor
    (which only sees parsed data) could never notice them. The workflow calls this
    on the raw record and passes the result to apply_input_scan().
    These fields are never used for scoring.
    """
    if not isinstance(raw_candidate, dict):
        return []
    return sorted(k for k in raw_candidate if _is_sensitive_name(k))


def apply_input_scan(audit_result: dict, sensitive_fields: list) -> dict:
    """
    Return a copy of audit_result that also reports sensitive input fields.
    Nothing is changed when the list is empty. The score is never touched.
    """
    if not sensitive_fields or not isinstance(audit_result, dict):
        return audit_result
    updated = dict(audit_result)
    updated["warnings"] = list(audit_result.get("warnings", []))
    updated["checks"] = dict(audit_result.get("checks", {}))
    updated["warnings"].append(
        f"Sensitive field(s) found in the input record: {', '.join(sensitive_fields)}. "
        "They were ignored by the Parser and are not part of the scoring model, so they "
        "did not affect the score. Remove them from the input data."
    )
    updated["checks"]["sensitive_data_usage"] = "warning"
    if updated.get("audit_status") == AUDIT_PASSED:
        updated["audit_status"] = AUDIT_WARNING
    updated["explanation"] = (
        str(audit_result.get("explanation", "")) + " Sensitive input fields were detected and ignored."
    ).strip()
    return updated


def _qualification_evidence(match_result: dict, parsed: dict, job: dict) -> dict:
    """
    Separate three situations for every scored criterion:
      demonstrated                 the candidate's data shows it
      not_demonstrated             data was provided and it does NOT show it
      unknown_missing_information  the data needed to decide is missing/invalid
    """
    evidence = {}
    missing_required = set(_text_list(parsed.get("missing_fields")))
    invalid = set(_text_list(parsed.get("invalid_fields")))
    missing_optional = set(_text_list(parsed.get("missing_optional_fields")))
    matched = set(_text_list(match_result.get("matched_required_skills")))

    skills_unknown = "skills" in missing_required or "skills" in invalid or not parsed.get("skills")
    for skill in _text_list(job.get("required_skills")):
        if skill in matched:
            evidence[skill] = EVIDENCE_DEMONSTRATED
        elif skills_unknown:
            evidence[skill] = EVIDENCE_UNKNOWN
        else:
            evidence[skill] = EVIDENCE_NOT_DEMONSTRATED

    scores = match_result.get("criterion_scores") if isinstance(match_result.get("criterion_scores"), dict) else {}
    projects_unknown = "projects" in missing_optional or "projects" in invalid or not parsed.get("projects")
    if _is_number(scores.get(PROJECT_CRITERION)) and scores.get(PROJECT_CRITERION) > 0:
        evidence[PROJECT_CRITERION] = EVIDENCE_DEMONSTRATED
    elif projects_unknown:
        evidence[PROJECT_CRITERION] = EVIDENCE_UNKNOWN
    else:
        evidence[PROJECT_CRITERION] = EVIDENCE_NOT_DEMONSTRATED
    return evidence


def audit_candidate(match_result: dict, parsed_candidate: dict, job: dict) -> dict:
    """
    Bias Auditor Agent. Runs a few documented fairness-related checks and explains
    what it found. It READS the match result; it never changes the score.

    Check 1  scoring_criteria       are the job's scoring criteria job-related? (allowlist)
    Check 2  criteria_consistency   does this candidate's result use exactly the configured criteria?
    Check 3  missing_information    missing vs "not demonstrated" vs "demonstrated"
    Check 4  sensitive_data_usage   unexpected sensitive fields / criteria
    Check 5  explanation            a short plain-language summary
    """
    result = {
        "candidate_id": _first_id(parsed_candidate, match_result),
        "audit_status": AUDIT_ERROR,
        "warnings": [],
        "review_issues": [],
        "checks": {
            "scoring_criteria": "passed",
            "criteria_consistency": "passed",
            "missing_information": "passed",
            "sensitive_data_usage": "passed",
        },
        "qualification_evidence": {},
        "notes": [],
        "explanation": "",
    }

    # ---- the audit cannot run on non-dictionary input -----------------------
    bad = [name for name, value in (("match_result", match_result),
                                    ("parsed_candidate", parsed_candidate),
                                    ("job", job)) if not isinstance(value, dict)]
    if bad:
        result["review_issues"].append(f"Audit could not run: {', '.join(bad)} is not a dictionary.")
        result["checks"] = {name: "not_run" for name in result["checks"]}
        result["explanation"] = "The audit could not run because required input was missing or malformed."
        return result

    warnings, issues = result["warnings"], result["review_issues"]
    checks = result["checks"]

    # ---- Check 1: are the job's scoring criteria job-related? ----------------
    weights = job.get("skill_weights")
    required = job.get("required_skills")
    if not isinstance(weights, dict) or not isinstance(required, list):
        issues.append("scoring_criteria: the job configuration has no usable 'skill_weights' / 'required_skills'.")
        checks["scoring_criteria"] = "review_required"
        weights, required = {}, []
    else:
        # Documented allowlist: only the job's required skills and the project criterion.
        allowed = set(required) | {PROJECT_CRITERION}
        for criterion in weights:
            if _is_sensitive_name(criterion):
                issues.append(
                    f"scoring_criteria: criterion '{criterion}' refers to a sensitive characteristic "
                    "and is not appropriate for evaluating professional qualifications."
                )
                checks["scoring_criteria"] = "review_required"
            elif criterion not in allowed:
                issues.append(
                    f"scoring_criteria: criterion '{criterion}' is not a required skill of this job "
                    "(allowlist = required skills + relevant_project)."
                )
                checks["scoring_criteria"] = "review_required"
        for skill in required:
            if skill not in weights:
                issues.append(f"scoring_criteria: required skill '{skill}' has no configured weight.")
                checks["scoring_criteria"] = "review_required"

    # ---- Check 2: was this candidate evaluated with exactly the configured rules? ----
    scores = match_result.get("criterion_scores")
    if not isinstance(scores, dict):
        issues.append("criteria_consistency: the match result has no 'criterion_scores' dictionary.")
        checks["criteria_consistency"] = "review_required"
    else:
        expected = set(weights) | {PROJECT_CRITERION}   # the Matcher always reports the project criterion
        missing_keys = sorted(expected - set(scores))
        extra_keys = sorted(set(scores) - expected)
        if missing_keys:
            issues.append(f"criteria_consistency: criteria missing from the scoring result: {missing_keys}.")
            checks["criteria_consistency"] = "review_required"
        if extra_keys:
            issues.append(f"criteria_consistency: unexpected criteria in the scoring result: {extra_keys}.")
            checks["criteria_consistency"] = "review_required"
        for key in sorted(set(scores) & expected):
            weight = weights.get(key, 0)
            value = scores[key]
            if not _is_number(value):
                issues.append(f"criteria_consistency: score for '{key}' is not a number.")
                checks["criteria_consistency"] = "review_required"
            elif _is_number(weight) and abs(value) > SCORE_TOLERANCE and abs(value - weight) > SCORE_TOLERANCE:
                issues.append(
                    f"criteria_consistency: '{key}' earned {_fmt(value)} points, but the configured rule "
                    f"gives either 0 or its full weight ({_fmt(weight)})."
                )
                checks["criteria_consistency"] = "review_required"
        # The skills reported as matched/missing must cover the required skills exactly once.
        matched = _text_list(match_result.get("matched_required_skills"))
        missing = _text_list(match_result.get("missing_required_skills"))
        if required and (set(matched) | set(missing) != set(required) or set(matched) & set(missing)):
            issues.append(
                "criteria_consistency: matched and missing required skills do not add up to the job's "
                f"required skills (matched={matched}, missing={missing})."
            )
            checks["criteria_consistency"] = "review_required"
    if match_result.get("job_id") != job.get("job_id"):
        issues.append(
            f"criteria_consistency: the result was produced for job {match_result.get('job_id')!r}, "
            f"not {job.get('job_id')!r}."
        )
        checks["criteria_consistency"] = "review_required"
    if not _is_number(match_result.get("match_score")):
        issues.append("criteria_consistency: the match result contains no numeric score to audit.")
        checks["criteria_consistency"] = "review_required"

    # ---- Check 3: missing information (missing != not demonstrated) --------
    result["qualification_evidence"] = _qualification_evidence(match_result, parsed_candidate, job)
    required_missing = _text_list(parsed_candidate.get("missing_fields"))
    invalid = _text_list(parsed_candidate.get("invalid_fields"))
    optional_missing = _text_list(parsed_candidate.get("missing_optional_fields"))
    if parsed_candidate.get("parsing_status") == PARSE_FAILED:
        issues.append("missing_information: the candidate record could not be parsed.")
        checks["missing_information"] = "review_required"
    else:
        required_invalid = [f for f in invalid if f in REQUIRED_CANDIDATE_FIELDS]
        optional_invalid = [f for f in invalid if f not in REQUIRED_CANDIDATE_FIELDS]
        if required_missing or required_invalid:
            unknown = [c for c, e in result["qualification_evidence"].items() if e == EVIDENCE_UNKNOWN]
            detail = ""
            if unknown:
                detail = f" Criteria that cannot be judged from the data provided: {', '.join(unknown)}."
            warnings.append(
                "missing_information: required information is missing "
                f"({', '.join(required_missing) or 'none'}) or invalid ({', '.join(required_invalid) or 'none'})."
                f"{detail} Missing information is NOT evidence that the candidate lacks a qualification."
            )
            checks["missing_information"] = "warning"
        if optional_invalid:
            result["notes"].append(f"Optional field(s) with invalid values: {', '.join(optional_invalid)}.")
    if optional_missing:
        result["notes"].append(
            f"Optional information not provided: {', '.join(optional_missing)} (informational only)."
        )

    # ---- Check 4: sensitive information -----------------------------------
    unexpected_keys = sorted(set(parsed_candidate) - DOCUMENTED_PARSED_KEYS)
    sensitive_keys = [k for k in unexpected_keys if _is_sensitive_name(k)]
    other_keys = [k for k in unexpected_keys if k not in sensitive_keys]
    if sensitive_keys:
        warnings.append(
            f"sensitive_data_usage: sensitive field(s) present in the parsed candidate: {', '.join(sensitive_keys)}. "
            "They are not part of the scoring model and must not influence the score; remove them upstream."
        )
        checks["sensitive_data_usage"] = "warning"
    if other_keys:
        result["notes"].append(f"Unexpected non-sensitive field(s) in the parsed candidate: {', '.join(other_keys)}.")
    sensitive_score_keys = [k for k in (scores or {}) if _is_sensitive_name(k)] if isinstance(scores, dict) else []
    if sensitive_score_keys:
        issues.append(
            f"sensitive_data_usage: the scoring result contains criteria that refer to sensitive characteristics: "
            f"{sensitive_score_keys}."
        )
        checks["sensitive_data_usage"] = "review_required"

    # ---- status + Check 5: explanation ------------------------------------
    if issues:
        result["audit_status"] = AUDIT_REVIEW
    elif warnings:
        result["audit_status"] = AUDIT_WARNING
    else:
        result["audit_status"] = AUDIT_PASSED

    if result["audit_status"] == AUDIT_PASSED:
        result["explanation"] = (
            "The configured criteria were used consistently and no issue was detected by the "
            "implemented checks. This does not prove the evaluation is fair."
        )
    else:
        parts = []
        if issues:
            parts.append(f"{len(issues)} serious issue(s) need a human look")
        if warnings:
            parts.append(f"{len(warnings)} warning(s) found")
        result["explanation"] = (
            "; ".join(parts).capitalize() + ". The audit reports findings only; it did not change the score."
        )
    return result


# ============================================================================
#  COMPONENT B: DECISION VALIDATOR AGENT
# ============================================================================
def _project_is_relevant(projects: list, required: list, equivalents: dict) -> bool:
    """Documented rule: a project is relevant if its text mentions a required skill
    (or a listed equivalent) as a whole word, ignoring case."""
    names = []
    for skill in required:
        names.append(skill)
        names.extend(equivalents.get(skill, []))
    for text in projects:
        if not isinstance(text, str):
            continue
        lowered = text.lower()
        for name in names:
            for spelling in _spellings(name):
                if re.search(r"(?<![a-z0-9])" + re.escape(spelling) + r"(?![a-z0-9])", lowered):
                    return True
    return False


def recalculate_score(parsed_candidate: dict, job: dict) -> dict:
    """
    Independent re-computation of the score. Same documented rule as the Matcher,
    but written separately: the validator never calls the Matcher.
      - required skill (or a listed equivalent) present  -> its weight, else 0
      - any project mentioning a required skill           -> project weight, else 0
      - preferred skills and experience are NOT scored
    Returns {"criterion_scores", "matched_required_skills", "missing_required_skills", "total"}.
    """
    weights = job["skill_weights"]
    required = job["required_skills"]
    equivalents = job.get("skill_equivalents") or {}
    have = {_canonical(s) for s in (parsed_candidate.get("skills") or []) if isinstance(s, str)}

    scores, matched, missing = {}, [], []
    for skill in required:
        accepted = [skill] + list(equivalents.get(skill, []))
        if any(_canonical(name) in have for name in accepted):
            scores[skill] = weights[skill]
            matched.append(skill)
        else:
            scores[skill] = 0
            missing.append(skill)
    project_weight = weights.get(PROJECT_CRITERION, 0)
    relevant = _project_is_relevant(parsed_candidate.get("projects") or [], required, equivalents)
    scores[PROJECT_CRITERION] = project_weight if relevant else 0

    total = max(0, min(MAX_SCORE, sum(scores.values())))
    return {
        "criterion_scores": scores,
        "matched_required_skills": matched,
        "missing_required_skills": missing,
        "total": total,
    }


def _check_job_weights(job: dict, errors: list) -> bool:
    """Check 5 (job part). Returns True when the job configuration can be used."""
    ok = True
    weights = job.get("skill_weights")
    required = job.get("required_skills")
    if not isinstance(weights, dict) or not weights:
        errors.append("criteria_consistency: 'skill_weights' is missing or empty.")
        return False
    if not isinstance(required, list) or not required or not all(isinstance(s, str) and s.strip() for s in required):
        errors.append("criteria_consistency: 'required_skills' must be a non-empty list of text values.")
        return False
    bad = [k for k, v in weights.items() if not _is_number(v) or v < 0]
    if bad:
        errors.append(f"criteria_consistency: weights must be numbers >= 0 (bad: {bad}).")
        ok = False
    else:
        total = sum(weights.values())
        if abs(total - MAX_SCORE) > SCORE_TOLERANCE:
            errors.append(f"criteria_consistency: weights sum to {_fmt(total)}, but they must sum to {MAX_SCORE}.")
            ok = False
    for skill in required:
        if skill not in weights:
            errors.append(f"criteria_consistency: required skill '{skill}' has no weight.")
            ok = False
    allowed = set(required) | {PROJECT_CRITERION}
    preferred = job.get("preferred_skills") if isinstance(job.get("preferred_skills"), list) else []
    for key in weights:
        if key in preferred:
            errors.append(f"criteria_consistency: preferred skill '{key}' has a weight, but preferred skills are never scored.")
            ok = False
        elif key not in allowed:
            errors.append(f"criteria_consistency: weight '{key}' is not a scored criterion of this job.")
            ok = False
    return ok


def validate_decision(audit_result: dict, match_result: dict, parsed_candidate: dict, job: dict) -> dict:
    """
    Decision Validator Agent. Independently checks the pipeline's output for ONE candidate.

    Hard failures (errors)   -> validation_failed
    Needs a person           -> review_required      (missing information, serious audit finding)
    Visible, non-blocking    -> validated_with_warnings
    Nothing found            -> validated
    """
    checks = {
        "candidate_identity": "not_run",
        "score_range": "not_run",
        "score_recalculation": "not_run",
        "criteria_consistency": "not_run",
        "missing_information": "not_run",
        "bias_audit_outcome": "not_run",
    }
    out = {
        "candidate_id": _first_id(parsed_candidate, match_result, audit_result),
        "validation_status": VAL_FAILED,
        "checks": checks,
        "recalculated_score": None,
        "recalculated_criterion_scores": {},
        "score_difference": None,
        "errors": [],
        "warnings": [],
        "review_reasons": [],
        "human_review_required": True,
        "explanation": "",
    }
    errors, warnings, reasons = out["errors"], out["warnings"], out["review_reasons"]

    # ---- all four inputs must be dictionaries ------------------------------
    bad = [n for n, v in (("match_result", match_result), ("parsed_candidate", parsed_candidate), ("job", job))
           if not isinstance(v, dict)]
    if bad:
        errors.append(f"candidate_identity: input is not a dictionary: {', '.join(bad)}.")
        out["explanation"] = "Validation failed because required input was missing or malformed."
        return out
    if not isinstance(audit_result, dict):
        # The Bias Auditor result is mandatory: a missing audit is never silently approved.
        checks["bias_audit_outcome"] = "failed"
        errors.append("bias_audit_outcome: the Bias Auditor result is missing, so the result cannot be approved.")
        audit_result = {}

    # ---- Check 1: candidate identity ---------------------------------------
    ids = {
        "parsed candidate": parsed_candidate.get("candidate_id"),
        "match result": match_result.get("candidate_id"),
        "audit result": audit_result.get("candidate_id"),
    }
    unusable = [k for k, v in ids.items() if not isinstance(v, str) or not v.strip()]
    if unusable:
        errors.append(f"candidate_identity: candidate_id is missing in: {', '.join(unusable)}.")
        checks["candidate_identity"] = "failed"
    elif len(set(ids.values())) > 1:
        errors.append(f"candidate_identity: candidate_id differs between agents: {ids}.")
        checks["candidate_identity"] = "failed"
    else:
        checks["candidate_identity"] = "passed"
    if match_result.get("job_id") != job.get("job_id"):
        errors.append(
            f"candidate_identity: the match result is for job {match_result.get('job_id')!r}, "
            f"but the job is {job.get('job_id')!r}."
        )
        checks["candidate_identity"] = "failed"

    # ---- Check 5 (job part first, because recalculation depends on it) -----
    config_ok = _check_job_weights(job, errors)

    # ---- Check 2: score range -----------------------------------------------
    score = match_result.get("match_score")
    status = match_result.get("matching_status")
    if status in ("skipped", "failed"):
        errors.append(f"score_range: the Matcher did not produce a result (matching_status='{status}').")
        checks["score_range"] = "failed"
    elif not _is_number(score):
        errors.append(f"score_range: match_score is not a number (got {score!r}).")
        checks["score_range"] = "failed"
    elif score < 0 or score > MAX_SCORE:
        errors.append(f"score_range: match_score {_fmt(score)} is outside 0-{MAX_SCORE}.")
        checks["score_range"] = "failed"
    else:
        checks["score_range"] = "passed"

    # ---- Check 4: missing information (is it represented explicitly?) -------
    required_set = set(REQUIRED_CANDIDATE_FIELDS)
    absent = [f for f in REQUIRED_CANDIDATE_FIELDS if parsed_candidate.get(f) in (None, "", [])]
    declared_missing = set(_text_list(parsed_candidate.get("missing_fields")))
    declared_invalid = set(_text_list(parsed_candidate.get("invalid_fields")))
    hidden = [f for f in absent if f not in declared_missing | declared_invalid]
    if hidden:
        errors.append(
            f"missing_information: required field(s) {hidden} are empty but are not listed as missing, "
            "so the information gap is hidden."
        )
        checks["missing_information"] = "failed"
    elif declared_missing & required_set or declared_invalid & required_set:
        problems = sorted((declared_missing | declared_invalid) & required_set)
        reasons.append(
            f"missing_information: required information is missing or invalid ({', '.join(problems)}). "
            "The score reflects only the data provided; the candidate is NOT rejected - a recruiter should review."
        )
        checks["missing_information"] = "review_required"
        if status == "success":
            warnings.append(
                "missing_information: the record is incomplete but the Matcher reported matching_status='success'."
            )
    else:
        checks["missing_information"] = "passed"
        if declared_invalid - required_set:
            warnings.append(
                f"missing_information: optional field(s) with invalid values: {sorted(declared_invalid - required_set)}."
            )

    # ---- Check 3: independent score recalculation --------------------------
    if config_ok:
        recalculated = recalculate_score(parsed_candidate, job)
        out["recalculated_score"] = recalculated["total"]
        out["recalculated_criterion_scores"] = recalculated["criterion_scores"]
        supplied_scores = match_result.get("criterion_scores")
        problems = []
        if not isinstance(supplied_scores, dict):
            problems.append("the match result has no 'criterion_scores' dictionary")
        else:
            for criterion, expected in recalculated["criterion_scores"].items():
                got = supplied_scores.get(criterion)
                if not _is_number(got):
                    problems.append(f"criterion '{criterion}' is missing or not numeric in the match result")
                elif abs(got - expected) > SCORE_TOLERANCE:
                    problems.append(f"criterion '{criterion}': Matcher gave {_fmt(got)}, validator recalculated {_fmt(expected)}")
        if _is_number(score):
            out["score_difference"] = round(score - recalculated["total"], 6)
            if abs(score - recalculated["total"]) > SCORE_TOLERANCE:
                problems.append(
                    f"Score discrepancy: Matcher reported {_fmt(score)} but the independent recalculation "
                    f"gives {_fmt(recalculated['total'])} (difference {_fmt(out['score_difference'])})"
                )
        if set(_text_list(match_result.get("matched_required_skills"))) != set(recalculated["matched_required_skills"]):
            problems.append(
                f"matched required skills differ: Matcher {_text_list(match_result.get('matched_required_skills'))}, "
                f"validator {recalculated['matched_required_skills']}"
            )
        if set(_text_list(match_result.get("missing_required_skills"))) != set(recalculated["missing_required_skills"]):
            problems.append(
                f"missing required skills differ: Matcher {_text_list(match_result.get('missing_required_skills'))}, "
                f"validator {recalculated['missing_required_skills']}"
            )
        if problems:
            for p in problems:
                errors.append(f"score_recalculation: {p}.")
            checks["score_recalculation"] = "failed"
        else:
            checks["score_recalculation"] = "passed"
    else:
        warnings.append("score_recalculation: not performed because the job configuration is invalid.")

    # ---- Check 5 (result part): criteria in the result vs the configuration -
    supplied_scores = match_result.get("criterion_scores")
    if config_ok and isinstance(supplied_scores, dict):
        allowed = set(job["skill_weights"]) | {PROJECT_CRITERION}
        extra = sorted(set(supplied_scores) - allowed)
        if extra:
            errors.append(f"criteria_consistency: unexpected criteria in the match result: {extra}.")
        numeric = [v for v in supplied_scores.values() if _is_number(v)]
        total_of_parts = sum(numeric)
        if total_of_parts > MAX_SCORE + SCORE_TOLERANCE:
            errors.append(f"criteria_consistency: criterion scores add up to {_fmt(total_of_parts)}, more than {MAX_SCORE}.")
        if _is_number(score) and abs(total_of_parts - score) > SCORE_TOLERANCE:
            errors.append(
                f"criteria_consistency: match_score {_fmt(score)} does not equal the sum of its "
                f"criterion scores ({_fmt(total_of_parts)})."
            )
    criteria_errors = [e for e in errors if e.startswith("criteria_consistency:")]
    checks["criteria_consistency"] = "failed" if criteria_errors else "passed"

    # ---- Check 6: bias audit outcome (never silently approved) -------------
    audit_status = audit_result.get("audit_status")
    if checks["bias_audit_outcome"] != "failed":
        if audit_status == AUDIT_PASSED:
            checks["bias_audit_outcome"] = "passed"
        elif audit_status == AUDIT_WARNING:
            checks["bias_audit_outcome"] = "warning"
            for msg in _text_list(audit_result.get("warnings")):
                warnings.append(f"Bias audit warning: {msg}")
        elif audit_status == AUDIT_REVIEW:
            checks["bias_audit_outcome"] = "review_required"
            for msg in _text_list(audit_result.get("review_issues")) or ["The Bias Auditor requested a human review."]:
                reasons.append(f"Bias audit: {msg}")
        elif audit_status == AUDIT_ERROR:
            checks["bias_audit_outcome"] = "failed"
            errors.append("bias_audit_outcome: the Bias Auditor reported an error, so the result cannot be approved.")
        else:
            checks["bias_audit_outcome"] = "failed"
            errors.append(f"bias_audit_outcome: unknown or missing audit_status ({audit_status!r}).")

    # ---- reported-only note: experience (never scored) ---------------------
    minimum = job.get("minimum_experience_years")
    experience = parsed_candidate.get("experience_years")
    if _is_number(minimum) and minimum > 0 and _is_number(experience) and experience < minimum:
        warnings.append(
            f"Reported only (experience is not scored): the candidate lists {_fmt(experience)} year(s), "
            f"below the posting's stated {_fmt(minimum)}+ year(s). A recruiter may want to look."
        )

    # ---- final status -------------------------------------------------------
    if errors:
        out["validation_status"] = VAL_FAILED
        out["human_review_required"] = True
    elif reasons:
        out["validation_status"] = VAL_REVIEW
        out["human_review_required"] = True
    elif warnings:
        out["validation_status"] = VAL_WARNINGS
        out["human_review_required"] = False
    else:
        out["validation_status"] = VAL_VALIDATED
        out["human_review_required"] = False

    # ---- explanation --------------------------------------------------------
    if out["validation_status"] == VAL_VALIDATED:
        out["explanation"] = (
            f"The candidate IDs agree, the score ({_fmt(score)}) is inside 0-{MAX_SCORE} and matches the "
            f"independently recalculated score ({_fmt(out['recalculated_score'])}). No issues found by the implemented checks."
        )
    elif out["validation_status"] == VAL_WARNINGS:
        out["explanation"] = (
            f"The score ({_fmt(score)}) matches the independent recalculation, but {len(warnings)} visible "
            "warning(s) should be read by the recruiter."
        )
    elif out["validation_status"] == VAL_REVIEW:
        out["explanation"] = (
            f"No hard error was found, but {len(reasons)} reason(s) require a human review before this result is used. "
            "This is not a rejection of the candidate."
        )
    else:
        out["explanation"] = (
            f"Validation FAILED with {len(errors)} error(s). The result must not be approved; "
            "a human must review it. This is not a rejection of the candidate."
        )
    return out


# ============================================================================
#  COMPONENT C: REPORT GENERATOR
# ============================================================================
class ReportExportError(Exception):
    """Raised with a readable message when the CSV report cannot be created."""


REPORT_COLUMNS = [
    "input_position", "candidate_id", "candidate_name", "job_title", "company",
    "match_score", "matched_required_skills", "missing_required_skills",
    "matched_preferred_skills", "criterion_scores", "parsing_status", "matching_status",
    "audit_status", "audit_warnings", "validation_status", "validation_errors",
    "validation_warnings", "recalculated_score", "score_difference",
    "human_review_required", "review_reasons", "routing", "explanation", "screening_note",
]


def _join(items) -> str:
    return "; ".join(str(i) for i in items) if isinstance(items, (list, tuple)) and items else ""


def _format_criteria(scores) -> str:
    if not isinstance(scores, dict) or not scores:
        return ""
    return "; ".join(f"{k}={_fmt(v)}" for k, v in scores.items())


def candidate_outcome(result: dict) -> dict:
    """
    One place that decides a candidate's final status, used by the report AND the summary.

      validation_status        from the validator, or "not_run" if it never ran
      human_review_required    True if the validator says so, OR the validator did not run,
                               OR the workflow attached its own review reasons
      routing                  "report"       -> validation passed, normal report
                               "human_review" -> a person must look first
      reasons                  everything a recruiter should read
    """
    validation = result.get("validation") if isinstance(result, dict) else None
    workflow_reasons = list(result.get("review_reasons") or []) if isinstance(result, dict) else []
    if isinstance(validation, dict) and validation.get("validation_status") in VALIDATION_STATUSES:
        status = validation["validation_status"]
        review = bool(validation.get("human_review_required"))
        reasons = list(validation.get("errors") or []) + list(validation.get("review_reasons") or [])
    else:
        status = VAL_NOT_RUN
        review = True
        reasons = []
    reasons.extend(workflow_reasons)
    if workflow_reasons:
        review = True
    if status == VAL_NOT_RUN and not reasons:
        reasons.append("Validation did not run for this candidate.")
    return {
        "validation_status": status,
        "human_review_required": review,
        "routing": "human_review" if review else "report",
        "reasons": reasons,
    }


def summarize_candidate_results(candidate_results: list) -> dict:
    """KPI numbers. validated + requiring_review == total; failures are a subset of review."""
    total = validated = review = failures = 0
    for result in candidate_results or []:
        outcome = candidate_outcome(result)
        total += 1
        if outcome["human_review_required"]:
            review += 1
        else:
            validated += 1
        if outcome["validation_status"] == VAL_FAILED:
            failures += 1
    return {
        "total_candidates": total,
        "validated_candidates": validated,
        "candidates_requiring_review": review,
        "validation_failures": failures,
    }


def _report_row(result: dict, job: dict, position: int) -> dict:
    parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
    match = result.get("match") if isinstance(result.get("match"), dict) else {}
    audit = result.get("audit") if isinstance(result.get("audit"), dict) else {}
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    outcome = candidate_outcome(result)

    audit_messages = [f"REVIEW: {m}" for m in _text_list(audit.get("review_issues"))] + _text_list(audit.get("warnings"))
    pieces = []
    if outcome["human_review_required"]:
        pieces.append("HUMAN REVIEW REQUIRED: " + (" | ".join(outcome["reasons"]) or "see validation status."))
    if match.get("explanation"):
        pieces.append("Matching: " + str(match["explanation"]))
    if audit.get("explanation"):
        pieces.append("Audit: " + str(audit["explanation"]))
    if validation.get("explanation"):
        pieces.append("Validation: " + str(validation["explanation"]))

    score = match.get("match_score")
    return {
        "input_position": result.get("input_position", position),
        "candidate_id": _first_id(parsed, match, audit, validation, result) or "(missing ID)",
        "candidate_name": parsed.get("name") or "",
        "job_title": job.get("job_title", ""),
        "company": job.get("company", ""),
        "match_score": score if _is_number(score) else None,
        "matched_required_skills": _join(match.get("matched_required_skills")),
        "missing_required_skills": _join(match.get("missing_required_skills")),
        "matched_preferred_skills": _join(match.get("matched_preferred_skills")),
        "criterion_scores": _format_criteria(match.get("criterion_scores")),
        "parsing_status": parsed.get("parsing_status", "not_run"),
        "matching_status": match.get("matching_status", "not_run"),
        "audit_status": audit.get("audit_status", "not_run"),
        "audit_warnings": " | ".join(audit_messages),
        "validation_status": outcome["validation_status"],
        "validation_errors": " | ".join(_text_list(validation.get("errors"))),
        "validation_warnings": " | ".join(_text_list(validation.get("warnings"))),
        "recalculated_score": validation.get("recalculated_score") if _is_number(validation.get("recalculated_score")) else None,
        "score_difference": validation.get("score_difference") if _is_number(validation.get("score_difference")) else None,
        "human_review_required": outcome["human_review_required"],
        "review_reasons": " | ".join(outcome["reasons"]),
        "routing": outcome["routing"],
        "explanation": " ".join(pieces),
        "screening_note": SCREENING_NOTE,
    }


def generate_report(results) -> dict:
    """
    Report Generator. Turns the agents' structured outputs into a recruiter report.

    `results` is a dict {"job": job, "candidate_results": [...]} (a bare list of
    candidate results is accepted too). Each candidate result holds the outputs of
    the agents: "parsed", "match", "audit", "validation" (+ optional workflow notes).

    Nothing is invented: an agent that did not run shows "not_run", and the row is
    sent to human review. The match score is shown as a screening aid, never as a
    probability of job performance.
    """
    if isinstance(results, dict):
        job = results.get("job") if isinstance(results.get("job"), dict) else {}
        candidate_results = results.get("candidate_results")
    else:
        job, candidate_results = {}, results
    if not isinstance(candidate_results, (list, tuple)):
        candidate_results = []

    rows = [
        _report_row(r if isinstance(r, dict) else {}, job, position)
        for position, r in enumerate(candidate_results, start=1)
    ]
    return {
        "report_status": "generated",
        "title": "RecruitAI Screening Report",
        "job": {k: job.get(k) for k in ("job_id", "company", "job_title", "source_url", "access_date")},
        "columns": list(REPORT_COLUMNS),
        "rows": rows,
        "summary": summarize_candidate_results(list(candidate_results)),
        "approved_for_report": [r["candidate_id"] for r in rows if r["routing"] == "report"],
        "held_for_human_review": [r["candidate_id"] for r in rows if r["routing"] == "human_review"],
        "disclaimer": (
            "This report supports a human recruiter. " + SCREENING_NOTE +
            " The candidates are synthetic and the scoring weights are prototype-defined."
        ),
    }


def report_to_dataframe(report: dict):
    """Report rows as a Pandas DataFrame with the SAME columns every time."""
    import pandas as pd

    if not isinstance(report, dict) or not isinstance(report.get("rows"), list):
        raise ReportExportError("The report has no rows to export (was the workflow stopped?).")
    return pd.DataFrame(report["rows"], columns=REPORT_COLUMNS)


def report_to_csv(report: dict) -> str:
    """CSV text of the report. Raises ReportExportError with a readable message on failure."""
    try:
        return report_to_dataframe(report).to_csv(index=False)
    except ReportExportError:
        raise
    except Exception as exc:  # pandas can raise several types; we re-raise a readable one
        raise ReportExportError(f"Could not create the CSV report: {type(exc).__name__}: {exc}") from exc
