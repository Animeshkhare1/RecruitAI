"""
workflow.py - the workflow controller of RecruitAI (User 2).

It calls the six components in the agreed order and passes each agent's
structured output to the next one:

    Planner ──> Parser ──> Matcher ──> Bias Auditor ──> Decision Validator ──> Report Generator
    plan_workflow  parse_candidate  match_candidate  audit_candidate  validate_decision  generate_report

HOW IT RUNS (simple version for the viva)
  1. plan_workflow(job, candidates) checks the inputs and returns a task list.
  2. If the plan is not "ready" the workflow STOPS and returns the planner's errors.
  3. Otherwise the controller walks through plan["tasks"] IN ORDER. Each task is run
     for every candidate before the next task starts (parse all, then match all, ...).
  4. Conditions (real `if` statements below):
        parse failed            -> no matching, audit or validation; candidate goes to human review
        match failed / no score -> same, the result is never treated as valid
        audit warning           -> kept and shown; the result continues
        validation failed       -> candidate is routed to human review
        validation passed       -> candidate is routed to the normal report
  5. An exception inside one agent is recorded for that candidate; the others continue.
  6. generate_report(results) builds the report; the controller returns everything to the UI.

HONEST NOTES
  * There is NO automatic reprocessing loop. A failed candidate is routed to a human.
  * The agents are deterministic Python functions, not autonomous LLM agents.
  * Nothing here hires or rejects anybody.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy

from agents import match_candidate, parse_candidate, plan_workflow
from agents_part2 import (
    apply_input_scan,
    audit_candidate,
    candidate_outcome,
    generate_report,
    scan_input_for_sensitive_fields,
    summarize_candidate_results,
    validate_decision,
)
from schemas import MATCH_FAILED, MATCH_SKIPPED, PARSE_FAILED, PLAN_READY, WORKFLOW_TASKS

# workflow_status values
WF_COMPLETED = "completed"                      # every candidate went through the workflow
WF_COMPLETED_WITH_ERRORS = "completed_with_errors"   # an agent raised an exception for >= 1 candidate
WF_STOPPED = "stopped"                          # the plan was invalid; nothing was processed

AGENT_FOR_TASK = {
    "parse_candidates": ("Resume Parser Agent", "parse_candidate"),
    "match_candidates": ("Job Matcher Agent", "match_candidate"),
    "audit_candidates": ("Bias Auditor Agent", "audit_candidate"),
    "validate_results": ("Decision Validator Agent", "validate_decision"),
    "generate_report": ("Report Generator", "generate_report"),
}


def default_functions() -> dict:
    """The real components. Tests can replace some of them (see run_workflow)."""
    return {
        "plan_workflow": plan_workflow,
        "parse_candidate": parse_candidate,
        "match_candidate": match_candidate,
        "audit_candidate": audit_candidate,
        "validate_decision": validate_decision,
        "generate_report": generate_report,
    }


def empty_summary() -> dict:
    return {
        "total_candidates": 0,
        "validated_candidates": 0,
        "candidates_requiring_review": 0,
        "validation_failures": 0,
    }


# =========================================================== small helpers ===
def _new_record(position: int) -> dict:
    """One candidate's slot. Each agent fills in its own key."""
    return {
        "input_position": position,
        "candidate_id": None,
        "parsed": None,
        "match": None,
        "audit": None,
        "validation": None,
        "sensitive_input_fields": [],
        "steps": {},            # step name -> {"status": ..., "detail": ...}
        "review_reasons": [],   # reasons added by the workflow itself
        "errors": [],           # exceptions raised by an agent for THIS candidate
        "outcome": {},          # filled at the end by candidate_outcome()
    }


def _call(record: dict, step: str, label: str, function, *args):
    """
    Run one agent function for one candidate.
    If it raises, the error is stored on the candidate (never swallowed silently)
    and None is returned so the rest of the batch continues.
    """
    try:
        return function(*args)
    except Exception as exc:  # isolation: one bad candidate must not stop the batch
        message = f"{label} raised {type(exc).__name__}: {exc}"
        record["errors"].append(message)
        record["steps"][step] = {"status": "error", "detail": message}
        record["review_reasons"].append(f"Processing error - {message}")
        return None


def _has_usable_match(record: dict) -> bool:
    """True when the Matcher produced a result we are allowed to audit and validate."""
    match = record.get("match")
    return (
        isinstance(record.get("parsed"), dict)
        and record["parsed"].get("parsing_status") != PARSE_FAILED
        and isinstance(match, dict)
        and match.get("matching_status") not in (MATCH_SKIPPED, MATCH_FAILED)
        and match.get("match_score") is not None
    )


def _log_entry(task: str, records: list, step: str, extra: str = "") -> dict:
    """Describe what really happened in one task, from the candidates' recorded steps."""
    agent, function = AGENT_FOR_TASK[task]
    counts = Counter(r["steps"].get(step, {}).get("status", "not_run") for r in records)
    errors = counts.get("error", 0)
    return {
        "task": task,
        "agent": agent,
        "function": function,
        "status": "completed_with_errors" if errors else "completed",
        "status_counts": dict(counts),
        "detail": extra,
    }


# ================================================================== tasks ====
def _task_parse(ctx: dict) -> dict:
    fn, records = ctx["fn"], ctx["records"]
    for record, raw in zip(records, ctx["candidates"]):
        record["sensitive_input_fields"] = scan_input_for_sensitive_fields(raw)
        parsed = _call(record, "parse", "parse_candidate", fn["parse_candidate"], raw)
        if parsed is None:
            continue
        record["parsed"] = parsed
        record["candidate_id"] = parsed.get("candidate_id")
        status = parsed.get("parsing_status")
        notes = "; ".join(parsed.get("parsing_notes") or []) or "Parsed without problems."
        record["steps"]["parse"] = {"status": status, "detail": notes}
        if status == PARSE_FAILED:
            record["review_reasons"].append(f"Parsing failed: {notes}")

    # The Planner only WARNS about duplicate IDs, so the controller flags them.
    id_counts = Counter(r["candidate_id"] for r in records if r["candidate_id"])
    for record in records:
        cid = record["candidate_id"]
        if cid and id_counts[cid] > 1:
            record["review_reasons"].append(
                f"Duplicate candidate_id '{cid}' appears {id_counts[cid]} times in the input, "
                "so these results cannot be told apart reliably."
            )
    return _log_entry("parse_candidates", records, "parse")


def _task_match(ctx: dict) -> dict:
    fn, records, job = ctx["fn"], ctx["records"], ctx["job"]
    for record in records:
        parsed = record["parsed"]
        if not isinstance(parsed, dict) or parsed.get("parsing_status") == PARSE_FAILED:
            record["steps"]["match"] = {"status": "skipped", "detail": "Skipped: the candidate could not be parsed."}
            continue
        match = _call(record, "match", "match_candidate", fn["match_candidate"], parsed, job)
        if match is None:
            continue
        record["match"] = match
        status, score = match.get("matching_status"), match.get("match_score")
        if status in (MATCH_SKIPPED, MATCH_FAILED) or score is None:
            record["steps"]["match"] = {"status": status or "failed", "detail": "No valid score was produced."}
            record["review_reasons"].append(
                f"Matching did not produce a valid score (matching_status='{status}'). The result is not treated as valid."
            )
        else:
            record["steps"]["match"] = {"status": status, "detail": f"Score {score}/100."}
    return _log_entry("match_candidates", records, "match")


def _task_audit(ctx: dict) -> dict:
    fn, records, job = ctx["fn"], ctx["records"], ctx["job"]
    for record in records:
        if not _has_usable_match(record):
            record["steps"]["audit"] = {"status": "skipped", "detail": "Skipped: there is no valid match result to audit."}
            continue
        audit = _call(record, "audit", "audit_candidate", fn["audit_candidate"],
                      record["match"], record["parsed"], job)
        if audit is None:
            continue    # the validator will then refuse to approve this candidate
        audit = apply_input_scan(audit, record["sensitive_input_fields"])
        record["audit"] = audit
        record["steps"]["audit"] = {
            "status": audit.get("audit_status"),
            "detail": f"{len(audit.get('warnings') or [])} warning(s), {len(audit.get('review_issues') or [])} serious issue(s).",
        }
    return _log_entry("audit_candidates", records, "audit")


def _task_validate(ctx: dict) -> dict:
    fn, records, job = ctx["fn"], ctx["records"], ctx["job"]
    for record in records:
        if not _has_usable_match(record):
            record["steps"]["validate"] = {"status": "skipped", "detail": "Skipped: there is no valid match result to validate."}
            continue
        validation = _call(record, "validate", "validate_decision", fn["validate_decision"],
                           record["audit"], record["match"], record["parsed"], job)
        if validation is None:
            continue
        record["validation"] = validation
        record["steps"]["validate"] = {
            "status": validation.get("validation_status"),
            "detail": validation.get("explanation", ""),
        }
    return _log_entry("validate_results", records, "validate")


def _task_report(ctx: dict) -> dict:
    fn, records = ctx["fn"], ctx["records"]
    # Decision branch: this is where "validation passes" and "validation fails" separate.
    for record in records:
        record["outcome"] = candidate_outcome(record)
    to_report = sum(1 for r in records if r["outcome"]["routing"] == "report")
    to_review = len(records) - to_report
    try:
        ctx["report"] = fn["generate_report"]({"job": ctx["job"], "plan": ctx["plan"], "candidate_results": records})
        status, detail = "completed", ""
    except Exception as exc:
        ctx["report"] = None
        message = f"generate_report raised {type(exc).__name__}: {exc}"
        ctx["errors"].append(message)
        status, detail = "error", message
    rows = len(ctx["report"]["rows"]) if isinstance(ctx["report"], dict) and isinstance(ctx["report"].get("rows"), list) else 0
    return {
        "task": "generate_report",
        "agent": AGENT_FOR_TASK["generate_report"][0],
        "function": "generate_report",
        "status": status,
        "status_counts": {"report_rows": rows},
        "detail": detail or (
            f"Branch after validation: {to_report} candidate(s) passed validation (normal report); "
            f"{to_review} candidate(s) routed to human review (there is no automatic reprocessing)."
        ),
    }


TASK_HANDLERS = {
    "parse_candidates": _task_parse,
    "match_candidates": _task_match,
    "audit_candidates": _task_audit,
    "validate_results": _task_validate,
    "generate_report": _task_report,
}


# ============================================================== main entry ===
def run_workflow(job: dict, candidates: list, functions: dict | None = None) -> dict:
    """
    Run the whole RecruitAI pipeline and return a predictable dictionary:

    {
      "workflow_status": "completed" | "completed_with_errors" | "stopped",
      "job": {...}, "execution_plan": {...},
      "candidate_results": [ {candidate record: parsed, match, audit, validation, steps, outcome, ...} ],
      "report": {...} | None        (None only when the workflow was stopped or the report failed),
      "summary": {total_candidates, validated_candidates, candidates_requiring_review, validation_failures},
      "errors": [str, ...],
      "execution_log": [ {task, agent, function, status, status_counts, detail}, ... ],
      "metadata": {"overridden_functions": [...]}
    }

    `functions` (optional) replaces some components, e.g. {"match_candidate": tampered_matcher}.
    It exists so tests and the demo can inject a fault and prove the validator catches it.
    """
    fn = default_functions()
    overridden = []
    if functions:
        unknown = sorted(set(functions) - set(fn))
        if unknown:
            raise ValueError(f"Unknown component name(s) in `functions`: {unknown}")
        fn.update(functions)
        overridden = sorted(functions)

    result = {
        "workflow_status": WF_STOPPED,
        "job": deepcopy(job),
        "execution_plan": {},
        "candidate_results": [],
        "report": None,
        "summary": empty_summary(),
        "errors": [],
        "execution_log": [],
        "metadata": {"overridden_functions": overridden},
    }

    # ---- Step 1: plan ---------------------------------------------------------
    try:
        plan = fn["plan_workflow"](job, candidates)
    except Exception as exc:
        result["errors"].append(f"plan_workflow raised {type(exc).__name__}: {exc}")
        result["execution_log"].append({"task": "plan_workflow", "agent": "Planner Agent", "function": "plan_workflow",
                                        "status": "error", "status_counts": {}, "detail": result["errors"][-1]})
        return result
    result["execution_plan"] = plan
    result["execution_log"].append({
        "task": "plan_workflow", "agent": "Planner Agent", "function": "plan_workflow",
        "status": plan.get("status"), "status_counts": {"candidates": plan.get("candidate_count", 0)},
        "detail": "; ".join(plan.get("warnings") or []) or "Inputs checked.",
    })

    # ---- Step 2: stop safely if the plan is not usable -------------------------
    if plan.get("status") != PLAN_READY:
        result["errors"] = list(plan.get("errors") or ["The plan is not ready."])
        return result
    if list(plan.get("tasks") or []) != WORKFLOW_TASKS:
        result["errors"] = [f"The plan contains unexpected tasks: {plan.get('tasks')}. Expected {WORKFLOW_TASKS}."]
        return result

    # ---- Steps 3-11: run the planned tasks in order --------------------------
    ctx = {
        "fn": fn, "job": job, "plan": plan, "candidates": candidates,
        "records": [_new_record(i) for i in range(1, len(candidates) + 1)],
        "report": None, "errors": result["errors"],
    }
    for task in plan["tasks"]:
        result["execution_log"].append(TASK_HANDLERS[task](ctx))

    records = ctx["records"]
    for record in records:
        record["outcome"] = candidate_outcome(record)
    result["candidate_results"] = records
    result["report"] = ctx["report"]
    result["summary"] = (
        ctx["report"]["summary"] if isinstance(ctx["report"], dict) and isinstance(ctx["report"].get("summary"), dict)
        else summarize_candidate_results(records)
    )
    for record in records:
        for message in record["errors"]:
            who = record["candidate_id"] or f"input #{record['input_position']}"
            result["errors"].append(f"Candidate {who}: {message}")
    has_errors = bool(result["errors"])
    result["workflow_status"] = WF_COMPLETED_WITH_ERRORS if has_errors else WF_COMPLETED
    return result


# ================================================== demo / test fault injection ==
def make_score_tamperer(target_id: str, tampered_score, matcher=match_candidate):
    """
    TEST / DEMO ONLY. Returns a replacement for match_candidate that overwrites the
    score of ONE candidate after the real Matcher ran (the criterion scores are left
    alone). Passing it to run_workflow(functions={"match_candidate": ...}) shows
    that the independent Decision Validator notices the altered score.
    """
    def tampered(parsed_candidate, job):
        match = matcher(parsed_candidate, job)
        if isinstance(parsed_candidate, dict) and parsed_candidate.get("candidate_id") == target_id and isinstance(match, dict):
            match = dict(match)
            match["match_score"] = tampered_score
        return match
    return tampered
