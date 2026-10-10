"""
Stage: FINAL_QUEUE_GENERATED

Merges triage predictions with human review overrides and produces
the final agent queue artifacts. If a ticket was overridden, its
category and priority are updated and route_to is recomputed from
routing_rules in config (never carried over blindly from predictions).

Output: final_queue.json, queue_summary.md
"""

import json
from collections import Counter
from pathlib import Path

QUEUE_PATH   = Path(__file__).parent.parent / "final_queue.json"
SUMMARY_PATH = Path(__file__).parent.parent / "queue_summary.md"


def run(predictions: list, overrides: list, config: dict) -> list:
    """
    Run the FINAL_QUEUE_GENERATED stage.

    For each ticket, merges the prediction with any human override.
    If overridden, final_category and final_priority are taken from the
    override and final_route_to is recomputed from routing_rules.

    Writes final_queue.json and queue_summary.md.

    Args:
        predictions: Validated predictions from TRIAGE_PREDICTED.
        overrides: Overrides from HUMAN_REVIEW_COMPLETE.
        config: Parsed triage_config.json.

    Returns:
        List of final queue dicts.
    """
    routing = config["routing_rules"]

    # Index overrides by ticket_id for O(1) lookup
    override_by_id = {o["ticket_id"]: o for o in overrides}

    final_queue = []
    for pred in predictions:
        tid      = pred["ticket_id"]
        override = override_by_id.get(tid)

        if override:
            final_category = override["new_category"]
            final_priority = override["new_priority"]
            was_overridden = True
        else:
            final_category = pred["category"]
            final_priority = pred["priority"]
            was_overridden = False

        # route_to is always recomputed from config, never carried over
        final_route_to = routing[final_category]

        final_queue.append({
            "ticket_id":       tid,
            "final_category":  final_category,
            "final_priority":  final_priority,
            "final_route_to":  final_route_to,
            "suggested_reply": pred["suggested_reply"],
            "was_overridden":  was_overridden,
        })

    # Write final_queue.json
    QUEUE_PATH.write_text(
        json.dumps(final_queue, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Write human-readable summary
    _write_summary(final_queue)

    return final_queue


def _write_summary(final_queue: list) -> None:
    """
    Generate queue_summary.md with counts and a destination breakdown.

    Sections:
      1. Total ticket count
      2. Count by final category (table)
      3. Count by final priority (table)
      4. Queue breakdown by destination queue (table)
      5. Overridden tickets (table or 'none' note)
    """
    total       = len(final_queue)
    cat_counts  = Counter(t["final_category"] for t in final_queue)
    pri_counts  = Counter(t["final_priority"]  for t in final_queue)
    route_counts = Counter(t["final_route_to"]  for t in final_queue)
    overridden  = [t for t in final_queue if t["was_overridden"]]

    lines = [
        "# Queue Summary\n",
        f"**Total tickets processed:** {total}\n",
        "---\n",
        "## Count by Final Category\n",
        "| Category | Count |",
        "|---|---|",
    ]
    for cat, count in sorted(cat_counts.items()):
        lines.append(f"| {cat} | {count} |")

    lines += [
        "\n---\n",
        "## Count by Final Priority\n",
        "| Priority | Count |",
        "|---|---|",
    ]
    for pri, count in sorted(pri_counts.items()):
        lines.append(f"| {pri} | {count} |")

    lines += [
        "\n---\n",
        "## Queue Breakdown by Destination\n",
        "| Destination Queue | Count |",
        "|---|---|",
    ]
    for route, count in sorted(route_counts.items()):
        lines.append(f"| {route} | {count} |")

    lines += ["\n---\n", "## Overridden Tickets\n"]
    if overridden:
        lines.append("| Ticket ID | Final Category | Final Priority |")
        lines.append("|---|---|---|")
        for t in overridden:
            lines.append(
                f"| {t['ticket_id']} | {t['final_category']} | {t['final_priority']} |"
            )
    else:
        lines.append("_No tickets were overridden in this run._")

    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
