"""
Stage: VALIDATION_COMPLETE

Runs automated correctness checks on all pipeline artifacts.
Can be invoked as part of the pipeline or via the standalone
validate.py script.

Checks performed:
  1. Required artifacts exist on disk.
  2. All JSON artifact files parse without errors.
  3. Normalized ticket IDs match the raw input ticket IDs.
  4. Every normalized ticket has exactly one prediction.
  5. All category values are in allowed_categories.
  6. All priority values are in allowed_priorities.
  7. final_route_to matches routing_rules[final_category] for every ticket.
  8. Human overrides are reflected in final_queue.json.
  9. suggested_reply word count is within reply_style.max_words.
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent

REQUIRED_ARTIFACTS = [
    "normalized_tickets.json",
    "triage_predictions.json",
    "review_overrides.json",
    "final_queue.json",
    "queue_summary.md",
]


def _load_json(filename: str):
    """Load and parse a JSON file from the project root directory."""
    with open(BASE_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def run(config: dict) -> None:
    """
    Execute all validation checks and raise ValueError on any failure.

    All failing checks are collected before raising so the caller
    receives a complete error report rather than one error at a time.

    Args:
        config: Parsed triage_config.json used as the source of truth
                for allowed values and routing rules.

    Raises:
        ValueError: A newline-separated summary of every failed check.
    """
    errors = []

    # ── Check 1: Required artifacts exist ────────────────────────────────────
    missing = [name for name in REQUIRED_ARTIFACTS if not (BASE_DIR / name).exists()]
    for m in missing:
        errors.append(f"Missing required artifact: {m}")
    if missing:
        # Cannot run further checks without the files present
        raise ValueError("\n".join(errors))

    # ── Check 2: JSON files are valid ─────────────────────────────────────────
    for fname in [f for f in REQUIRED_ARTIFACTS if f.endswith(".json")]:
        try:
            _load_json(fname)
        except Exception as exc:
            errors.append(f"Invalid JSON in {fname}: {exc}")

    # Stop early if any JSON file failed to parse
    if errors:
        raise ValueError("\n".join(errors))

    # ── Load all artifacts ────────────────────────────────────────────────────
    normalized  = _load_json("normalized_tickets.json")
    predictions = _load_json("triage_predictions.json")
    overrides   = _load_json("review_overrides.json")
    final_queue = _load_json("final_queue.json")

    normalized_ids  = {t["ticket_id"] for t in normalized}
    prediction_ids  = {p["ticket_id"] for p in predictions}

    allowed_cats = set(config["allowed_categories"])
    allowed_pris = set(config["allowed_priorities"])
    routing      = config["routing_rules"]
    max_words    = config["reply_style"]["max_words"]

    # ── Check 3: Normalization matches raw input ───────────────────────────────
    raw_tickets = _load_json("tickets.json")
    raw_ids     = {t["ticket_id"] for t in raw_tickets}
    if normalized_ids != raw_ids:
        errors.append(
            f"Normalized IDs do not match input ticket IDs. "
            f"Missing: {raw_ids - normalized_ids}. "
            f"Extra: {normalized_ids - raw_ids}."
        )

    # ── Check 4: One prediction per normalized ticket ─────────────────────────
    if prediction_ids != normalized_ids:
        errors.append(
            f"Prediction IDs do not match normalized IDs. "
            f"Missing: {normalized_ids - prediction_ids}. "
            f"Extra: {prediction_ids - normalized_ids}."
        )

    # ── Check 5 & 6: Category and priority values are config-valid ────────────
    for p in predictions:
        if p.get("category") not in allowed_cats:
            errors.append(
                f"Ticket {p['ticket_id']}: invalid category '{p.get('category')}'"
            )
        if p.get("priority") not in allowed_pris:
            errors.append(
                f"Ticket {p['ticket_id']}: invalid priority '{p.get('priority')}'"
            )

    # ── Check 7: Route mappings match config ──────────────────────────────────
    for item in final_queue:
        expected = routing.get(item["final_category"])
        if item["final_route_to"] != expected:
            errors.append(
                f"Ticket {item['ticket_id']}: route_to is '{item['final_route_to']}' "
                f"but config maps '{item['final_category']}' -> '{expected}'"
            )

    # ── Check 8: Human overrides are applied in final outputs ─────────────────
    override_by_id = {o["ticket_id"]: o for o in overrides}
    final_by_id    = {f["ticket_id"]: f for f in final_queue}

    for tid, override in override_by_id.items():
        final = final_by_id.get(tid)
        if final is None:
            errors.append(f"Overridden ticket {tid} not found in final_queue.json")
            continue
        if final["final_category"] != override["new_category"]:
            errors.append(
                f"Ticket {tid}: override category not applied "
                f"(expected '{override['new_category']}', found '{final['final_category']}')"
            )
        if final["final_priority"] != override["new_priority"]:
            errors.append(
                f"Ticket {tid}: override priority not applied "
                f"(expected '{override['new_priority']}', found '{final['final_priority']}')"
            )

    # ── Check 9: Reply word count within limit ────────────────────────────────
    for item in final_queue:
        count = len(item.get("suggested_reply", "").split())
        if count > max_words:
            errors.append(
                f"Ticket {item['ticket_id']}: suggested_reply has {count} words "
                f"(limit is {max_words})"
            )

    if errors:
        formatted = "\n".join(f"  - {e}" for e in errors)
        raise ValueError(f"Validation failed with {len(errors)} error(s):\n{formatted}")
