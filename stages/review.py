"""
Stage: HUMAN_REVIEW_COMPLETE

Displays triage predictions in the terminal and pauses for interactive
human input. The reviewer may override category and/or priority for
any ticket. All overrides are validated against triage_config.json
before being accepted.

Override values affect final_queue.json — they are not merely logged.

Output: review_overrides.json
"""

import json
from pathlib import Path

OVERRIDES_PATH = Path(__file__).parent.parent / "review_overrides.json"


def display_predictions(predictions: list) -> None:
    """
    Print current predictions to the terminal for reviewer inspection.

    Each row shows ticket ID, predicted category, and priority so the
    reviewer can quickly identify any tickets that need correction.
    """
    print("\n" + "─" * 64)
    print("  HUMAN REVIEW  —  Predicted Triage Results")
    print("─" * 64)
    for p in predictions:
        print(
            f"  [{p['ticket_id']}]  "
            f"Category: {p['category']:<22} "
            f"Priority: {p['priority']}"
        )
    print("─" * 64 + "\n")


def parse_override_line(
    line: str,
    config: dict,
    prediction_ids: set,
) -> dict | None:
    """
    Parse and validate a single override input line.

    Expected format: ticket_id,category,priority

    Validation checks:
      - Exactly three comma-separated fields.
      - ticket_id exists in the current predictions.
      - category is in allowed_categories.
      - priority is in allowed_priorities.

    Args:
        line: Raw input line from the reviewer.
        config: Parsed triage_config.json.
        prediction_ids: Set of known ticket IDs.

    Returns:
        A dict with ticket_id, category, priority if valid; else None.
    """
    parts = [p.strip() for p in line.split(",")]

    if len(parts) != 3:
        print(
            f"  [ERROR] Wrong format. "
            f"Expected: ticket_id,category,priority  —  got: '{line}'"
        )
        return None

    ticket_id, category, priority = parts

    if ticket_id not in prediction_ids:
        print(f"  [ERROR] Unknown ticket_id: '{ticket_id}'")
        return None

    if category not in config["allowed_categories"]:
        print(
            f"  [ERROR] Invalid category '{category}'. "
            f"Allowed: {config['allowed_categories']}"
        )
        return None

    if priority not in config["allowed_priorities"]:
        print(
            f"  [ERROR] Invalid priority '{priority}'. "
            f"Allowed: {config['allowed_priorities']}"
        )
        return None

    return {"ticket_id": ticket_id, "category": category, "priority": priority}


def run(predictions: list, config: dict) -> list:
    """
    Run the HUMAN_REVIEW_COMPLETE stage.

    Displays all predictions, then enters an input loop where the
    reviewer can enter override lines one at a time. An empty line
    ends the session. All overrides are validated before being stored.

    If a ticket is overridden more than once, the latest override wins.

    If review_overrides.json already exists from a prior run it is
    overwritten, consistent with the always-overwrite replay policy.

    Args:
        predictions: Validated predictions from TRIAGE_PREDICTED.
        config: Parsed triage_config.json.

    Returns:
        List of override dicts (empty list if no overrides were entered).
    """
    display_predictions(predictions)

    # Build lookup map for quick access to current predictions
    pred_by_id     = {p["ticket_id"]: p for p in predictions}
    prediction_ids = set(pred_by_id.keys())

    print("Enter any overrides as: ticket_id,category,priority")
    print("Press Enter on an empty line when done.\n")

    overrides  = []
    seen_ids   = set()  # Track tickets already overridden this session

    while True:
        try:
            line = input("  Override: ").strip()
        except EOFError:
            # Non-interactive mode (e.g. piped stdin) — treat as done
            break

        if line == "":
            break

        parsed = parse_override_line(line, config, prediction_ids)
        if parsed is None:
            continue  # Error already printed; prompt the reviewer again

        tid = parsed["ticket_id"]

        if tid in seen_ids:
            # Replace the earlier override for the same ticket
            overrides = [o for o in overrides if o["ticket_id"] != tid]
            print(f"  [WARN] Duplicate override for {tid} — replacing previous entry.")

        old = pred_by_id[tid]
        overrides.append({
            "ticket_id":    tid,
            "old_category": old["category"],
            "new_category": parsed["category"],
            "old_priority": old["priority"],
            "new_priority": parsed["priority"],
        })
        seen_ids.add(tid)
        print(f"  [OK]  Override recorded for {tid}.")

    OVERRIDES_PATH.write_text(
        json.dumps(overrides, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return overrides
