"""
view_tables.py - turns workflow results into Pandas tables for the Streamlit UI.

This file does NOT import Streamlit, so these functions can be tested without
launching the app (see tests/test_workflow.py). app.py only displays what these
functions return; nothing here invents data - every cell comes from an agent output.
"""

from __future__ import annotations

import pandas as pd

from agents_part2 import report_to_dataframe

RESULT_TABLE_COLUMNS = [
    "Candidate ID", "Candidate Name", "Match Score", "Matched Skills", "Missing Skills",
    "Audit Status", "Validation Status", "Human Review Required", "Routing",
]


def _join(items) -> str:
    return ", ".join(str(i) for i in items) if isinstance(items, (list, tuple)) and items else ""


def _label(record: dict) -> str:
    return record.get("candidate_id") or f"(missing ID, input #{record.get('input_position')})"


def candidates_table(candidates: list) -> pd.DataFrame:
    """Raw input candidates, every cell converted to text (a column can mix numbers and text,
    e.g. experience_years = 'about two', which would break an Arrow table)."""
    rows = []
    for c in candidates or []:
        c = c if isinstance(c, dict) else {}
        rows.append({
            "candidate_id": str(c.get("candidate_id", "")),
            "name": str(c.get("name", "")),
            "education": str(c.get("education", "")),
            "skills": _join(c.get("skills")) if isinstance(c.get("skills"), list) else str(c.get("skills", "(not provided)")),
            "experience_years": str(c.get("experience_years", "(not provided)")),
            "projects": "; ".join(c.get("projects", [])) if isinstance(c.get("projects"), list) else str(c.get("projects", "")),
            "certifications": _join(c.get("certifications")),
        })
    return pd.DataFrame(rows, columns=["candidate_id", "name", "education", "skills", "experience_years",
                                       "projects", "certifications"])


def results_table(report: dict) -> pd.DataFrame:
    """Section 5 table, built from the report rows (the same data as the CSV)."""
    df = report_to_dataframe(report)
    table = pd.DataFrame({
        "Candidate ID": df["candidate_id"].astype(str),
        "Candidate Name": df["candidate_name"].astype(str),
        "Match Score": pd.to_numeric(df["match_score"], errors="coerce"),
        "Matched Skills": df["matched_required_skills"].astype(str),
        "Missing Skills": df["missing_required_skills"].astype(str),
        "Audit Status": df["audit_status"].astype(str),
        "Validation Status": df["validation_status"].astype(str),
        "Human Review Required": df["human_review_required"].astype(bool),
        "Routing": df["routing"].astype(str),
    })
    return table[RESULT_TABLE_COLUMNS]


def parse_table(records: list) -> pd.DataFrame:
    rows = []
    for r in records:
        p = r.get("parsed") or {}
        rows.append({
            "Candidate": _label(r),
            "Parsing status": (r["steps"].get("parse") or {}).get("status", "not_run"),
            "Skills found": _join(p.get("skills")),
            "Missing required fields": _join(p.get("missing_fields")),
            "Invalid fields": _join(p.get("invalid_fields")),
            "Notes": "; ".join(p.get("parsing_notes") or []) or (r["steps"].get("parse") or {}).get("detail", ""),
        })
    return pd.DataFrame(rows)


def match_table(records: list) -> pd.DataFrame:
    rows = []
    for r in records:
        m = r.get("match") or {}
        score = m.get("match_score")
        rows.append({
            "Candidate": _label(r),
            "Matching status": (r["steps"].get("match") or {}).get("status", "not_run"),
            "Score (0-100)": score if isinstance(score, (int, float)) else None,
            "Matched required": _join(m.get("matched_required_skills")),
            "Missing required": _join(m.get("missing_required_skills")),
            "Matched preferred (not scored)": _join(m.get("matched_preferred_skills")),
        })
    return pd.DataFrame(rows)


def audit_table(records: list) -> pd.DataFrame:
    rows = []
    for r in records:
        a = r.get("audit")
        step = r["steps"].get("audit") or {}
        row = {"Candidate": _label(r), "Audit status": (a or {}).get("audit_status", step.get("status", "not_run"))}
        checks = (a or {}).get("checks") or {}
        for name in ("scoring_criteria", "criteria_consistency", "missing_information", "sensitive_data_usage"):
            row[name] = checks.get(name, "not_run")
        row["Warnings"] = len((a or {}).get("warnings") or [])
        row["Serious issues"] = len((a or {}).get("review_issues") or [])
        rows.append(row)
    return pd.DataFrame(rows)


def validation_table(records: list) -> pd.DataFrame:
    rows = []
    for r in records:
        v = r.get("validation")
        step = r["steps"].get("validate") or {}
        row = {"Candidate": _label(r),
               "Validation status": (v or {}).get("validation_status", "not_run" if step.get("status") in (None, "skipped") else step.get("status"))}
        checks = (v or {}).get("checks") or {}
        for name in ("candidate_identity", "score_range", "score_recalculation", "criteria_consistency",
                     "missing_information", "bias_audit_outcome"):
            row[name] = checks.get(name, "not_run")
        row["Matcher score"] = (r.get("match") or {}).get("match_score")
        row["Recalculated"] = (v or {}).get("recalculated_score")
        row["Difference"] = (v or {}).get("score_difference")
        row["Human review"] = bool((r.get("outcome") or {}).get("human_review_required", True))
        rows.append(row)
    return pd.DataFrame(rows)


def criterion_table(record: dict, job: dict) -> pd.DataFrame:
    """Criterion-wise view for ONE candidate: weight, points from the Matcher, points recalculated
    by the Validator, and the Auditor's evidence label."""
    weights = (job or {}).get("skill_weights") or {}
    m_scores = (record.get("match") or {}).get("criterion_scores") or {}
    v_scores = (record.get("validation") or {}).get("recalculated_criterion_scores") or {}
    evidence = (record.get("audit") or {}).get("qualification_evidence") or {}
    names = list(weights) + [k for k in m_scores if k not in weights]
    return pd.DataFrame([{
        "Criterion": name,
        "Weight": weights.get(name),
        "Matcher points": m_scores.get(name),
        "Validator recalculated": v_scores.get(name),
        "Evidence (Auditor)": evidence.get(name, ""),
    } for name in names])


def issues_table(candidate_results: list) -> pd.DataFrame:
    return pd.DataFrame(collect_issues(candidate_results), columns=["candidate_id", "category", "severity", "message"])


def review_reasons_table(candidate_results: list) -> pd.DataFrame:
    rows = []
    for r in candidate_results or []:
        outcome = r.get("outcome") or {}
        if outcome.get("human_review_required"):
            rows.append({"candidate_id": _label(r), "validation_status": outcome.get("validation_status"),
                         "reasons": " | ".join(outcome.get("reasons") or [])})
    return pd.DataFrame(rows, columns=["candidate_id", "validation_status", "reasons"])


def collect_issues(candidate_results: list) -> list:
    """
    Flat list of problems for the "Warnings and Validation" section:
    [{"candidate_id", "category", "severity", "message"}, ...]
    Categories: Missing information, Fairness warning, Score discrepancy,
    Validation failure, Validation warning, Processing error, Workflow flag.
    """
    issues = []

    def add(record, category, severity, message):
        issues.append({"candidate_id": _label(record), "category": category,
                       "severity": severity, "message": message})

    for record in candidate_results or []:
        parsed = record.get("parsed") or {}
        audit = record.get("audit") or {}
        validation = record.get("validation") or {}
        required_missing = parsed.get("missing_fields") or []
        invalid = parsed.get("invalid_fields") or []
        if required_missing or invalid:
            add(record, "Missing information", "warning",
                f"Missing required: {', '.join(required_missing) or 'none'}; "
                f"invalid values: {', '.join(invalid) or 'none'}. "
                "This is not evidence that the candidate lacks the qualification.")
        for message in audit.get("review_issues") or []:
            add(record, "Fairness warning", "serious", message)
        for message in audit.get("warnings") or []:
            if not message.startswith("missing_information:"):      # already listed above
                add(record, "Fairness warning", "warning", message)
        for message in validation.get("errors") or []:
            category = "Score discrepancy" if message.startswith("score_recalculation:") else "Validation failure"
            add(record, category, "error", message)
        for message in validation.get("warnings") or []:
            if not message.startswith("Bias audit warning:"):       # already listed above
                add(record, "Validation warning", "warning", message)
        for message in record.get("errors") or []:
            add(record, "Processing error", "error", message)
        for message in record.get("review_reasons") or []:
            if not message.startswith("Processing error"):          # already listed above
                add(record, "Workflow flag", "warning", message)
    return issues
