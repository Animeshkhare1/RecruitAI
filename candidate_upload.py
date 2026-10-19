"""Safe CSV import helpers for structured candidate profiles.

CSV input is NOT PDF/DOCX resume parsing. List fields use semicolons (;) or pipes (|).
Required headers: candidate_id, name, skills, experience_years.
Optional headers: education, projects, certifications.
"""
from __future__ import annotations

import csv
import io
import math
from typing import Any

REQUIRED_COLUMNS = ("candidate_id", "name", "skills", "experience_years")
OPTIONAL_COLUMNS = ("education", "projects", "certifications")
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
MAX_CANDIDATES = 500


def _split_list(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    # Semicolon is recommended. Pipe is accepted as an alternative.
    separator = ";" if ";" in text else ("|" if "|" in text else None)
    pieces = text.split(separator) if separator else [text]
    return [piece.strip() for piece in pieces if piece.strip()]


def parse_candidate_csv(file_obj) -> list[dict]:
    """Parse an uploaded CSV file into the candidate dictionaries used by agents.py.

    Raises ValueError with a user-friendly message for malformed files/headers/IDs.
    Invalid experience text is retained so the Resume Parser can flag it for review.
    """
    try:
        raw = file_obj.getvalue() if hasattr(file_obj, "getvalue") else file_obj.read()
        if isinstance(raw, bytes):
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("utf-8-sig", errors="replace")
        else:
            text = str(raw)
        if not text.strip():
            raise ValueError("The uploaded CSV is empty.")
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ValueError("Could not find a CSV header row.")
        headers = [str(h or "").strip().lower() for h in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise ValueError("The CSV contains duplicate column names. Keep each header unique.")
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            raise ValueError("Missing required CSV column(s): " + ", ".join(missing) + ". Download the template and use its header names.")

        candidates = []
        seen_ids = set()
        for line_number, row in enumerate(reader, start=2):
            if not row or not any(str(value or "").strip() for value in row.values()):
                continue
            # Normalize headers and tolerate extra columns (they are ignored).
            normalized = {str(k or "").strip().lower(): v for k, v in row.items() if k is not None}
            candidate_id = str(normalized.get("candidate_id") or "").strip()
            if not candidate_id:
                raise ValueError(f"Row {line_number}: candidate_id is blank.")
            if candidate_id in seen_ids:
                raise ValueError(f"Row {line_number}: duplicate candidate_id '{candidate_id}'. IDs must be unique.")
            seen_ids.add(candidate_id)

            name = str(normalized.get("name") or "").strip()
            experience_text = str(normalized.get("experience_years") or "").strip()
            experience: Any = experience_text
            if experience_text:
                try:
                    number = float(experience_text)
                    if math.isfinite(number):
                        experience = int(number) if number.is_integer() else number
                except ValueError:
                    pass  # Keep invalid text so the parser/validator can flag it.

            candidate = {
                "candidate_id": candidate_id,
                "name": name,
                "skills": _split_list(normalized.get("skills")),
                "experience_years": experience,
                "education": str(normalized.get("education") or "").strip(),
                "projects": _split_list(normalized.get("projects")),
                "certifications": _split_list(normalized.get("certifications")),
            }
            candidates.append(candidate)
            if len(candidates) > MAX_CANDIDATES:
                raise ValueError(f"A maximum of {MAX_CANDIDATES} candidates can be uploaded at once.")

        if not candidates:
            raise ValueError("No candidate rows were found in the CSV.")
        return candidates
    except (csv.Error, UnicodeError) as exc:
        raise ValueError(f"Could not read this CSV: {exc}") from exc


def csv_template_text() -> str:
    """Return a small CSV template with one fictional example row."""
    return (
        "candidate_id,name,skills,experience_years,education,projects,certifications\n"
        'C101,Example Candidate,"SQL; Python; ETL",2,B.Tech,"Sales ETL pipeline using SQL and Python",Cloud Fundamentals\n'
    )
