"""
app.py - Streamlit interface for RecruitAI.

Run from the project folder:   streamlit run app.py

The page only DISPLAYS what the workflow returns. Every number, status and table
comes from run_workflow() - nothing is typed in by hand.
"""

import streamlit as st
from candidate_upload import csv_template_text, parse_candidate_csv

from agents_part2 import ReportExportError, report_to_csv, report_to_dataframe
from dataset import SYNTHETIC_DATA_NOTICE, get_sample_candidates, get_sample_job
from view_tables import (
    audit_table, candidates_table, criterion_table, issues_table, match_table,
    parse_table, results_table, review_reasons_table, validation_table,
)
from workflow import make_score_tamperer, run_workflow

st.set_page_config(page_title="RecruitAI", layout="wide")

ARCHITECTURE_DOT = """
digraph RecruitAI {
  rankdir=TB;
  node [shape=box, style="rounded,filled", fillcolor="#eef2f7", fontname="Helvetica", fontsize=11];
  recruiter [label="Recruiter input\\n(job configuration + candidate profiles)", shape=oval, fillcolor="#fff4d6"];
  data [label="Tools / data\\nReal job config (Amazon posting)\\nSynthetic candidates (dataset.py)", shape=cylinder, fillcolor="#fff4d6"];
  ui [label="Streamlit UI (app.py)"];
  wf [label="Workflow controller (workflow.py)\\nruns the Planner's task list in order,\\nkeeps conditions and execution log"];
  planner [label="1. Planner Agent\\nplan_workflow", fillcolor="#dbe9ff"];
  parser [label="2. Resume Parser Agent\\nparse_candidate", fillcolor="#dbe9ff"];
  matcher [label="3. Job Matcher Agent\\nmatch_candidate", fillcolor="#dbe9ff"];
  auditor [label="4. Bias Auditor Agent\\naudit_candidate", fillcolor="#e3f5e1"];
  validator [label="5. Decision Validator Agent\\nvalidate_decision\\n(independent score recalculation)", fillcolor="#e3f5e1"];
  decision [label="Validation\\npassed?", shape=diamond, fillcolor="#ffffff"];
  report [label="Report Generator\\ngenerate_report", fillcolor="#e3f5e1"];
  review [label="Human review\\n(manual - no automatic reprocessing)", fillcolor="#ffe1e1"];
  dash [label="Results dashboard + CSV download", fillcolor="#eef2f7"];
  recruiter -> ui; data -> ui [style=dashed]; ui -> wf; wf -> planner;
  planner -> parser [label=" plan: ready + tasks"];
  parser -> matcher [label=" parsed candidate"];
  matcher -> auditor [label=" match result"];
  auditor -> validator [label=" audit result"];
  validator -> decision [label=" validation result"];
  decision -> report [label=" pass"];
  decision -> review [label=" fail / review"];
  report -> dash; review -> dash;
  planner -> dash [style=dotted, label=" plan invalid: stop + show errors", fontsize=9];
}
"""

# ------------------------------------------------------------------ header ---
st.title("RecruitAI — Multi-Agent Recruitment Screening System")
st.write(
    "A **screening-assistance prototype**. It uses the requirements of a **real job posting** and "
    "**synthetic (fictional) candidate data**. Five specialised workflow agents and a report generator "
    "pass structured results to each other. The system never hires or rejects anyone - "
    "a human recruiter makes every decision."
)

with st.expander("How the agents work together (architecture)"):
    st.graphviz_chart(ARCHITECTURE_DOT)
    st.markdown(
        "- **Planner** checks the job configuration and candidate list, then returns an ordered task list.\n"
        "- **Resume Parser** turns each candidate record into a clean parsed candidate and lists missing/invalid fields.\n"
        "- **Job Matcher** scores the parsed candidate against the configured criteria.\n"
        "- **Bias Auditor** checks the scoring criteria and result for selected fairness risks (read-only).\n"
        "- **Decision Validator** recalculates the score independently and decides: report, or human review.\n"
        "- **Report Generator** assembles the recruiter report and the CSV.\n\n"
        "The agents are deterministic Python functions (no LLM). The workflow controller calls them in order "
        "and passes each agent's output to the next."
    )

job = get_sample_job()

# --------------------------------------------------------- 1. job information ---
st.header("1. Job information")
col1, col2, col3 = st.columns(3)
col1.markdown(f"**Company:** {job['company']}")
col2.markdown(f"**Job title:** {job['job_title']}")
col3.markdown(f"**Job ID:** {job['job_id']}")
st.markdown(f"**Official job source:** [{job['source_url']}]({job['source_url']})  (accessed {job['access_date']})")
left, right = st.columns(2)
with left:
    st.markdown("**Required skills** (from the posting)")
    st.write(", ".join(job["required_skills"]))
    st.markdown("**Preferred skills** (reported only, never scored)")
    st.write(", ".join(job["preferred_skills"]))
    equivalents = job.get("skill_equivalents") or {}
    if equivalents:
        st.caption("Posting examples accepted as equivalents: " +
                   "; ".join(f"{k} = {', '.join(v)}" for k, v in equivalents.items()))
    st.caption(f"Minimum experience stated in the posting: {job['minimum_experience_years']}+ year(s) "
               "(reported, not scored). Education requirement: none stated.")
with right:
    st.markdown("**Scoring criteria and weights** (prototype-defined)")
    st.dataframe({"Criterion": list(job["skill_weights"]), "Points": list(job["skill_weights"].values())})
    st.caption("The job requirements are real; the numeric weights are this project's own design, "
               "NOT Amazon's hiring criteria.")

# ----------------------------------------------------------- 2. candidate data ---
st.header("2. Candidate data (synthetic)")
st.info(SYNTHETIC_DATA_NOTICE)
st.caption("Upload a structured candidate CSV or use the built-in synthetic sample. PDF/DOCX resume parsing is not implemented.")

with st.expander("Upload your own candidate dataset (CSV)", expanded=False):
    st.markdown(
        "Upload a CSV with **candidate_id, name, skills, experience_years**. Optional columns: "
        "**education, projects, certifications**. Separate multiple skills/projects/certifications with semicolons (`;`). "
        "Extra columns are ignored. Maximum 500 candidates per upload."
    )
    st.download_button(
        "Download CSV template", data=csv_template_text(), file_name="recruitai_candidate_template.csv",
        mime="text/csv", key="candidate_template_download"
    )
    uploaded_file = st.file_uploader("Choose candidate CSV", type=["csv"], key="candidate_csv_upload")
    if uploaded_file is not None:
        try:
            uploaded_candidates = parse_candidate_csv(uploaded_file)
            st.success(f"CSV looks readable: {len(uploaded_candidates)} candidate(s) found.")
            st.dataframe(candidates_table(uploaded_candidates), use_container_width=True)
            if st.button("Use uploaded candidates", type="primary", key="use_uploaded_candidates"):
                st.session_state["candidates"] = uploaded_candidates
                st.session_state["candidate_source"] = uploaded_file.name
                st.session_state.pop("workflow_result", None)
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))

if st.button("Load synthetic sample dataset", key="load_btn"):
    st.session_state["candidates"] = get_sample_candidates()
    st.session_state["candidate_source"] = "Built-in synthetic sample dataset"
    st.session_state.pop("workflow_result", None)      # old results belong to the old selection

candidates = st.session_state.get("candidates")
selected_indexes = []
if not candidates:
    st.write("Click **Load synthetic sample dataset** to begin.")
else:
    st.caption("Active dataset: " + str(st.session_state.get("candidate_source", "Loaded candidate dataset")))
    st.dataframe(candidates_table(candidates), use_container_width=True)
    selected_indexes = st.multiselect(
        "Select candidates to process",
        options=list(range(len(candidates))),
        default=list(range(len(candidates))),
        format_func=lambda i: f"{candidates[i].get('candidate_id')} - {candidates[i].get('name')}",
    )

# -------------------------------------------------- sidebar: validator demo only ---
with st.sidebar:
    st.header("Demo controls")
    st.caption("Optional test: deliberately alter one Matcher score to show that the independent "
               "Decision Validator detects it. Leave off for the normal demonstration.")
    inject = st.checkbox("Inject a tampered Matcher score (test only)", key="inject_box",
                         disabled=not selected_indexes)
    tamper_id, tamper_score = None, 99
    if inject and selected_indexes:
        tamper_id = st.selectbox("Candidate to tamper with",
                                 [str(candidates[i].get("candidate_id")) for i in selected_indexes])
        tamper_score = st.number_input("Tampered score", min_value=0, max_value=150, value=99, step=1)

# ------------------------------------------------------------ 3. agent execution ---
st.header("3. Agent execution")
run_clicked = st.button("Run Recruitment Screening", type="primary", key="run_btn",
                        disabled=not selected_indexes)
if run_clicked:
    chosen = [candidates[i] for i in selected_indexes]
    functions = {"match_candidate": make_score_tamperer(tamper_id, tamper_score)} if inject and tamper_id else None
    st.session_state["workflow_result"] = run_workflow(job, chosen, functions)

wf = st.session_state.get("workflow_result")
if wf is None:
    st.info("Load the dataset, choose candidates and press **Run Recruitment Screening**.")
else:
    if wf["metadata"]["overridden_functions"]:
        st.warning("TEST MODE: a component was replaced by a deliberate fault "
                   f"({', '.join(wf['metadata']['overridden_functions'])}). "
                   "These are NOT normal demonstration results.")

    state = {"completed": "complete", "completed_with_errors": "error", "stopped": "error"}[wf["workflow_status"]]
    with st.status(f"Workflow status: {wf['workflow_status']}", expanded=True, state=state):
        for entry in wf["execution_log"]:
            st.markdown(f"**{entry['agent']}** (`{entry['function']}`) - {entry['status']} - {entry['status_counts']}")
            if entry["detail"]:
                st.caption(entry["detail"])

    if wf["workflow_status"] == "stopped":
        st.error("The workflow was stopped before processing any candidate. No results were produced.")
        for message in wf["errors"]:
            st.error(message)
    else:
        for message in wf["errors"]:
            st.error(message)
        records = wf["candidate_results"]
        with st.expander("Planner Agent - execution plan"):
            st.json(wf["execution_plan"])
        with st.expander("Resume Parser Agent - outcome per candidate"):
            st.dataframe(parse_table(records))
        with st.expander("Job Matcher Agent - scores"):
            st.dataframe(match_table(records))
        with st.expander("Bias Auditor Agent - checks"):
            st.caption("'passed' means only that the implemented checks found no issue. It is not a fairness guarantee.")
            st.dataframe(audit_table(records))
        with st.expander("Decision Validator Agent - checks and independent recalculation"):
            st.dataframe(validation_table(records))
        report_rows = len(wf["report"]["rows"]) if wf["report"] else 0
        st.write(f"**Report Generator:** {'generated' if wf['report'] else 'FAILED'} - {report_rows} row(s).")

        # ------------------------------------------------------- 4. KPI dashboard ---
        summary = wf["summary"]
        st.header("4. KPI dashboard")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Candidates", summary["total_candidates"])
        k2.metric("Validated Candidates", summary["validated_candidates"])
        k3.metric("Candidates Requiring Review", summary["candidates_requiring_review"])
        k4.metric("Validation Failures", summary["validation_failures"])
        st.caption("Candidates requiring review include validation failures. Validated + requiring review = total.")

        # ----------------------------------------------------- 5. candidate results ---
        st.header("5. Candidate results")
        if wf["report"]:
            st.dataframe(results_table(wf["report"]))
            st.caption("The match score is a screening aid. It is NOT a probability of job performance "
                       "and a low score does not make a candidate unqualified.")
        choice = st.selectbox(
            "Inspect one candidate's detailed result",
            options=list(range(len(records))),
            format_func=lambda i: f"{records[i]['candidate_id'] or '(missing ID)'} - "
                                  f"{(records[i]['parsed'] or {}).get('name') or 'no name'}",
        )
        record = records[choice]
        outcome = record["outcome"]
        st.markdown(f"**Validation status:** `{outcome['validation_status']}` - "
                    f"**Routing:** {'human review' if outcome['human_review_required'] else 'normal report'}")
        tab_parse, tab_match, tab_audit, tab_validate = st.tabs(["Parser", "Matcher", "Bias Auditor", "Validator"])
        with tab_parse:
            st.json(record["parsed"] if record["parsed"] is not None else {"note": "Parser produced no output."})
        with tab_match:
            if record["match"]:
                st.write(record["match"]["explanation"])
            st.dataframe(criterion_table(record, job))
        with tab_audit:
            st.json(record["audit"] if record["audit"] is not None else {"note": "The audit did not run for this candidate."})
        with tab_validate:
            st.json(record["validation"] if record["validation"] is not None else {"note": "The validator did not run for this candidate."})

        # ------------------------------------------- 6. warnings and validation ---
        st.header("6. Warnings and validation")
        if summary["validation_failures"]:
            st.error(f"{summary['validation_failures']} validation failure(s) detected. "
                     "These results must not be used without human review.")
        if summary["candidates_requiring_review"]:
            st.warning(f"{summary['candidates_requiring_review']} candidate(s) require human review.")
        issues = issues_table(records)
        if issues.empty:
            st.success("No issues were found by the implemented checks (this is not a guarantee of fairness).")
        else:
            st.dataframe(issues)
        reasons = review_reasons_table(records)
        if not reasons.empty:
            st.markdown("**Reasons for human review**")
            st.dataframe(reasons)

        # --------------------------------------------------- 7. downloadable report ---
        st.header("7. Downloadable report")
        try:
            csv_text = report_to_csv(wf["report"])
            st.download_button("Download CSV report", data=csv_text, file_name="recruitai_screening_report.csv",
                               mime="text/csv", key="download_btn")
            with st.expander("Preview the CSV contents"):
                st.dataframe(report_to_dataframe(wf["report"]))
        except ReportExportError as exc:
            st.error(str(exc))

# ------------------------------------------------------------- 8. limitations ---
st.header("8. Project limitations")
st.markdown(
    "- The built-in candidates are **synthetic**; uploaded CSV data is only as accurate as the user-provided records.\n"
    "- Scoring uses **predefined criteria** and **prototype weights** that are not the employer's official criteria.\n"
    "- Upload accepts structured CSV only; PDF/DOCX resume extraction is not implemented.\n"
    "- Matching is keyword-based and can miss equivalent skills written differently.\n"
    "- The match score is **not a probability of job performance**.\n"
    "- The fairness checks are **limited**; a 'passed' audit does not prove an absence of bias or discrimination.\n"
    "- There is no automatic reprocessing; failed items go to a human.\n"
    "- **Human recruiters retain responsibility for every decision.**"
)
