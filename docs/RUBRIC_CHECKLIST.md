# CA-3 rubric checklist (honest status)

Legend: ✅ implemented **and tested** · 🟡 implemented **but not fully tested** · ❌ not implemented.
"Tested" = executed in the build environment with real pytest 9.1.1: **72 passed, 1 skipped** (the Streamlit end-to-end test, because Streamlit could not be installed there).
**Before the viva run `python -m pytest -q` and `streamlit run app.py` on your own machine** — that upgrades the 🟡 items.

| # | Requirement | Evidence | Status |
|---|---|---|---|
| 1 | Problem identification | README §2 | ✅ documented |
| 2 | Domain justification | README §3 | ✅ documented |
| 3 | Existing challenge and solution | README §3 manual-vs-assisted table | ✅ documented (no time-saving numbers claimed) |
| 4 | Agent roles (five agents + report generator) | README §7; `agents.py`, `agents_part2.py` | ✅ |
| 5 | Orchestration with conditional branches | `workflow.py`: plan check → stop; parse/match failure → review; audit warning kept; validation pass → report / fail → review; per-candidate exception isolation | ✅ tests 01–03, 06, 11–13 |
| 6 | Architecture diagram (agents, data flow, tools, decisions) | `docs/architecture.png/.svg/.dot`, Mermaid in README §6 | ✅ file rendered and checked; 🟡 in-app `st.graphviz_chart` not viewed in a real browser |
| 7 | Working Streamlit PoC | `app.py` (8 sections, CSV download) | 🟡 executed only against a stub of Streamlit (no errors); the real-Streamlit `AppTest` (test 17c) is written but was **skipped** here |
| 8 | Synthetic data | `dataset.py` (User 1, 8 candidates) | ✅ User 1's tests |
| 9 | Output validation | `validate_decision`: ID check, range, **independent recalculation**, missing info, weights, audit outcome | ✅ tests 06–09, 11; mutation-checked (disabling the comparison makes tests fail); 8/8 injected alterations detected; 0 disagreements with the Matcher on 308 cases |
| 10 | Results | README §14 (measured numbers only) | ✅ measured in the build run; 🟡 until you re-run pytest and the app on your machine |
| 11 | Limitations and future scope | README §15, in-app Section 8 | ✅ |
| 12 | PPT (7 slides, ≤ 5 min for two people) | Slides artifact "RecruitAI CA-3 Presentation" (private link; export to .pptx/PDF from it) and the text copy `docs/PPT_DRAFT.md` | 🟡 draft; not reviewed by User 1; timing not rehearsed; teammate name/PRN is a placeholder |
| 13 | Technical understanding by both members | `docs/VIVA_GUIDE.md` | 🟡 a guide exists; understanding cannot be verified by code — both of you must read it and trace the code |

## Items that are NOT done / need you
* ❌ PDF/DOCX resume parsing (not implemented; the UI says so).
* ❌ Automatic reprocessing of failed candidates (not implemented; failures go to a human, and the UI says so).
* ⚠️ The Amazon job URL was not independently re-verified by me (the page-fetch tool could not confirm it live). Open it and save a PDF/screenshot.
* ⚠️ `job_research.md` was supplied as a PDF; copy User 1's original `job_research.md` into the project folder.
* ⚠️ Team names/PRNs on slide 1 are placeholders for you to confirm.


## CSV upload addition
- [x] Streamlit accepts structured candidate CSV uploads and previews parsed rows.
- [x] Required headers and duplicate candidate IDs are validated.
- [x] Downloadable CSV template is provided.
- [x] Uploaded candidates go through the existing workflow; PDF/DOCX parsing is explicitly not claimed.
