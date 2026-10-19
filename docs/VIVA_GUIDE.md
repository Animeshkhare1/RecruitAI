# Viva guide — RecruitAI (simple language)

## One-sentence summary
"RecruitAI is a pipeline of five specialised agents and a report generator. They score synthetic candidates against the requirements of a real Amazon job, check selected fairness risks, **independently recalculate and validate** each score, and send anything doubtful to a human recruiter. It never hires or rejects."

## The workflow in one breath
UI → **Planner** (is the input valid? what are the tasks?) → **Parser** (clean the record) → **Matcher** (score it) → **Bias Auditor** (look for fairness problems) → **Validator** (recompute the score and decide) → pass → **Report** / fail → **Human review** → dashboard + CSV.
The controller (`workflow.py`) runs the Planner's task list in order, one task for all candidates at a time, and passes each agent's dictionary to the next.

## Each agent: input → output
| Agent | Input | Output (key fields) | Plain explanation |
|---|---|---|---|
| Planner | `job`, `candidates` | `status` ready/invalid_input, `tasks`, `errors` | Checks weights sum to 100, required skills have weights, candidates exist. If invalid the workflow stops. |
| Parser | one raw candidate dict | `skills` (normalized), `missing_fields`, `invalid_fields`, `parsing_status` | Cleans spellings ("python"→"Python"); lists what is missing instead of guessing. Drops unknown fields such as `age`. |
| Matcher | parsed candidate, job | `criterion_scores`, `match_score`, matched/missing skills, `explanation` | Each required skill (or its listed equivalent) earns its weight; a project mentioning a required skill earns 10. Preferred skills and experience are reported, not scored. |
| Bias Auditor | match result, parsed candidate, job | `audit_status`, `checks`, `warnings`, `review_issues`, `qualification_evidence` | Checks the criteria are job-related, the result uses exactly the configured criteria, separates "missing" from "not demonstrated", flags sensitive fields. Read-only. |
| Decision Validator | audit, match, parsed, job | `validation_status`, `checks`, `recalculated_score`, `score_difference`, `errors`, `warnings`, `human_review_required` | Does **not** trust the Matcher: recomputes the score with its own code and compares. |
| Report Generator | all candidate results | `rows` (flat), `summary`, approved vs held lists | Builds the recruiter report and CSV; never invents missing results. |

## Questions you may be asked
**Is this really "agentic AI" if there is no LLM?**
Each agent is a specialised component with its own responsibility, input and output, coordinated by a planner and a workflow controller with conditional branches. They are deterministic Python functions — we do not claim autonomous LLM reasoning. Rules are cheaper, testable and repeatable; an LLM could replace the Parser or Matcher later.

**How is the validator "independent"?**
It never calls the Matcher. `recalculate_score` in `agents_part2.py` re-implements the documented rule from the job configuration and the parsed candidate. We test that it agrees with the Matcher on all 8 samples and 300 random candidates, and that it catches a tampered score (8 of 8 injected changes detected).

**What if the scoring rule itself is wrong?**
Both would be wrong. The validator catches *inconsistency* (bugs, tampering, wrong IDs, bad weights), not a wrong business rule. That is a stated limitation.

**What does audit status "passed" mean?**
Only that the implemented checks found nothing. It does not prove fairness.

**Difference between a warning, a review and a failure?**
Warning = visible, result continues (`validated_with_warnings`). Review = a person must look (`review_required`, e.g. missing information). Failure = a hard inconsistency (`validation_failed`, e.g. score ≠ recalculated score). Failure and review both block normal report approval.

**Why is C006 not rejected?**
Its skills are missing and experience is invalid. Missing information is not evidence that the candidate lacks the skill, so it goes to human review.

**Why is a low score not "unqualified"?**
The score only measures keyword matches against prototype weights. It is a screening aid, not a probability of job performance.

**Where do the weights come from?**
Our own design. Amazon publishes no scoring system. Only the requirements are real.

**How does the workflow handle failures?**
Invalid plan → stop. Parse failure or no score → skip later agents, flag for review. An exception inside one agent is recorded for that candidate and the others continue. There is **no automatic reprocessing**.

**What is the 0.01 tolerance?**
The rounding tolerance when comparing the Matcher's score with the recalculation. Scores here are whole numbers, so it only guards against floating-point noise.

**How did you handle sensitive fields?**
They are not in the scoring model. The Parser drops unknown fields, so the controller scans the raw record and the auditor reports them as a warning. Test 10 shows that adding age/gender/photo does not change the score. The name-independence test (16) only shows names are not used.

**Why a separate `agents_part2.py`?**
So the two of us never edit each other's files. It can be merged into `agents.py` later without changes.

**What was measured?**
8 candidates: 7 validated (6 + 1 with warning), 1 review, 0 failures; injected-error tests: 8/8 detected. No performance or time-saving claims.

## Be ready to open and explain
`workflow.py → run_workflow` and `_task_*`; `agents_part2.py → validate_decision` and `recalculate_score`; `tests/test_workflow.py → test_07`.
