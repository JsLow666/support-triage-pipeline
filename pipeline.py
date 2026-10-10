"""
Support-Triage Pipeline — Main Entry Point

Orchestrates the full pipeline in strict stage order:

    INIT
     -> INPUTS_LOADED
     -> TICKETS_NORMALIZED
     -> TRIAGE_PREDICTED
     -> HUMAN_REVIEW_COMPLETE
     -> FINAL_QUEUE_GENERATED
     -> VALIDATION_COMPLETE
     -> RESULTS_FINALISED

No stage may run until its predecessor has completed successfully.
All output artifacts are overwritten on every run (replay policy).

Usage:
    python pipeline.py
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from stages import normalize, predict, review, queue, validate_stage

# ── Stage name constants (must match requirement.md exactly) ──────────────────
INIT                  = "INIT"
INPUTS_LOADED         = "INPUTS_LOADED"
TICKETS_NORMALIZED    = "TICKETS_NORMALIZED"
TRIAGE_PREDICTED      = "TRIAGE_PREDICTED"
HUMAN_REVIEW_COMPLETE = "HUMAN_REVIEW_COMPLETE"
FINAL_QUEUE_GENERATED = "FINAL_QUEUE_GENERATED"
VALIDATION_COMPLETE   = "VALIDATION_COMPLETE"
RESULTS_FINALISED     = "RESULTS_FINALISED"

# ── Input file paths ──────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
TICKETS_PATH = BASE_DIR / "tickets.json"
CONFIG_PATH  = BASE_DIR / "triage_config.json"


def _banner(text: str) -> None:
    """Print a simple section banner to stdout."""
    width = 62
    print(f"\n{'═' * width}")
    print(f"  {text}")
    print(f"{'═' * width}\n")


def _stage_log(stage: str, message: str) -> None:
    """Print a stage-prefixed log line to stdout."""
    print(f"[{stage}] {message}")


def load_inputs() -> tuple:
    """
    Read and parse both input files from disk.

    Validates that triage_config.json contains all required top-level
    keys before returning, so downstream stages can rely on their
    presence without repeating the check.

    Returns:
        Tuple of (tickets list, config dict).

    Raises:
        FileNotFoundError: If either input file is missing.
        ValueError: If triage_config.json is missing required keys.
    """
    with open(TICKETS_PATH, "r", encoding="utf-8") as f:
        tickets = json.load(f)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    required_keys = ["allowed_categories", "allowed_priorities", "reply_style", "routing_rules"]
    missing_keys  = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(
            f"triage_config.json is missing required keys: {missing_keys}"
        )

    return tickets, config


def run_pipeline() -> None:
    """
    Run the full support-triage pipeline from INIT to RESULTS_FINALISED.

    Each stage updates the pipeline state variable. If a stage raises
    an exception the pipeline logs the error and exits with code 1,
    preventing any later stage from running on partial data.
    """
    load_dotenv()

    _banner("SUPPORT TRIAGE PIPELINE")
    state = INIT
    _stage_log(state, "Pipeline starting…")

    # ── INPUTS_LOADED ─────────────────────────────────────────────────────────
    try:
        tickets, config = load_inputs()
        state = INPUTS_LOADED
        _stage_log(state, f"Loaded {len(tickets)} ticket(s) from disk.")
    except Exception as exc:
        print(f"[ERROR] Failed during {state} → {INPUTS_LOADED}: {exc}")
        sys.exit(1)

    # ── TICKETS_NORMALIZED ────────────────────────────────────────────────────
    try:
        normalized = normalize.run(tickets)
        state = TICKETS_NORMALIZED
        _stage_log(state, f"Normalized {len(normalized)} ticket(s)  →  normalized_tickets.json")
    except Exception as exc:
        print(f"[ERROR] Failed during {state}: {exc}")
        sys.exit(1)

    # ── TRIAGE_PREDICTED ──────────────────────────────────────────────────────
    try:
        _stage_log(TRIAGE_PREDICTED, "Calling Gemini for batch triage prediction…")
        predictions, escalations = predict.run(normalized, config)
        state = TRIAGE_PREDICTED
        _stage_log(state, f"Predicted {len(predictions)} ticket(s)  →  triage_predictions.json")
        if escalations:
            _stage_log(state, f"Flagged {len(escalations)} escalation(s)  →  escalations.json")
    except Exception as exc:
        print(f"[ERROR] Failed during {state}: {exc}")
        sys.exit(1)

    # ── HUMAN_REVIEW_COMPLETE ─────────────────────────────────────────────────
    try:
        overrides = review.run(predictions, config)
        state = HUMAN_REVIEW_COMPLETE
        _stage_log(state, f"{len(overrides)} override(s) recorded  →  review_overrides.json")
    except Exception as exc:
        print(f"[ERROR] Failed during {state}: {exc}")
        sys.exit(1)

    # ── FINAL_QUEUE_GENERATED ─────────────────────────────────────────────────
    try:
        final_queue = queue.run(predictions, overrides, config)
        state = FINAL_QUEUE_GENERATED
        _stage_log(state, "Final queue written  →  final_queue.json,  queue_summary.md")
    except Exception as exc:
        print(f"[ERROR] Failed during {state}: {exc}")
        sys.exit(1)

    # ── VALIDATION_COMPLETE ───────────────────────────────────────────────────
    try:
        validate_stage.run(config)
        state = VALIDATION_COMPLETE
        _stage_log(state, "All validation checks passed.")
    except ValueError as exc:
        print(f"[ERROR] {state} failed:\n{exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] Unexpected error during {state}: {exc}")
        sys.exit(1)

    # ── RESULTS_FINALISED ─────────────────────────────────────────────────────
    state = RESULTS_FINALISED
    overridden_count = sum(1 for t in final_queue if t.get("was_overridden"))

    _banner("RESULTS_FINALISED")

    print(f"  Tickets processed  :  {len(final_queue)}")
    print(f"  Overrides applied  :  {overridden_count}")
    print(f"  Escalations flagged:  {len(escalations)}")

    print("\n  Artifacts produced:")
    artifacts = [
        "normalized_tickets.json",
        "triage_predictions.json",
        "review_overrides.json",
        "final_queue.json",
        "queue_summary.md",
    ]
    if escalations:
        artifacts.append("escalations.json")
    for artifact in artifacts:
        print(f"    ✓  {artifact}")

    print(f"\n{'═' * 62}")
    print("  Pipeline complete.")
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    run_pipeline()
