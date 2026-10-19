# RecruitAI — 7-slide presentation draft (max 5 minutes for the pair)

Text copy of the "RecruitAI CA-3 Presentation" Slides artifact, so both of you can review it offline.
Status: **draft, not timed, not yet reviewed by User 1.** Replace the bracketed teammate name/PRN on slide 1.

| # | Slide | Content | Speaker | Time |
|---|---|---|---|---|
| 1 | Title | RecruitAI — Multi-Agent Recruitment Screening and Validation System. Animesh Khare (PRN 24070126032) and [teammate, PRN]. Domain: HR and recruitment. | Both | 0:15 |
| 2 | Problem | Manual screening is slow and inconsistent: high volume, slow comparison, inconsistent weighing, fairness easy to miss. Why HR: repetitive and rule-driven, but sensitive, so a human stays in charge. | User 1 | 0:35 |
| 3 | Solution and data | Manual vs assisted table (qualitative, no time-saving numbers). Real: Amazon "Data Engineer I", Job ID 2829886, accessed 2026-10-14. Synthetic: 8 fictional candidates. Prototype: our own scoring weights (SQL 25, ETL 20, Data Modeling 15, Data Warehousing 15, Scripting Language 15, relevant project 10). | User 1 | 0:40 |
| 4 | Agent roles | Part 1 (User 1): Planner, Resume Parser, Job Matcher. Part 2 (Animesh): Bias Auditor, Decision Validator, Report Generator. All deterministic Python functions, not LLM agents. | User 1 (1–3), Animesh (4–6) | 0:50 |
| 5 | Architecture | Recruiter input → Streamlit UI → workflow controller (with tools/data) → Planner → Parser → Matcher → Auditor → Validator → pass: Report Generator / fail or review: human review (no automatic reprocessing) → dashboard + CSV. | Animesh | 0:50 |
| 6 | Demo and results | Live demo. Normal run: 8 processed, 7 validated (6 + 1 with a warning), 1 to human review (C006), 0 failures. Injected-error tests: C002 altered 75 → 99 caught; 8/8 alterations detected; independent recalculation matched the Matcher on 308 cases. Build run: pytest 72 passed, 1 skipped. | Animesh (demo), User 1 (numbers) | 1:10 |
| 7 | Limitations, future scope, conclusion | Limits: synthetic data, keyword matching, prototype weights, limited fairness checks, validator catches inconsistency not a wrong rule, no hiring decisions. Future: PDF/DOCX parsing, semantic matching, local LLM, better fairness evaluation, system integration, audit logging. | User 1 (limits), Animesh (future + conclusion) | 0:40 |

Total: 5:00.

## Before presenting
1. Re-run `python -m pytest -q` and `streamlit run app.py` on your own machine; update the slide 6 footer if your numbers differ.
2. Open the Amazon link and save a PDF or screenshot of the posting (it may be removed later).
3. Rehearse once with a stopwatch; the timings above are estimates.
4. Do not present the "tampered score" demo as a normal result: say it is a deliberate test.
