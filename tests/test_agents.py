"""
tests/test_agents.py - unit tests for User 1's components.

Run from the RecruitAI folder:   python -m pytest -v
Each test says what we expect. Plain asserts only, no special tricks.

Honest note: one fairness test (name does not change the score) shows only that
the NAME is not used in scoring. It does not prove the whole system is unbiased.
"""

import sys
from pathlib import Path

# make `import agents` work no matter where pytest is started from
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents import match_candidate, normalize_skill, parse_candidate, plan_workflow, validate_job_config
from dataset import get_sample_candidates, get_sample_job


def _candidate(**overrides):
    """A complete synthetic candidate that matches every criterion."""
    base = {
        "candidate_id": "T001",
        "name": "Test Candidate",
        "education": "B.Tech",
        "skills": ["SQL", "ETL", "Data Modeling", "Data Warehousing", "Python"],
        "experience_years": 2,
        "projects": ["ETL pipeline in Python"],
        "certifications": [],
    }
    base.update(overrides)
    return base


def _score(candidate, job=None):
    job = job or get_sample_job()
    return match_candidate(parse_candidate(candidate), job)


# ---- 1. valid job configuration ------------------------------------------
def test_valid_job_configuration_has_no_errors():
    # Expect: the sample job (from the real Amazon posting) passes validation.
    assert validate_job_config(get_sample_job()) == []


# ---- 2. invalid job configuration ----------------------------------------
def test_invalid_job_configuration_is_reported():
    # Expect: empty/None/incomplete jobs give errors and the plan is not ready.
    assert validate_job_config({}) != []
    assert validate_job_config(None) != []
    job = get_sample_job()
    job["required_skills"] = []
    assert validate_job_config(job) != []
    assert plan_workflow(job, get_sample_candidates())["status"] == "invalid_input"


# ---- 3. missing required candidate information ---------------------------
def test_missing_required_candidate_information_is_flagged():
    # Expect: no name/skills/experience -> listed in missing_fields, status "partial".
    parsed = parse_candidate({"candidate_id": "X1"})
    assert parsed["parsing_status"] == "partial"
    for field in ["name", "skills", "experience_years"]:
        assert field in parsed["missing_fields"]
    # No candidate_id at all -> "failed" (we cannot track the record)
    assert parse_candidate({"name": "No Id"})["parsing_status"] == "failed"


# ---- 4. empty candidate list ---------------------------------------------
def test_empty_candidate_list_makes_plan_invalid():
    # Expect: not ready, no tasks, and an error message.
    plan = plan_workflow(get_sample_job(), [])
    assert plan["status"] == "invalid_input"
    assert plan["tasks"] == []
    assert plan["errors"] != []
    assert plan_workflow(get_sample_job(), None)["status"] == "invalid_input"


# ---- 5. invalid skill weights --------------------------------------------
def test_weights_not_summing_to_100_block_the_plan():
    # Expect: weights adding to 90 -> plan not ready and the error mentions the sum.
    job = get_sample_job()
    job["skill_weights"]["SQL"] = 15          # total becomes 90
    plan = plan_workflow(job, get_sample_candidates())
    assert plan["status"] == "invalid_input"
    assert any("sum" in error for error in plan["errors"])
    # Negative weight is also rejected
    job2 = get_sample_job()
    job2["skill_weights"]["SQL"] = -5
    assert validate_job_config(job2) != []


def test_valid_inputs_give_a_ready_plan_with_all_tasks():
    # Expect: ready plan, 5 tasks in order, candidate_count = 8.
    plan = plan_workflow(get_sample_job(), get_sample_candidates())
    assert plan["status"] == "ready"
    assert plan["tasks"] == ["parse_candidates", "match_candidates", "audit_candidates",
                             "validate_results", "generate_report"]
    assert plan["candidate_count"] == 8
    assert plan["errors"] == []


# ---- 6. candidate matching all criteria ----------------------------------
def test_candidate_matching_all_criteria_scores_100():
    # Expect: every criterion earns its weight -> 100, nothing missing.
    result = _score(_candidate())
    assert result["match_score"] == 100
    assert result["missing_required_skills"] == []
    assert result["matching_status"] == "success"


# ---- 7. candidate missing a required skill -------------------------------
def test_candidate_missing_a_required_skill_loses_exactly_its_weight():
    # Expect: dropping SQL (weight 25) -> SQL listed as missing, score 75.
    result = _score(_candidate(skills=["ETL", "Data Modeling", "Data Warehousing", "Python"]))
    assert result["missing_required_skills"] == ["SQL"]
    assert result["criterion_scores"]["SQL"] == 0
    assert result["match_score"] == 75


# ---- 8. candidate without a relevant project -----------------------------
def test_candidate_without_relevant_project_gets_zero_project_points():
    # Expect: an unrelated project -> relevant_project = 0, score 90.
    result = _score(_candidate(projects=["Poster design for a college fest"]))
    assert result["criterion_scores"]["relevant_project"] == 0
    assert result["match_score"] == 90
    # An empty project list behaves the same
    assert _score(_candidate(projects=[]))["criterion_scores"]["relevant_project"] == 0


# ---- 9. case-insensitive skill normalization -----------------------------
def test_skill_normalization_ignores_case_and_spelling_variants():
    # Expect: different spellings map to one canonical name.
    assert normalize_skill("python") == "Python"
    assert normalize_skill("  PYTHON ") == "Python"
    assert normalize_skill("sql") == "SQL"
    assert normalize_skill("Data Modelling") == "Data Modeling"
    assert normalize_skill("data warehouse") == "Data Warehousing"
    assert normalize_skill("Korn Shell") == "KornShell"
    assert normalize_skill("Tableau") == "Tableau"      # unknown skills are kept, not invented/dropped
    parsed = parse_candidate(_candidate(skills=["python", "PYTHON", "Sql"]))
    assert parsed["skills"] == ["Python", "SQL"]        # duplicates removed
    # lowercase input scores the same as properly-cased input
    assert _score(_candidate(skills=["sql", "etl", "data modeling", "data warehousing", "python"]))["match_score"] == 100


# ---- 10. scores stay between 0 and 100 -----------------------------------
def test_scores_stay_between_0_and_100():
    # Expect: every sample candidate, plus an empty candidate, scores inside 0..100.
    job = get_sample_job()
    for record in get_sample_candidates() + [{"candidate_id": "EMPTY"}]:
        score = match_candidate(parse_candidate(record), job)["match_score"]
        assert 0 <= score <= 100


# ---- 11. consistent score calculation ------------------------------------
def test_same_input_gives_same_result_every_time():
    # Expect: running twice gives identical dictionaries (no randomness, no hidden state).
    first = _score(_candidate(skills=["SQL", "Python"]))
    second = _score(_candidate(skills=["SQL", "Python"]))
    assert first == second
    # Total always equals the sum of criterion scores
    assert first["match_score"] == sum(first["criterion_scores"].values())


# ---- 12. name does not change the score ----------------------------------
def test_changing_the_name_does_not_change_the_score():
    # Expect: only the name differs -> identical score and criterion scores.
    a = _score(_candidate(name="Synthetic Candidate A"))
    b = _score(_candidate(name="Totally Different Name"))
    assert a["match_score"] == b["match_score"]
    assert a["criterion_scores"] == b["criterion_scores"]


# ---- 13. job config has traceable source metadata ------------------------
def test_job_configuration_has_traceable_source_metadata():
    # Expect: company, exact title, official URL and access date are present.
    job = get_sample_job()
    assert job["company"] == "Amazon"
    assert job["job_title"] == "Data Engineer I"
    assert job["source_url"].startswith("https://amazon.jobs/")
    assert job["access_date"] == "2026-10-14"
    # If metadata is removed, validation fails
    del job["source_url"]
    assert validate_job_config(job) != []


# ---- 14. preferred skills are not added to the score ---------------------
def test_preferred_skills_are_reported_but_not_scored():
    # Expect: adding preferred skills changes the preferred list, not the score.
    base = _score(_candidate(skills=["SQL", "Python"]))
    extra = _score(_candidate(skills=["SQL", "Python", "Spark", "Hadoop", "Hive", "EMR"]))
    assert base["match_score"] == extra["match_score"]
    assert base["matched_preferred_skills"] == []
    assert extra["matched_preferred_skills"] == ["Hadoop", "Hive", "Spark", "EMR"]
    assert "Spark" not in extra["criterion_scores"]
    # A preferred skill given a weight is a configuration error
    job = get_sample_job()
    job["skill_weights"]["Spark"] = 5
    assert validate_job_config(job) != []


# ---- 15. missing information is represented explicitly -------------------
def test_missing_information_is_represented_explicitly():
    # Expect: absent values become None / [] and are named; nothing is invented.
    parsed = parse_candidate({"candidate_id": "X2", "name": "Only Name"})
    assert parsed["skills"] == []
    assert parsed["experience_years"] is None
    assert parsed["education"] is None
    assert "skills" in parsed["missing_fields"]
    assert "education" in parsed["missing_optional_fields"]
    # A missing optional field (certifications) does NOT make a complete record partial
    complete = parse_candidate(_candidate())
    assert "certifications" in complete["missing_optional_fields"]
    assert complete["parsing_status"] == "success"


# ---- extra tests ----------------------------------------------------------
def test_invalid_experience_values_are_flagged_not_guessed():
    # Expect: text, negative, boolean and absurd values are invalid and become None.
    for bad in ["two", -1, True, 999, float("nan")]:
        parsed = parse_candidate(_candidate(experience_years=bad))
        assert parsed["experience_years"] is None
        assert "experience_years" in parsed["invalid_fields"]
        assert parsed["parsing_status"] == "partial"


def test_non_dictionary_input_returns_a_structured_failure():
    # Expect: no crash; status "failed"; the matcher skips it with no score.
    parsed = parse_candidate("not a dictionary")
    assert parsed["parsing_status"] == "failed"
    result = match_candidate(parsed, get_sample_job())
    assert result["matching_status"] == "skipped"
    assert result["match_score"] is None


def test_matching_with_invalid_job_returns_failed_status():
    # Expect: bad weights -> matching_status "failed", no score, no crash.
    job = get_sample_job()
    job["skill_weights"]["SQL"] = 1
    result = match_candidate(parse_candidate(_candidate()), job)
    assert result["matching_status"] == "failed"
    assert result["match_score"] is None


def test_partial_record_is_scored_with_a_warning_not_rejected():
    # Expect: scored from available data, status "partial", explanation says it is not proof.
    result = _score({"candidate_id": "P1", "name": "Partial", "experience_years": 1,
                     "projects": ["ETL project"]})
    assert result["matching_status"] == "partial"
    assert result["match_score"] == 10
    assert "not evidence" in result["explanation"]


def test_scripting_language_is_satisfied_by_listed_examples():
    # Expect: KornShell alone satisfies "Scripting Language" (posting lists it as an example).
    result = _score(_candidate(skills=["SQL", "ETL", "Data Modeling", "Data Warehousing", "Korn Shell"]))
    assert "Scripting Language" in result["matched_required_skills"]
    assert result["match_score"] == 100


def test_unknown_extra_fields_such_as_age_are_ignored():
    # Expect: sensitive/irrelevant keys never reach the parsed output or the score.
    with_extra = _candidate(age=22, gender="x", photo="p.jpg")
    parsed = parse_candidate(with_extra)
    assert "age" not in parsed and "gender" not in parsed and "photo" not in parsed
    assert _score(with_extra)["match_score"] == _score(_candidate())["match_score"]


def test_explanation_shows_how_the_total_was_calculated():
    # Expect: explanation names matched/missing criteria and the final total.
    result = _score(_candidate(skills=["Python"], projects=[]))
    assert "SQL not matched: 0 points." in result["explanation"]
    assert "Scripting Language matched (via Python): 15 points." in result["explanation"]
    assert "Total: 15/100." in result["explanation"]


def test_sample_dataset_has_eight_varied_explainable_scores():
    # Expect: 8 synthetic candidates and the exact scores we designed.
    job = get_sample_job()
    candidates = get_sample_candidates()
    assert len(candidates) >= 8
    scores = {c["candidate_id"]: match_candidate(parse_candidate(c), job)["match_score"] for c in candidates}
    assert scores == {"C001": 100, "C002": 75, "C003": 85, "C004": 90,
                      "C005": 50, "C006": 10, "C007": 65, "C008": 60}
    assert len(set(scores.values())) == 8
    assert all(c["name"].startswith("Synthetic") for c in candidates)
