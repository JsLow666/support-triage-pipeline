# Support-Triage Pipeline — Design Document

> **Confirmed Design Decisions**
> - Language: **Python**
> - LLM Provider: **Google Gemini** (via `google-genai` SDK)
> - Environment Config: **`.env` file** using `python-dotenv`
> - Project Layout: **`stages/` package**
> - Replay Policy: **Always overwrite all outputs** on each run
> - Override + Reply: Human category override does **NOT** re-invoke LLM; original `suggested_reply` is kept

## Overview

This document describes the design of a **replayable support-triage pipeline** that reads customer support tickets from disk, classifies them, detects urgency, drafts suggested replies, supports a human review checkpoint, and produces a final agent queue summary.

The pipeline is designed to be:
- **Deterministic** in all pre-LLM and post-LLM logic.
- **Reproducible** from a clean checkout against any equivalent input files.
- **Strictly validated** — all category/priority values are constrained to `triage_config.json`.

---

## Project Structure

```
support-triage-pipeline/
│
├── tickets.json                  # Input: raw customer tickets
├── triage_config.json            # Input: allowed labels, routing, reply style
│
├── pipeline.py                   # Main entry point — orchestrates all stages
├── stages/
│   ├── __init__.py
│   ├── normalize.py              # Stage: TICKETS_NORMALIZED
│   ├── predict.py                # Stage: TRIAGE_PREDICTED
│   ├── review.py                 # Stage: HUMAN_REVIEW_COMPLETE
│   ├── queue.py                  # Stage: FINAL_QUEUE_GENERATED
│   └── validate_stage.py         # Stage: VALIDATION_COMPLETE
│
├── llm/
│   ├── __init__.py
│   └── client.py                 # LLM provider wrapper (prompt construction + call)
│
├── validate.py                   # Standalone validation script (SHOULD ATTEMPT §6)
│
├── normalized_tickets.json       # Output: deterministic normalization
├── triage_predictions.json       # Output: LLM triage results
├── review_overrides.json         # Output: human corrections
├── final_queue.json              # Output: post-review agent queue
├── queue_summary.md              # Output: markdown summary
├── escalations.json              # Output (optional): low-confidence / "other" tickets
└── llm_calls.jsonl               # Output (optional): LLM call audit log
```

> **Decision:** The pipeline uses a `stages/` package for clear separation of concerns and testability.

---

## Pipeline Stages

The pipeline enforces a strict linear stage progression. A `PipelineState` enum (or equivalent string constant) tracks the current stage. No stage may execute unless the previous stage has completed successfully.

```
INIT
 -> INPUTS_LOADED
 -> TICKETS_NORMALIZED
 -> TRIAGE_PREDICTED
 -> HUMAN_REVIEW_COMPLETE
 -> FINAL_QUEUE_GENERATED
 -> VALIDATION_COMPLETE
 -> RESULTS_FINALISED
```

The stage name strings used in code and in `llm_calls.jsonl` will match these exact names.

### Stage Gate Enforcement

```
pipeline_state = "INIT"

for each stage in ordered_stages:
    assert pipeline_state == expected_predecessor
    execute(stage)
    pipeline_state = stage.name
```

If any stage fails, the pipeline halts and logs the failure before exiting. This prevents partial outputs from being consumed as valid results.

---

## Stage-by-Stage Design

---

### Stage 1 — `INPUTS_LOADED`

**Responsibility:** Read both input files from disk and validate their structure.

**Actions:**
1. Read `tickets.json` → parse as a JSON array of ticket objects.
2. Read `triage_config.json` → parse as a JSON object.
3. Validate that required top-level keys exist:
   - `allowed_categories`, `allowed_priorities`, `reply_style`, `routing_rules`
4. Store both in memory for downstream stages.

**No LLM calls. No file writes.**

---

### Stage 2 — `TICKETS_NORMALIZED`

**Responsibility:** Deterministically transform raw tickets into a normalized form **before any LLM call**.

**Actions:**
1. For each ticket in `tickets.json`, produce a normalized record:

```json
{
  "ticket_id": "string",
  "subject": "string",
  "message": "string",
  "channel": "string",
  "created_at": "string",
  "text_for_model": "string",
  "char_count": 0
}
```

2. `text_for_model` construction (deterministic, no LLM):
   - Strip leading/trailing whitespace from `subject` and `message`.
   - Concatenate as: `"Subject: {subject}\nMessage: {message}"`
   - Normalize internal whitespace (collapse multiple spaces/newlines).

3. `char_count` = `len(text_for_model)` (character count of the final string).

4. Write result to `normalized_tickets.json`.

**No LLM calls.**

---

### Stage 3 — `TRIAGE_PREDICTED`

**Responsibility:** Make LLM call(s) to classify and respond to all normalized tickets.

#### LLM Call Strategy

- **One LLM call** processes all tickets in a single batch prompt (as required).
- The prompt includes:
  - The full `triage_config.json` (as context for allowed values and routing).
  - All normalized tickets (using the `text_for_model` field).
  - Strict instructions to output a JSON array matching the schema below.

#### LLM Output Schema (per ticket)

```json
{
  "ticket_id": "string",
  "category": "billing_issue",
  "priority": "urgent",
  "confidence": 0.95,
  "reason": "string",
  "suggested_reply": "string",
  "route_to": "string"
}
```

> **Note:** `confidence` (0.0–1.0) is requested from Gemini as part of the SHOULD ATTEMPT §5 feature. Gemini's JSON mode (`response_mime_type="application/json"`) is used to enforce structured output.

#### Post-LLM Validation (deterministic code, not LLM)

After receiving the LLM response, the pipeline validates and enforces constraints in code:

| Field | Validation Rule |
|---|---|
| `category` | Must be in `allowed_categories`; if not → fallback to `"other"` |
| `priority` | Must be in `allowed_priorities`; if not → fallback to `"normal"` |
| `route_to` | **Recomputed** from `routing_rules[category]` — never trusted from the model |
| `suggested_reply` | Word count checked against `reply_style.max_words` (80); truncate if over |
| `confidence` | Clamped to `[0.0, 1.0]`; default to `0.5` if missing or invalid |

> **Design decision:** `route_to` is always derived in deterministic code, never from the LLM's output. This ensures routing correctness regardless of model drift.

#### Batch Resilience (STRETCH §7)

- If a ticket's prediction is malformed or missing from the LLM response, it is not discarded.
- Fallback record is inserted:
  - `category` → `"other"`
  - `priority` → `"normal"`
  - `route_to` → `"manual_review_queue"`
  - `confidence` → `0.0`
  - `reason` → `"Parse error: model output missing or malformed"`
  - `suggested_reply` → `""`
- The error is logged to `llm_calls.jsonl` and/or a pipeline log.

#### Output

Write validated predictions to `triage_predictions.json`.

---

### Stage 4 — `HUMAN_REVIEW_COMPLETE`

**Responsibility:** Display predictions to the reviewer, accept optional overrides, validate them, and persist corrections.

#### Terminal Display (per ticket)

```
[T-1001] Category: billing_issue | Priority: urgent
[T-1002] Category: account_access | Priority: high
...
```

#### Override Input Loop

```
Enter any overrides as: ticket_id,category,priority
Press Enter on an empty line when done.
```

- Reviewer may enter zero or more override lines.
- Each line is parsed as `ticket_id,category,priority`.
- Validation in code:
  - `ticket_id` must exist in `triage_predictions.json`.
  - `category` must be in `allowed_categories`.
  - `priority` must be in `allowed_priorities`.
  - Invalid overrides are rejected with an error message; reviewer is prompted to retry that line.

#### Override Effects

- If `category` changes → `route_to` is **recomputed** from `routing_rules[new_category]`.
- If only `priority` changes → `category` and `route_to` remain the same.
- `suggested_reply` is **not** re-generated at this stage (see open question below).

#### Output: `review_overrides.json`

```json
[
  {
    "ticket_id": "string",
    "old_category": "string",
    "new_category": "string",
    "old_priority": "string",
    "new_priority": "string"
  }
]
```

If no overrides are provided, `review_overrides.json` is written as an empty array `[]`.

> **Decision:** The original `suggested_reply` from the LLM is kept even if the category is overridden. No second LLM call is made during human review.

---

### Stage 5 — `FINAL_QUEUE_GENERATED`

**Responsibility:** Merge predictions with human overrides and produce the final agent queue artifacts.

#### Merge Logic (deterministic)

For each ticket:
1. Start with the `triage_predictions.json` record.
2. Check if `ticket_id` has an entry in `review_overrides.json`.
3. If yes → apply `new_category`, `new_priority`; recompute `route_to`.
4. Set `was_overridden: true` if an override was applied, else `false`.

#### Output: `final_queue.json`

```json
[
  {
    "ticket_id": "string",
    "final_category": "string",
    "final_priority": "string",
    "final_route_to": "string",
    "suggested_reply": "string",
    "was_overridden": true
  }
]
```

#### Output: `queue_summary.md`

A markdown report containing:

1. **Total ticket count**
2. **Count by final category** (table)
3. **Count by final priority** (table)
4. **Queue breakdown by destination** (`final_route_to` groupings)
5. **Overridden tickets section** (list tickets where `was_overridden == true`)

---

### Stage 6 — `VALIDATION_COMPLETE`

**Responsibility:** Run automated checks against all artifacts.

This stage is also exposed as a standalone `validate.py` script (SHOULD ATTEMPT §6).

#### Checks Performed

| Check | Description |
|---|---|
| Required artifacts exist | All files in the required artifacts list are present |
| JSON files are valid | Parse each JSON/JSONL file without errors |
| Inputs read from disk | `normalized_tickets.json` was generated from `tickets.json` (ticket IDs match) |
| Normalization before LLM | `normalized_tickets.json` timestamp precedes any LLM call (if logged) |
| One prediction per ticket | `triage_predictions.json` has exactly one record per ticket in `normalized_tickets.json` |
| Category values valid | All `category` values in `allowed_categories` |
| Priority values valid | All `priority` values in `allowed_priorities` |
| Route mappings correct | `final_route_to` = `routing_rules[final_category]` for every ticket |
| Overrides applied | Tickets in `review_overrides.json` have corresponding changes in `final_queue.json` |
| Reply word count | `len(suggested_reply.split()) <= reply_style.max_words` for all tickets |

Validation exits with code `0` on success, non-zero on failure, printing a clear summary.

---

### Stage 7 — `RESULTS_FINALISED`

**Responsibility:** Pipeline completion. Print a brief summary to stdout.

**Actions:**
1. Print total tickets processed, overrides applied, escalations flagged.
2. List output artifact file paths.
3. Exit with code `0`.

---

## Confidence and Escalation (SHOULD ATTEMPT §5)

After `TRIAGE_PREDICTED`, a deterministic escalation check is applied to each ticket:

```
escalate if:
    category == "other"
    OR confidence < 0.60
```

Output: `escalations.json`

```json
[
  {
    "ticket_id": "string",
    "category": "string",
    "confidence": 0.45,
    "reason_for_escalation": "string"
  }
]
```

`reason_for_escalation` is generated by code:
- `"Category is 'other'"` if category is other
- `"Low confidence: {confidence:.2f}"` if confidence below threshold
- Both reasons combined if both apply

This logic is **not delegated to the LLM**.

---

## LLM Call Logging (STRETCH §8)

Every LLM call is appended to `llm_calls.jsonl` (one JSON object per line):

```json
{
  "stage": "TRIAGE_PREDICTED",
  "timestamp": "2026-05-10T09:00:00Z",
  "provider": "string",
  "model": "string",
  "prompt_hash": "sha256-hex-string",
  "input_artifacts": ["normalized_tickets.json", "triage_config.json"],
  "output_artifact": "triage_predictions.json"
}
```

- `prompt_hash` is a SHA-256 hash of the exact prompt string sent to the LLM. This enables replay verification.
- `provider` and `model` are read from environment variables or a config file (not hardcoded).

---

## LLM Integration Design

### Provider: Google Gemini (`google-genai` SDK)

The `llm/client.py` module wraps the Gemini API behind a simple interface:

```python
def call_llm(prompt: str, stage: str, response_schema: dict) -> str:
    ...
```

- Uses `google.genai.Client` initialized with `GEMINI_API_KEY` from `.env`.
- Model is read from `GEMINI_MODEL` env var.
- Every call is logged to `llm_calls.jsonl` (STRETCH §8).

### Prompt Design for Stage `TRIAGE_PREDICTED`

The prompt will:
1. Set the role: act as a support triage assistant.
2. Embed the full `triage_config.json` (allowed categories, priorities, reply tone, routing rules).
3. Embed all normalized tickets using the `text_for_model` field.
4. Instruct the model to output a JSON array — one object per ticket — and nothing else.
5. Remind the model of the word limit for `suggested_reply` (`reply_style.max_words`).
6. Request a `confidence` float between 0.0 and 1.0 per ticket.

### Structured Output via Gemini JSON Mode

The Gemini API call is made with:
```python
response_mime_type="application/json"
response_schema={ ... }  # Pydantic-style schema for the array of predictions
```

This forces Gemini to return valid, schema-conforming JSON. After receiving the response, the pipeline's own code still validates and enforces every constraint. Gemini is never trusted as the authority on allowed values.

---


## Data Flow Diagram

```
tickets.json  ─────────────────────────────────────────────────────────────┐
triage_config.json  ────────────────────────────────────────────────────┐  │
                                                                        │  │
                                        INIT -> INPUTS_LOADED           │  │
                                                    │                   │  │
                         (deterministic code)       v                   │  │
                                        TICKETS_NORMALIZED              │  │
                                                    │                   │  │
                                        normalized_tickets.json         │  │
                                                    │                   │  │
                       (one LLM call, all tickets)  v                   │  │
                                        TRIAGE_PREDICTED <──────────────┘──┘
                                                    │
                                        triage_predictions.json
                                        escalations.json (optional)
                                        llm_calls.jsonl (optional)
                                                    │
                           (interactive terminal)   v
                                   HUMAN_REVIEW_COMPLETE
                                                    │
                                        review_overrides.json
                                                    │
                           (deterministic merge)    v
                                   FINAL_QUEUE_GENERATED
                                                    │
                                        final_queue.json
                                        queue_summary.md
                                                    │
                           (automated checks)       v
                                   VALIDATION_COMPLETE
                                                    │
                                                    v
                                   RESULTS_FINALISED
```

---

## Environment Configuration

Configuration is loaded from a `.env` file in the project root using `python-dotenv`.

Example `.env`:
```
GEMINI_API_KEY=your-api-key-here
GEMINI_MODEL=gemini-2.0-flash
```

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Google Gemini API key |
| `GEMINI_MODEL` | Gemini model identifier (e.g. `gemini-2.0-flash`, `gemini-1.5-pro`) |

The `.env` file must **not** be committed to version control. A `.env.example` template will be included instead.

---

## Replay Policy

Every run of `pipeline.py` **overwrites all output artifacts**, regardless of whether they already exist. This guarantees clean, reproducible outputs from a fresh run — matching the evaluator's expectation of a clean-checkout run.

Outputs overwritten on each run:
- `normalized_tickets.json`
- `triage_predictions.json`
- `review_overrides.json`
- `final_queue.json`
- `queue_summary.md`
- `escalations.json` (if applicable)
- `llm_calls.jsonl` (if applicable)

---

## Summary of Output Artifacts

| Artifact | Stage Produced | Required? |
|---|---|---|
| `normalized_tickets.json` | `TICKETS_NORMALIZED` | Required |
| `triage_predictions.json` | `TRIAGE_PREDICTED` | Required |
| `review_overrides.json` | `HUMAN_REVIEW_COMPLETE` | Required |
| `final_queue.json` | `FINAL_QUEUE_GENERATED` | Required |
| `queue_summary.md` | `FINAL_QUEUE_GENERATED` | Required |
| `escalations.json` | `TRIAGE_PREDICTED` (post-check) | Should Attempt |
| `llm_calls.jsonl` | Each LLM call | Stretch |
| Validation output | `VALIDATION_COMPLETE` | Should Attempt |
