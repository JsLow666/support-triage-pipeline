# Support Triage Pipeline

A replayable, human-in-the-loop support-triage pipeline. It reads support tickets and triage rules from disk, deterministically normalizes ticket text, uses an LLM to classify the full batch, accepts human corrections, and produces a final agent queue and summary.

## What it does

For every ticket, the pipeline:

1. Loads `tickets.json` and `triage_config.json`.
2. Creates deterministic normalized records in `normalized_tickets.json` before an LLM call is made.
3. Makes one batch LLM request to classify tickets and draft replies.
4. Enforces categories, priorities, routing, confidence bounds, and reply-length limits in code.
5. Displays predictions and pauses for a reviewer to provide corrections.
6. Creates the post-review final queue and a Markdown summary.
7. Validates generated artifacts.

The enforced pipeline order is:

```text
INIT
 -> INPUTS_LOADED
 -> TICKETS_NORMALIZED
 -> TRIAGE_PREDICTED
 -> HUMAN_REVIEW_COMPLETE
 -> FINAL_QUEUE_GENERATED
 -> VALIDATION_COMPLETE
 -> RESULTS_FINALISED
```

## Requirements

- Python 3.10 or newer
- A Google Gemini API key

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Create a `.env` file from the example:

```bash
copy .env.example .env
```

Set the required value in `.env`:

```dotenv
GEMINI_API_KEY=your_api_key_here
```

Optionally choose a Gemini model:

```dotenv
GEMINI_MODEL=gemini-3.8-flash
```

## Input files

### `tickets.json`

Provide a JSON array of support tickets. Each ticket requires:

```json
{
  "ticket_id": "T-1001",
  "customer_id": "C-001",
  "subject": "Charged twice",
  "message": "I can see two charges for one deposit.",
  "channel": "email",
  "created_at": "2026-05-10T09:15:00Z"
}
```

### `triage_config.json`

This file is the source of truth for the controlled category and priority values, reply style, and routing destinations:

```json
{
  "allowed_categories": ["billing_issue", "account_access", "product_how_to", "bug_report", "other"],
  "allowed_priorities": ["urgent", "high", "normal", "low"],
  "reply_style": {
    "tone": "clear, polite, concise",
    "max_words": 80
  },
  "routing_rules": {
    "billing_issue": "payments_queue",
    "account_access": "trust_and_access_queue",
    "product_how_to": "general_support_queue",
    "bug_report": "technical_queue",
    "other": "manual_review_queue"
  }
}
```

## Run the pipeline

From the project directory, run:

```bash
python pipeline.py
```

During the human-review stage, predicted category and priority values are printed for every ticket. To correct one, enter:

```text
ticket_id,category,priority
```

For example:

```text
T-1004,account_access,normal
```

Enter an empty line to finish review. Override category and priority values must be present in `triage_config.json`. When a category changes, its destination queue is recalculated from the configured routing rule.

## Generated artifacts

Each run regenerates these artifacts in the project root:

| File | Description |
|---|---|
| `normalized_tickets.json` | Deterministically prepared ticket data, including `text_for_model` and `char_count`. |
| `triage_predictions.json` | Config-constrained batch LLM predictions, suggested replies, routes, and confidence. |
| `review_overrides.json` | Reviewer corrections, including the original and replacement values. |
| `final_queue.json` | Final post-review ticket queue for support agents. |
| `queue_summary.md` | Ticket totals and breakdowns by category, priority, destination, and overrides. |
| `escalations.json` | Tickets flagged deterministically because their category is `other` or confidence is below `0.60`. |

The pipeline also handles malformed or missing individual LLM records without failing the entire batch. Such tickets are placed in the `other` category with a manual-review route.

## Validate artifacts

After a pipeline run, validate the generated artifacts with:

```bash
python validate.py
```

The validation process checks that required artifacts exist and contain valid JSON, normalized and predicted ticket IDs align with the source input, configured category/priority values and routes are respected, overrides are reflected in the final queue, and suggested replies meet the configured word limit.

## Notes

- Input tickets and configuration are always read from disk; the implementation does not rely on the sample ticket IDs or wording.
- Ticket normalization is deterministic Python code and does not use the LLM.
- Routing is always derived from `triage_config.json`, rather than trusted from LLM output.
- Generated artifacts are overwritten on each run so a run can be replayed from current inputs.
