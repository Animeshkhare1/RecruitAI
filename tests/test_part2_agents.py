"""
tests/test_part2_agents.py - unit tests for the Bias Auditor, Decision Validator and
Report Generator (agents_part2.py), plus a parity check against User 1's Matcher.

Run from the RecruitAI folder:   python -m pytest -q
Plain asserts only; each test says what we expect.
"""

import random
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import match_candidate, parse_candidate
from agents_part2 import (
    AUDIT_STATUSES, VALIDATION_STATUSES, apply_input_scan, audit_candidate, candidate_outcome,
    generate_report, recalculate_score, scan_input_for_sensitive_fields, summarize_candidate_results,
    validate_decision,
)
from dataset import get_sample_candidates, get_sample_job
from schemas import SKILL_ALIASES
from workflow import run_workflow


def _candidate(**overrides):
    base = {"candidate_id": "T001", "name": "Test Candidate", "education": "B.Tech",
            "skills": ["SQL", "ETL", "Data Modeling", "Data Warehousing", "Python"],
            "experience_years": 2, "projects": ["ETL pipeline in Python"], "certifications": []}
    base.update(overrides)
    return base


def _pipeline(candidate, job=None):
    job = job or get_sample_job()
    parsed = parse_candidate(candidate)
    match = match_candidate(parsed, job)
    audit = audit_candidate(match, parsed, job)
    return parsed, match, audit, job


# ---- the validator's own scoring code agrees with the Matcher --------------
def test_independent_recalculation_agrees_with_the_matcher_on_all_samples():
    # Expect: same total, same criterion scores, same matched/missing lists for the 8 candidates.
    job = get_sample_job()
    for record in get_sample_candidates() + [{"candidate_id": "EMPTY"}]:
        parsed = parse_candidate(record)
        match = match_candidate(parsed, job)
        mine = recalculate_score(parsed, job)
        assert mine["total"] == match["match_score"], record["candidate_id"]
        assert mine["criterion_scores"] == match["criterion_scores"]
        assert mine["matched_required_skills"] == match["matched_required_skills"]
        assert mine["missing_required_skills"] == match["missing_required_skills"]


def test_independent_recalculation_agrees_with_the_matcher_on_random_candidates():
    # Expect: no disagreement on 300 pseudo-random synthetic candidates (fixed seed, so repeatable).
    rng = random.Random(2026)
    job = get_sample_job()
    spellings = [s for group in SKILL_ALIASES.values() for s in group] + ["Tableau", "Perl", "Scala", "  SQL  ", "PYTHON"]
    project_bits = ["ETL pipeline", "poster design", "Python scripts", "data warehouse build", "ksh job",
                    "SQL reports", "Spark app", "data modelling", "hiveql", "sqlite notes", "etl-tool"]
    for n in range(300):
        record = {"candidate_id": f"R{n}", "name": "x", "experience_years": rng.choice([0, 0.5, 1, 3, 10]),
                  "skills": rng.sample(spellings, rng.randint(0, 6)),
                  "projects": rng.sample(project_bits, rng.randint(0, 3))}
        parsed = parse_candidate(record)
        match = match_candidate(parsed, job)
        mine = recalculate_score(parsed, job)
        assert mine["total"] == match["match_score"], record
        assert mine["criterion_scores"] == match["criterion_scores"], record


# ---- Bias Auditor ----------------------------------------------------------
def test_audit_of_a_clean_candidate_passes_and_never_changes_the_score():
    parsed, match, audit, job = _pipeline(_candidate())
    before = deepcopy(match)
    audit_candidate(match, parsed, job)
    assert match == before                                         # read-only
    assert audit["audit_status"] == "passed"
    assert audit["warnings"] == [] and audit["review_issues"] == []
    assert audit["checks"] == {"scoring_criteria": "passed", "criteria_consistency": "passed",
                               "missing_information": "passed", "sensitive_data_usage": "passed"}
    assert audit["candidate_id"] == "T001"
    assert "does not prove" in audit["explanation"]                # never claims fairness


def test_audit_statuses_are_only_the_documented_ones():
    for record in get_sample_candidates():
        _, _, audit, _ = _pipeline(record)
        assert audit["audit_status"] in AUDIT_STATUSES


def test_audit_distinguishes_missing_not_demonstrated_and_demonstrated():
    # No skills data -> "unknown"; skills given but no SQL -> "not demonstrated"; SQL present -> "demonstrated".
    _, _, none_given, _ = _pipeline({"candidate_id": "A", "name": "n", "experience_years": 1})
    assert none_given["qualification_evidence"]["SQL"] == "unknown_missing_information"
    assert none_given["audit_status"] == "warning"
    _, _, no_sql, _ = _pipeline(_candidate(skills=["ETL", "Python"]))
    assert no_sql["qualification_evidence"]["SQL"] == "not_demonstrated"
    assert no_sql["qualification_evidence"]["ETL"] == "demonstrated"
    assert no_sql["audit_status"] == "passed"                       # a missing skill is not a fairness issue


def test_missing_optional_information_is_a_note_not_a_warning():
    _, _, audit, _ = _pipeline(_candidate(certifications=[], education=None))
    assert audit["audit_status"] == "passed"
    assert any("Optional information not provided" in n for n in audit["notes"])


def test_audit_flags_unexpected_or_missing_criteria_in_the_scoring_result():
    parsed, match, _, job = _pipeline(_candidate())
    extra = deepcopy(match); extra["criterion_scores"]["Hobbies"] = 5
    assert audit_candidate(extra, parsed, job)["checks"]["criteria_consistency"] == "review_required"
    short = deepcopy(match); del short["criterion_scores"]["ETL"]
    audit = audit_candidate(short, parsed, job)
    assert audit["audit_status"] == "review_required"
    assert any("missing from the scoring result" in m for m in audit["review_issues"])
    partial_credit = deepcopy(match); partial_credit["criterion_scores"]["SQL"] = 10   # rule is 0 or full weight
    assert audit_candidate(partial_credit, parsed, job)["audit_status"] == "review_required"


def test_audit_flags_sensitive_keys_found_in_the_parsed_candidate():
    parsed, match, _, job = _pipeline(_candidate())
    parsed["religion"] = "x"
    audit = audit_candidate(match, parsed, job)
    assert audit["checks"]["sensitive_data_usage"] == "warning"
    assert audit["audit_status"] == "warning"


def test_audit_returns_error_status_for_malformed_input():
    for args in ((None, {}, {}), ({}, None, {}), ({}, {}, None), ("x", "y", "z")):
        audit = audit_candidate(*args)
        assert audit["audit_status"] == "error"
        assert audit["review_issues"]


def test_input_scan_only_flags_whole_word_sensitive_fields():
    raw = {"candidate_id": "x", "age": 1, "Marital_Status": "s", "date_of_birth": "d", "language": "en",
           "percentage": 80, "skills": []}
    assert scan_input_for_sensitive_fields(raw) == ["Marital_Status", "age", "date_of_birth"]
    assert scan_input_for_sensitive_fields("not a dict") == []
    audit = {"audit_status": "passed", "warnings": [], "checks": {}, "explanation": "ok"}
    assert apply_input_scan(audit, [])["audit_status"] == "passed"
    updated = apply_input_scan(audit, ["age"])
    assert updated["audit_status"] == "warning" and len(updated["warnings"]) == 1
    assert audit["warnings"] == []                                   # the original was not modified


# ---- Decision Validator -----------------------------------------------------
def test_validator_passes_a_correct_result_with_zero_difference():
    parsed, match, audit, job = _pipeline(_candidate())
    v = validate_decision(audit, match, parsed, job)
    assert v["validation_status"] == "validated"
    assert v["checks"] == {"candidate_identity": "passed", "score_range": "passed", "score_recalculation": "passed",
                           "criteria_consistency": "passed", "missing_information": "passed",
                           "bias_audit_outcome": "passed"}
    assert v["recalculated_score"] == 100 and v["score_difference"] == 0
    assert v["errors"] == [] and v["warnings"] == [] and v["human_review_required"] is False


def test_controlled_injection_every_altered_score_is_detected():
    # TEST DATA (not a normal result): alter each sample candidate's score once, in a way that makes it
    # different from the real score, and count how many alterations the validator detects. Expect 8 of 8.
    from workflow import make_score_tamperer
    real = {r["candidate_id"]: r["match"]["match_score"]
            for r in run_workflow(get_sample_job(), get_sample_candidates())["candidate_results"]}
    detected = 0
    for cid, score in real.items():
        altered = score + 7 if score <= 90 else score - 7           # always a different, in-range number
        result = run_workflow(get_sample_job(), get_sample_candidates(),
                              {"match_candidate": make_score_tamperer(cid, altered)})
        record = [r for r in result["candidate_results"] if r["candidate_id"] == cid][0]
        if record["validation"]["validation_status"] == "validation_failed" and record["validation"]["score_difference"] == altered - score:
            detected += 1
    assert detected == len(real) == 8


def test_validator_does_not_trust_a_wrong_criterion_list_even_if_the_total_is_right():
    parsed, match, audit, job = _pipeline(_candidate(skills=["SQL"], projects=[]))   # SQL only = 25
    match["matched_required_skills"] = ["SQL", "ETL"]                 # wrong list, right total
    v = validate_decision(audit, match, parsed, job)
    assert v["score_difference"] == 0
    assert v["validation_status"] == "validation_failed"
    assert any("matched required skills differ" in e for e in v["errors"])


def test_score_tolerance_is_small_but_real():
    # Candidate without SQL scores 75. A difference of 0.004 is inside the 0.01 rounding tolerance;
    # a difference of 0.5 is not. (Scores above 100 are rejected by the range check, not the tolerance.)
    parsed, match, audit, job = _pipeline(_candidate(skills=["ETL", "Data Modeling", "Data Warehousing", "Python"]))
    assert match["match_score"] == 75
    ok = deepcopy(match); ok["match_score"] = 75.004; ok["criterion_scores"]["ETL"] = 20.004
    assert validate_decision(audit, ok, parsed, job)["validation_status"] == "validated"
    bad = deepcopy(match); bad["match_score"] = 75.5
    assert validate_decision(audit, bad, parsed, job)["validation_status"] == "validation_failed"
    too_high = deepcopy(match); too_high["match_score"] = 100.004
    assert validate_decision(audit, too_high, parsed, job)["checks"]["score_range"] == "failed"


def test_missing_audit_result_or_audit_error_blocks_approval():
    parsed, match, audit, job = _pipeline(_candidate())
    assert validate_decision(None, match, parsed, job)["validation_status"] == "validation_failed"
    err = dict(audit, audit_status="error")
    assert validate_decision(err, match, parsed, job)["validation_status"] == "validation_failed"
    unknown = dict(audit, audit_status="mystery")
    assert validate_decision(unknown, match, parsed, job)["validation_status"] == "validation_failed"


def test_serious_audit_finding_requires_review_and_warning_only_continues():
    parsed, match, audit, job = _pipeline(_candidate())
    serious = dict(audit, audit_status="review_required", review_issues=["something serious"])
    v = validate_decision(serious, match, parsed, job)
    assert v["validation_status"] == "review_required"
    assert v["human_review_required"] is True
    assert any("something serious" in r for r in v["review_reasons"])
    mild = dict(audit, audit_status="warning", warnings=["something mild"])
    v = validate_decision(mild, match, parsed, job)
    assert v["validation_status"] == "validated_with_warnings"
    assert v["human_review_required"] is False
    assert any("something mild" in w for w in v["warnings"])


def test_hidden_missing_information_is_a_hard_failure():
    # The Parser result claims nothing is missing although skills are empty -> the gap is hidden.
    parsed, match, audit, job = _pipeline(_candidate(skills=[]))
    parsed["missing_fields"] = []
    parsed["invalid_fields"] = []
    v = validate_decision(audit, match, parsed, job)
    assert v["checks"]["missing_information"] == "failed"
    assert v["validation_status"] == "validation_failed"


def test_experience_below_the_posting_minimum_is_a_visible_warning_only():
    parsed, match, audit, job = _pipeline(_candidate(experience_years=0.5))
    v = validate_decision(audit, match, parsed, job)
    assert v["validation_status"] == "validated_with_warnings"
    assert match["match_score"] == 100                                # experience is not scored
    assert any("not scored" in w for w in v["warnings"])


def test_validator_handles_a_skipped_match_without_crashing():
    job = get_sample_job()
    parsed = parse_candidate("not a dictionary")
    match = match_candidate(parsed, job)
    v = validate_decision(None, match, parsed, job)
    assert v["validation_status"] == "validation_failed"
    assert v["checks"]["score_range"] == "failed"
    assert v["recalculated_score"] is None or isinstance(v["recalculated_score"], (int, float))


def test_validator_returns_only_documented_statuses_and_no_hiring_decisions():
    for record in get_sample_candidates():
        parsed, match, audit, job = _pipeline(record)
        v = validate_decision(audit, match, parsed, job)
        assert v["validation_status"] in VALIDATION_STATUSES
        assert set(v) >= {"candidate_id", "validation_status", "checks", "recalculated_score", "score_difference",
                          "errors", "warnings", "human_review_required", "explanation"}
        text = (v["explanation"] + " ".join(v["warnings"] + v["errors"])).lower()
        assert "unqualified" not in text and "hire" not in text


# ---- Report Generator / helpers --------------------------------------------
def test_outcome_and_summary_are_consistent():
    result = run_workflow(get_sample_job(), get_sample_candidates())
    summary = summarize_candidate_results(result["candidate_results"])
    assert summary == result["summary"]
    assert summary["validated_candidates"] + summary["candidates_requiring_review"] == summary["total_candidates"]
    assert summary["validation_failures"] <= summary["candidates_requiring_review"]
    assert candidate_outcome({})["validation_status"] == "not_run"
    assert candidate_outcome({"validation": {"validation_status": "validated", "human_review_required": False}})["routing"] == "report"
    flagged = candidate_outcome({"validation": {"validation_status": "validated", "human_review_required": False},
                                 "review_reasons": ["Duplicate candidate_id"]})
    assert flagged["human_review_required"] is True                  # a workflow flag overrides "validated"


def test_workflow_rejects_unknown_component_overrides():
    failed = False
    try:
        run_workflow(get_sample_job(), get_sample_candidates(), functions={"not_a_component": len})
    except ValueError:
        failed = True
    assert failed


def test_report_scores_are_never_presented_as_probabilities():
    report = generate_report({"job": get_sample_job(), "candidate_results": run_workflow(
        get_sample_job(), get_sample_candidates())["candidate_results"]})
    text = " ".join(str(v) for row in report["rows"] for v in row.values()).lower()
    assert "probability" not in text and "unqualified" not in text
    assert "not a prediction of job performance" in report["disclaimer"].lower()
