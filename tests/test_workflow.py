"""
tests/test_workflow.py - integration and validation tests for User 2's part.

Run from the RecruitAI folder:   python -m pytest -q
Plain asserts only. Every test says what we expect. The numbers in the comments
(1-17) match the numbered list in the CA-3 task description.

Honest notes
  * "Deliberately injected errors" (tampered scores, bad weights, ...) are TEST data,
    not normal demonstration results.
  * test 16 shows that candidate NAMES do not change scores. It does not prove the
    whole system is unbiased.
"""

import csv
import io
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))     # make `import workflow` work wherever pytest starts

from agents import match_candidate, parse_candidate
from agents_part2 import (
    REPORT_COLUMNS, ReportExportError, audit_candidate, generate_report, report_to_csv, validate_decision,
)
from dataset import get_sample_candidates, get_sample_job
from schemas import WORKFLOW_TASKS
from view_tables import (
    RESULT_TABLE_COLUMNS, audit_table, candidates_table, issues_table, match_table, parse_table,
    results_table, review_reasons_table, validation_table, criterion_table,
)
from workflow import make_score_tamperer, run_workflow

EXPECTED_SCORES = {"C001": 100, "C002": 75, "C003": 85, "C004": 90,
                   "C005": 50, "C006": 10, "C007": 65, "C008": 60}


_DEFAULT = object()


def _run(candidates=_DEFAULT, job=_DEFAULT, functions=None):
    """Run the workflow. Anything not given falls back to the real sample job/candidates
    (so an explicit job={} or candidates=[] really is passed through)."""
    return run_workflow(get_sample_job() if job is _DEFAULT else job,
                        get_sample_candidates() if candidates is _DEFAULT else candidates, functions)


def _by_id(result):
    return {r["candidate_id"]: r for r in result["candidate_results"]}


def _candidate(**overrides):
    base = {"candidate_id": "T001", "name": "Test Candidate", "education": "B.Tech",
            "skills": ["SQL", "ETL", "Data Modeling", "Data Warehousing", "Python"],
            "experience_years": 2, "projects": ["ETL pipeline in Python"], "certifications": []}
    base.update(overrides)
    return base


# ---- 1. complete successful workflow --------------------------------------
def test_01_complete_successful_workflow():
    # Expect: plan ready, every task logged in order, all 8 candidates processed,
    # the validator recalculates exactly the scores User 1's Matcher produced.
    result = _run()
    assert result["workflow_status"] == "completed"
    assert result["errors"] == []
    assert result["execution_plan"]["status"] == "ready"
    assert [e["task"] for e in result["execution_log"]] == ["plan_workflow"] + WORKFLOW_TASKS
    assert all(e["status"] in ("ready", "completed") for e in result["execution_log"])
    by_id = _by_id(result)
    assert {cid: r["match"]["match_score"] for cid, r in by_id.items()} == EXPECTED_SCORES
    for r in by_id.values():
        assert r["validation"]["recalculated_score"] == r["match"]["match_score"]
        assert r["validation"]["score_difference"] == 0
        assert set(r["steps"]) == {"parse", "match", "audit", "validate"}
    assert result["summary"] == {"total_candidates": 8, "validated_candidates": 7,
                                 "candidates_requiring_review": 1, "validation_failures": 0}
    assert result["report"]["report_status"] == "generated"
    assert len(result["report"]["rows"]) == 8


# ---- 2. invalid job configuration -----------------------------------------
def test_02_invalid_job_configuration_stops_the_workflow():
    # Expect: stopped, the planner's error is shown, nothing processed, no report invented.
    job = get_sample_job()
    job["skill_weights"]["SQL"] = 15          # weights now sum to 90
    result = _run(job=job)
    assert result["workflow_status"] == "stopped"
    assert any("sum" in e for e in result["errors"])
    assert result["candidate_results"] == []
    assert result["report"] is None
    assert result["summary"]["total_candidates"] == 0
    # a missing job and a job without source metadata are stopped too (no crash)
    assert _run(job={})["workflow_status"] == "stopped"
    job2 = get_sample_job()
    del job2["source_url"]
    assert _run(job=job2)["workflow_status"] == "stopped"
    assert run_workflow(None, get_sample_candidates())["workflow_status"] == "stopped"


# ---- 3. empty candidate list ----------------------------------------------
def test_03_empty_candidate_list_stops_the_workflow():
    result = _run(candidates=[])
    assert result["workflow_status"] == "stopped"
    assert result["errors"] != []
    assert result["report"] is None
    assert run_workflow(get_sample_job(), None)["workflow_status"] == "stopped"


# ---- 4. candidate with missing information --------------------------------
def test_04_missing_information_goes_to_human_review_not_rejection():
    # C006 has no skills and experience_years = "about two".
    c6 = _by_id(_run())["C006"]
    assert c6["parsed"]["parsing_status"] == "partial"
    assert c6["audit"]["checks"]["missing_information"] == "warning"
    assert c6["validation"]["validation_status"] == "review_required"
    assert c6["validation"]["human_review_required"] is True
    assert c6["validation"]["errors"] == []                       # a review, not a failure
    assert c6["outcome"]["routing"] == "human_review"
    # Missing skills data is "unknown", NOT "not demonstrated"
    evidence = c6["audit"]["qualification_evidence"]
    assert evidence["SQL"] == "unknown_missing_information"
    assert evidence["relevant_project"] == "demonstrated"          # its project mentions ETL
    # the explanation tells the recruiter this is not evidence of missing skill
    assert "not evidence" in c6["match"]["explanation"]


# ---- 5. candidate with a missing required skill ---------------------------
def test_05_missing_required_skill_is_reported_and_not_a_failure():
    # C002 has no SQL: score 75, SQL listed as missing, still validated (a low score is not a failure).
    c2 = _by_id(_run())["C002"]
    assert c2["match"]["missing_required_skills"] == ["SQL"]
    assert c2["match"]["match_score"] == 75
    assert c2["audit"]["qualification_evidence"]["SQL"] == "not_demonstrated"
    assert c2["validation"]["validation_status"] == "validated"
    assert c2["outcome"]["human_review_required"] is False
    row = [r for r in _run()["report"]["rows"] if r["candidate_id"] == "C002"][0]
    assert row["missing_required_skills"] == "SQL"
    assert "unqualified" not in " ".join(str(v).lower() for v in row.values())


# ---- 6. duplicate or inconsistent candidate IDs ---------------------------
def test_06a_inconsistent_ids_between_agent_results_fail_validation():
    job = get_sample_job()
    parsed = parse_candidate(_candidate())
    match = match_candidate(parsed, job)
    audit = audit_candidate(match, parsed, job)
    assert validate_decision(audit, match, parsed, job)["validation_status"] == "validated"
    for which in ("audit", "match", "parsed"):
        a, m, p = deepcopy(audit), deepcopy(match), deepcopy(parsed)
        {"audit": a, "match": m, "parsed": p}[which]["candidate_id"] = "OTHER"
        v = validate_decision(a, m, p, job)
        assert v["validation_status"] == "validation_failed", which
        assert v["checks"]["candidate_identity"] == "failed"
        assert v["human_review_required"] is True
    # a missing ID in an agent result also fails
    m2 = deepcopy(match); m2["candidate_id"] = None
    assert validate_decision(audit, m2, parsed, job)["checks"]["candidate_identity"] == "failed"


def test_06b_matcher_returning_the_wrong_id_is_caught_in_the_workflow():
    def wrong_id_matcher(parsed, job):
        result = match_candidate(parsed, job)
        if parsed["candidate_id"] == "C004":
            result = dict(result, candidate_id="C999")
        return result
    result = _run(functions={"match_candidate": wrong_id_matcher})
    c4 = _by_id(result)["C004"]
    assert c4["validation"]["validation_status"] == "validation_failed"
    assert c4["outcome"]["routing"] == "human_review"
    assert result["summary"]["validation_failures"] == 1


def test_06c_duplicate_input_ids_are_flagged_for_review():
    a, b = _candidate(candidate_id="DUP"), _candidate(candidate_id="DUP", name="Other Copy")
    result = _run(candidates=[a, b, _candidate(candidate_id="OK1")])
    flagged = [r for r in result["candidate_results"] if r["candidate_id"] == "DUP"]
    assert len(flagged) == 2
    for r in flagged:
        assert r["outcome"]["human_review_required"] is True
        assert any("Duplicate candidate_id" in reason for reason in r["outcome"]["reasons"])
    assert _by_id(result)["OK1"]["outcome"]["human_review_required"] is False


# ---- 7. deliberately altered match score ----------------------------------
def test_07_altered_match_score_is_detected_by_the_independent_validator():
    # TEST DATA: the score of C002 is changed from 75 to 99 AFTER the real Matcher ran.
    result = _run(functions={"match_candidate": make_score_tamperer("C002", 99)})
    c2 = _by_id(result)["C002"]
    v = c2["validation"]
    assert c2["match"]["match_score"] == 99
    assert v["recalculated_score"] == 75
    assert v["score_difference"] == 24
    assert v["validation_status"] == "validation_failed"
    assert v["checks"]["score_recalculation"] == "failed"
    assert any("Score discrepancy" in e for e in v["errors"])
    assert result["metadata"]["overridden_functions"] == ["match_candidate"]
    # the other seven candidates are not affected
    assert result["summary"]["validation_failures"] == 1
    assert all(r["validation"]["validation_status"] != "validation_failed"
               for cid, r in _by_id(result).items() if cid != "C002")


def test_07b_changing_one_criterion_while_keeping_the_total_is_also_detected():
    job = get_sample_job()
    parsed = parse_candidate(_candidate())
    match = match_candidate(parsed, job)
    match["criterion_scores"]["SQL"], match["criterion_scores"]["ETL"] = 20, 25   # total still 100
    audit = audit_candidate(match, parsed, job)
    v = validate_decision(audit, match, parsed, job)
    assert v["score_difference"] == 0
    assert v["validation_status"] == "validation_failed"
    assert any("criterion 'SQL'" in e for e in v["errors"])


# ---- 8. invalid score greater than 100 ------------------------------------
def test_08_invalid_scores_are_flagged():
    job = get_sample_job()
    parsed = parse_candidate(_candidate())
    for bad in (150, 100.5, -1, "80", None, True, float("nan")):
        match = match_candidate(parsed, job)
        match["match_score"] = bad
        audit = audit_candidate(match, parsed, job)
        v = validate_decision(audit, match, parsed, job)
        assert v["checks"]["score_range"] == "failed", bad
        assert v["validation_status"] == "validation_failed", bad
        assert v["human_review_required"] is True


# ---- 9. invalid criterion weights -----------------------------------------
def test_09_invalid_weights_fail_validation():
    parsed = parse_candidate(_candidate())
    good_job = get_sample_job()
    match = match_candidate(parsed, good_job)
    audit = audit_candidate(match, parsed, good_job)

    bad_sum = get_sample_job(); bad_sum["skill_weights"]["SQL"] = 15
    v = validate_decision(audit, match, parsed, bad_sum)
    assert v["validation_status"] == "validation_failed"
    assert any("sum" in e for e in v["errors"])

    negative = get_sample_job(); negative["skill_weights"]["SQL"] = -5
    assert validate_decision(audit, match, parsed, negative)["validation_status"] == "validation_failed"

    preferred = get_sample_job(); preferred["skill_weights"]["Spark"] = 5; preferred["skill_weights"]["SQL"] = 20
    v = validate_decision(audit, match, parsed, preferred)       # sums to 100 but scores a preferred skill
    assert v["validation_status"] == "validation_failed"
    assert any("preferred" in e for e in v["errors"])

    unexpected = get_sample_job(); unexpected["skill_weights"]["Age"] = 10; unexpected["skill_weights"]["SQL"] = 15
    v = validate_decision(audit, match, parsed, unexpected)
    assert v["validation_status"] == "validation_failed"

    # an unexpected criterion inside the match result cannot push the total above 100 unnoticed
    match2 = deepcopy(match); match2["criterion_scores"]["Bonus"] = 20; match2["match_score"] = 120
    v = validate_decision(audit_candidate(match2, parsed, good_job), match2, parsed, good_job)
    assert v["validation_status"] == "validation_failed"
    assert any("unexpected criteria" in e for e in v["errors"])


# ---- 10. bias auditor warning ---------------------------------------------
def test_10_bias_auditor_warns_about_sensitive_input_fields_and_does_not_use_them():
    # Sensitive keys in the RAW input are ignored by the parser; the auditor reports them.
    plain = _candidate()
    sensitive = _candidate(age=29, gender="x", photo="me.jpg", marital_status="single")
    base = _by_id(_run(candidates=[plain]))["T001"]
    flagged = _by_id(_run(candidates=[sensitive]))["T001"]
    assert flagged["match"]["match_score"] == base["match"]["match_score"] == 100     # not used for scoring
    assert flagged["audit"]["audit_status"] == "warning"
    assert flagged["audit"]["checks"]["sensitive_data_usage"] == "warning"
    assert sorted(flagged["sensitive_input_fields"]) == ["age", "gender", "marital_status", "photo"]
    assert flagged["validation"]["validation_status"] == "validated_with_warnings"
    assert flagged["outcome"]["human_review_required"] is False       # a warning, not a block
    assert base["audit"]["audit_status"] == "passed"


def test_10b_bias_auditor_flags_an_inappropriate_scoring_criterion():
    job = get_sample_job()
    job["skill_weights"]["Age"] = 10
    job["skill_weights"]["SQL"] = 15
    parsed = parse_candidate(_candidate())
    match = match_candidate(parsed, get_sample_job())
    audit = audit_candidate(match, parsed, job)
    assert audit["audit_status"] == "review_required"
    assert audit["checks"]["scoring_criteria"] == "review_required"
    assert any("sensitive" in m for m in audit["review_issues"])
    # the validator does not silently approve it
    v = validate_decision(audit, match, parsed, job)
    assert v["validation_status"] != "validated"


# ---- 11. validation failure leading to human review -----------------------
def test_11_validation_failure_routes_to_human_review_and_the_report_shows_it():
    result = _run(functions={"match_candidate": make_score_tamperer("C005", 150)})
    c5 = _by_id(result)["C005"]
    assert c5["validation"]["validation_status"] == "validation_failed"
    assert c5["outcome"]["routing"] == "human_review"
    row = [r for r in result["report"]["rows"] if r["candidate_id"] == "C005"][0]
    assert row["validation_status"] == "validation_failed"
    assert row["human_review_required"] is True
    assert row["routing"] == "human_review"
    assert "score_range" in row["validation_errors"]
    assert row["explanation"].startswith("HUMAN REVIEW REQUIRED")
    assert "C005" in result["report"]["held_for_human_review"]
    assert "C005" not in result["report"]["approved_for_report"]
    assert result["summary"]["candidates_requiring_review"] == 2     # C005 (failed) + C006 (missing info)


# ---- 12. successful validation leading to normal report processing --------
def test_12_successful_validation_leads_to_the_normal_report():
    result = _run()
    c1 = _by_id(result)["C001"]
    assert c1["validation"]["validation_status"] == "validated"
    assert c1["outcome"]["routing"] == "report"
    assert "C001" in result["report"]["approved_for_report"]
    assert "C001" not in result["report"]["held_for_human_review"]
    row = [r for r in result["report"]["rows"] if r["candidate_id"] == "C001"][0]
    assert row["human_review_required"] is False and row["routing"] == "report"
    assert result["report"]["held_for_human_review"] == ["C006"]


# ---- 13. one failed candidate must not crash the batch --------------------
def test_13a_bad_records_do_not_stop_the_other_candidates():
    batch = [_candidate(candidate_id="G1"), "not a dictionary", {"name": "No Id"},
             _candidate(candidate_id="G2"), {"candidate_id": "P1"}]
    result = _run(candidates=batch)
    assert result["workflow_status"] == "completed"
    assert len(result["candidate_results"]) == 5
    by_id = _by_id(result)
    assert by_id["G1"]["validation"]["validation_status"] == "validated"
    assert by_id["G2"]["validation"]["validation_status"] == "validated"
    failed = [r for r in result["candidate_results"] if r["parsed"]["parsing_status"] == "failed"]
    assert len(failed) == 2
    for r in failed:                          # never matched, audited, validated or scored
        assert r["match"] is None and r["audit"] is None and r["validation"] is None
        assert r["steps"]["match"]["status"] == "skipped"
        assert r["steps"]["audit"]["status"] == "skipped"
        assert r["steps"]["validate"]["status"] == "skipped"
        assert r["outcome"] == {"validation_status": "not_run", "human_review_required": True,
                                "routing": "human_review", "reasons": r["outcome"]["reasons"]}
        assert any("Parsing failed" in x for x in r["outcome"]["reasons"])
    assert by_id["P1"]["outcome"]["human_review_required"] is True       # partial record
    assert result["summary"] == {"total_candidates": 5, "validated_candidates": 2,
                                 "candidates_requiring_review": 3, "validation_failures": 0}
    assert len(result["report"]["rows"]) == 5


def test_13b_an_exception_in_one_agent_is_recorded_and_the_batch_continues():
    def raising_matcher(parsed, job):
        if parsed["candidate_id"] == "C003":
            raise RuntimeError("boom")
        return match_candidate(parsed, job)
    result = _run(functions={"match_candidate": raising_matcher})
    assert result["workflow_status"] == "completed_with_errors"
    assert any("C003" in e and "boom" in e for e in result["errors"])
    c3 = _by_id(result)["C003"]
    assert c3["match"] is None and c3["validation"] is None
    assert c3["outcome"]["validation_status"] == "not_run"
    assert c3["outcome"]["human_review_required"] is True
    others = [r for cid, r in _by_id(result).items() if cid not in ("C003", "C006")]
    assert all(r["validation"]["validation_status"] in ("validated", "validated_with_warnings") for r in others)
    assert len(result["report"]["rows"]) == 8


def test_13c_a_missing_audit_result_is_never_silently_approved():
    def broken_auditor(match, parsed, job):
        raise ValueError("auditor crashed")
    result = _run(functions={"audit_candidate": broken_auditor})
    assert result["workflow_status"] == "completed_with_errors"
    for r in result["candidate_results"]:
        assert r["validation"]["validation_status"] == "validation_failed"
        assert r["validation"]["checks"]["bias_audit_outcome"] == "failed"


# ---- 14. report generation with multiple candidates -----------------------
def test_14_report_has_every_required_field_for_multiple_candidates():
    report = _run()["report"]
    assert report["columns"] == REPORT_COLUMNS
    assert report["job"]["company"] == "Amazon" and report["job"]["job_title"] == "Data Engineer I"
    assert len(report["rows"]) == 8
    for row in report["rows"]:
        assert list(row) == REPORT_COLUMNS
        assert row["company"] == "Amazon" and row["job_title"] == "Data Engineer I"
    by_id = {r["candidate_id"]: r for r in report["rows"]}
    c8 = by_id["C008"]
    assert c8["candidate_name"] == "Synthetic Candidate H"
    assert c8["match_score"] == 60
    assert c8["matched_required_skills"] == "ETL; Data Warehousing; Scripting Language"
    assert c8["missing_required_skills"] == "SQL; Data Modeling"
    assert c8["matched_preferred_skills"] == "ETL Tool"
    assert c8["criterion_scores"] == "SQL=0; ETL=20; Data Modeling=0; Data Warehousing=15; Scripting Language=15; relevant_project=10"
    assert c8["audit_status"] == "passed" and c8["validation_status"] == "validated"
    assert "not a hiring decision" in c8["screening_note"].lower()
    assert report["summary"] == _run()["summary"]
    assert "not a prediction of job performance" in report["disclaimer"].lower()


def test_14b_report_does_not_invent_results_for_missing_agent_output():
    report = generate_report({"job": get_sample_job(), "candidate_results": [{"input_position": 1}, "junk"]})
    assert len(report["rows"]) == 2
    for row in report["rows"]:
        assert row["validation_status"] == "not_run"
        assert row["human_review_required"] is True and row["routing"] == "human_review"
        assert row["match_score"] is None
    assert generate_report(None)["rows"] == []


# ---- 15. CSV export with consistent columns -------------------------------
def test_15_csv_has_consistent_columns_even_for_messy_candidates():
    messy = [_candidate(candidate_id="A1"), {"candidate_id": "B2"}, "junk", {"name": "no id"},
             _candidate(candidate_id="C3", experience_years="two", projects=None)]
    for batch in (get_sample_candidates(), messy):
        report = _run(candidates=batch)["report"]
        text = report_to_csv(report)
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == REPORT_COLUMNS
        assert len(rows) - 1 == len(batch)
        assert all(len(r) == len(REPORT_COLUMNS) for r in rows)
        for r in rows[1:]:                                  # nothing nested was dumped into a cell
            assert not any(cell.startswith("{") or cell.startswith("[") for cell in r)
    failure = None
    try:
        report_to_csv(None)
    except ReportExportError as exc:
        failure = str(exc)
    assert failure and "no rows" in failure


# ---- 16. candidate names do not affect scores -----------------------------
def test_16_candidate_names_do_not_change_scores_or_validation():
    original = _run()
    renamed_input = get_sample_candidates()
    for i, c in enumerate(renamed_input):
        c["name"] = f"Completely Different Name {i * 7}"
    renamed = _run(candidates=renamed_input)
    for cid, record in _by_id(original).items():
        other = _by_id(renamed)[cid]
        assert record["match"]["match_score"] == other["match"]["match_score"]
        assert record["match"]["criterion_scores"] == other["match"]["criterion_scores"]
        assert record["validation"]["recalculated_score"] == other["validation"]["recalculated_score"]
        assert record["validation"]["validation_status"] == other["validation"]["validation_status"]


# ---- 17. UI execution using the integrated workflow -----------------------
def test_17a_ui_tables_are_built_from_the_real_workflow_result():
    # app.py only displays these tables; building them here exercises the same code without Streamlit.
    candidates = get_sample_candidates()
    result = _run(candidates=candidates)
    records, job = result["candidate_results"], result["job"]
    assert len(candidates_table(candidates)) == 8                  # mixed text/number cells are all text
    table = results_table(result["report"])
    assert list(table.columns) == RESULT_TABLE_COLUMNS
    assert table["Match Score"].tolist() == [EXPECTED_SCORES[c["candidate_id"]] for c in candidates]
    assert int(table["Human Review Required"].sum()) == result["summary"]["candidates_requiring_review"]
    assert len(parse_table(records)) == len(match_table(records)) == len(audit_table(records)) == 8
    assert len(validation_table(records)) == 8
    assert len(criterion_table(records[0], job)) == 6
    issues = issues_table(records)
    assert "C006" in issues["candidate_id"].tolist()
    assert "Missing information" in issues["category"].tolist()
    reasons = review_reasons_table(records)
    assert reasons["candidate_id"].tolist() == ["C006"]
    # stopped workflow: the UI must still be able to show something
    assert issues_table([]).empty and review_reasons_table([]).empty


def test_17b_tampered_run_shows_a_validation_failure_in_the_ui_tables():
    result = _run(functions={"match_candidate": make_score_tamperer("C001", 50)})
    issues = issues_table(result["candidate_results"])
    assert "Score discrepancy" in issues["category"].tolist()
    table = results_table(result["report"])
    assert table.loc[table["Candidate ID"] == "C001", "Validation Status"].iloc[0] == "validation_failed"


def test_17c_streamlit_app_runs_end_to_end():
    # Needs Streamlit (pip install -r requirements.txt). Skipped, not failed, if it is missing.
    try:
        from streamlit.testing.v1 import AppTest
    except ImportError:
        raise unittest.SkipTest("streamlit is not installed")
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception
    at.button(key="load_btn").click().run()
    at.button(key="run_btn").click().run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Total Candidates"] == "8"
    assert metrics["Validated Candidates"] == "7"
    assert metrics["Candidates Requiring Review"] == "1"
    assert metrics["Validation Failures"] == "0"
