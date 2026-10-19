import io

import pytest

from candidate_upload import csv_template_text, parse_candidate_csv


def test_parses_template_into_agent_candidate_shape():
    candidates = parse_candidate_csv(io.BytesIO(csv_template_text().encode("utf-8")))
    assert len(candidates) == 1
    assert candidates[0]["candidate_id"] == "C101"
    assert candidates[0]["skills"] == ["SQL", "Python", "ETL"]
    assert candidates[0]["experience_years"] == 2
    assert candidates[0]["projects"] == ["Sales ETL pipeline using SQL and Python"]


def test_accepts_pipe_separator_and_preserves_invalid_experience_for_review():
    csv_text = (
        "candidate_id,name,skills,experience_years\n"
        'X1,Alex,"SQL|Python",about two\n'
    )
    candidate = parse_candidate_csv(io.BytesIO(csv_text.encode("utf-8")))[0]
    assert candidate["skills"] == ["SQL", "Python"]
    assert candidate["experience_years"] == "about two"


def test_missing_required_column_has_helpful_error():
    with pytest.raises(ValueError, match="Missing required CSV column"):
        parse_candidate_csv(io.BytesIO(b"candidate_id,name,skills\nX1,Alex,SQL\n"))


def test_duplicate_candidate_ids_rejected():
    csv_text = "candidate_id,name,skills,experience_years\nX1,A,SQL,1\nX1,B,Python,2\n"
    with pytest.raises(ValueError, match="duplicate candidate_id"):
        parse_candidate_csv(io.BytesIO(csv_text.encode("utf-8")))


def test_empty_csv_rejected():
    with pytest.raises(ValueError, match="empty"):
        parse_candidate_csv(io.BytesIO(b"  \n"))
