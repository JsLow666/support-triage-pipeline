"""
Stage: TRIAGE_PREDICTED

Makes a single Gemini LLM call to classify all normalized tickets,
then applies deterministic post-processing to enforce config constraints.

Also implements:
  - Batch resilience (STRETCH §7): malformed or missing ticket
    predictions fall back to a safe 'other' record instead of crashing.
  - Confidence and escalation flagging (SHOULD ATTEMPT §5): tickets
    with category 'other' or confidence < 0.60 are written to
    escalations.json using pure Python logic — no LLM.

Output: triage_predictions.json, escalations.json
"""

import json
from pathlib import Path

from llm.client import call_llm

PREDICTIONS_PATH     = Path(__file__).parent.parent / "triage_predictions.json"
ESCALATIONS_PATH     = Path(__file__).parent.parent / "escalations.json"

# Escalation threshold (SHOULD ATTEMPT §5)
CONFIDENCE_THRESHOLD = 0.60

# Safe fallback values used for batch resilience (STRETCH §7)
FALLBACK_CATEGORY = "other"
FALLBACK_PRIORITY = "normal"


# ── Prompt construction ───────────────────────────────────────────────────────

def build_prompt(normalized_tickets: list, config: dict) -> str:
    """
    Build the batch triage prompt for Gemini.

    Embeds all ticket text and triage config into a single prompt,
    instructing the model to return a JSON array with one prediction
    object per ticket.

    Args:
        normalized_tickets: Output of the TICKETS_NORMALIZED stage.
        config: Parsed triage_config.json.

    Returns:
        A formatted prompt string ready to send to the LLM.
    """
    ticket_block = "\n\n".join(
        f"ticket_id: {t['ticket_id']}\n{t['text_for_model']}"
        for t in normalized_tickets
    )

    allowed_cats = config["allowed_categories"]
    allowed_pris = config["allowed_priorities"]
    tone         = config["reply_style"]["tone"]
    max_words    = config["reply_style"]["max_words"]

    return f"""You are a customer support triage assistant.

Your job is to classify each support ticket below and draft a suggested reply.

## Allowed categories — you MUST use exactly one of these strings:
{json.dumps(allowed_cats, indent=2)}

## Allowed priorities — you MUST use exactly one of these strings:
{json.dumps(allowed_pris, indent=2)}

## Reply style:
- Tone: {tone}
- Maximum words for suggested_reply: {max_words} words (do not exceed this limit)

## Tickets to classify:
{ticket_block}

## Output format:
Return a JSON array with exactly one object per ticket, in the same order as above.
Each object must have these keys:
  - ticket_id      (string) — copy from the ticket
  - category       (string) — one of the allowed categories
  - priority       (string) — one of the allowed priorities
  - confidence     (number) — your classification confidence, between 0.0 and 1.0
  - reason         (string) — brief explanation of your classification
  - suggested_reply (string) — customer-facing reply following the reply style

Return ONLY the JSON array. No prose, no markdown fences, no explanation outside the array.
"""


# ── Post-LLM constraint enforcement (deterministic) ──────────────────────────

def enforce_constraints(raw: dict, config: dict) -> dict:
    """
    Validate and enforce config constraints on a single raw prediction.

    Rules applied deterministically in code (not by the LLM):
      - category must be in allowed_categories; falls back to 'other'.
      - priority must be in allowed_priorities; falls back to 'normal'.
      - route_to is always recomputed from routing_rules — never trusted
        from the model output.
      - confidence is clamped to [0.0, 1.0]; defaults to 0.5 if absent.
      - suggested_reply is word-truncated if it exceeds max_words.

    Args:
        raw: A single prediction dict from the LLM response.
        config: Parsed triage_config.json.

    Returns:
        A validated and constraint-enforced prediction dict.
    """
    allowed_cats = config["allowed_categories"]
    allowed_pris = config["allowed_priorities"]
    routing      = config["routing_rules"]
    max_words    = config["reply_style"]["max_words"]

    # Category — fall back to 'other' if the model returned an unknown value
    category = raw.get("category", "")
    if category not in allowed_cats:
        category = FALLBACK_CATEGORY

    # Priority — fall back to 'normal' if unknown
    priority = raw.get("priority", "")
    if priority not in allowed_pris:
        priority = FALLBACK_PRIORITY

    # route_to is always derived from config, never from the model
    route_to = routing[category]

    # Confidence — clamp to [0.0, 1.0]; default 0.5 on error
    try:
        confidence = float(raw.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5

    # Reply word-count enforcement
    reply = raw.get("suggested_reply", "")
    words = reply.split()
    if len(words) > max_words:
        reply = " ".join(words[:max_words])

    return {
        "ticket_id":       raw.get("ticket_id", ""),
        "category":        category,
        "priority":        priority,
        "confidence":      confidence,
        "reason":          raw.get("reason", ""),
        "suggested_reply": reply,
        "route_to":        route_to,
    }


def make_fallback(ticket_id: str, config: dict, reason: str) -> dict:
    """
    Create a safe fallback prediction for a malformed or missing LLM result.

    Used for batch resilience (STRETCH §7): a single bad model output
    will not crash the entire pipeline. The ticket is placed in the
    'other' category with 0.0 confidence, which also triggers escalation.

    Args:
        ticket_id: The affected ticket's ID.
        config: Parsed triage_config.json (for routing lookup).
        reason: Description of why the fallback was needed.

    Returns:
        A safe prediction dict for the ticket.
    """
    return {
        "ticket_id":       ticket_id,
        "category":        FALLBACK_CATEGORY,
        "priority":        FALLBACK_PRIORITY,
        "confidence":      0.0,
        "reason":          f"Parse error — {reason}",
        "suggested_reply": "",
        "route_to":        config["routing_rules"][FALLBACK_CATEGORY],
    }


# ── Escalation check (deterministic, SHOULD ATTEMPT §5) ──────────────────────

def check_escalations(predictions: list) -> list:
    """
    Flag tickets for manual escalation using deterministic rules only.

    A ticket is escalated if either condition holds:
      - category == 'other'
      - confidence < CONFIDENCE_THRESHOLD (0.60)

    This logic is pure Python — the LLM plays no role here.

    Args:
        predictions: List of validated prediction dicts.

    Returns:
        List of escalation dicts for tickets that meet escalation criteria.
    """
    escalations = []

    for p in predictions:
        reasons = []
        if p["category"] == "other":
            reasons.append("Category is 'other'")
        if p["confidence"] < CONFIDENCE_THRESHOLD:
            reasons.append(f"Low confidence: {p['confidence']:.2f}")

        if reasons:
            escalations.append({
                "ticket_id":            p["ticket_id"],
                "category":             p["category"],
                "confidence":           p["confidence"],
                "reason_for_escalation": "; ".join(reasons),
            })

    return escalations


# ── Stage entry point ─────────────────────────────────────────────────────────

def run(normalized_tickets: list, config: dict) -> tuple:
    """
    Run the TRIAGE_PREDICTED stage.

    Steps:
      1. Build a batch prompt from all normalized tickets and config.
      2. Make a single Gemini call.
      3. Parse the JSON response.
      4. For each normalized ticket, find its prediction and enforce
         config constraints deterministically. Missing or malformed
         records receive a safe fallback (batch resilience, §7).
      5. Write triage_predictions.json.
      6. Run escalation check and write escalations.json (§5).

    Args:
        normalized_tickets: Output from TICKETS_NORMALIZED stage.
        config: Parsed triage_config.json.

    Returns:
        Tuple of (predictions list, escalations list).
    """
    prompt = build_prompt(normalized_tickets, config)
    raw_response = call_llm(prompt)

    # Parse the top-level JSON array from the model
    try:
        raw_list = json.loads(raw_response)
        if not isinstance(raw_list, list):
            raise ValueError("Expected a JSON array at the top level.")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"  [WARN] LLM response could not be parsed as a JSON array: {exc}")
        raw_list = []

    # Index raw predictions by ticket_id for O(1) lookup
    raw_by_id = {}
    for item in raw_list:
        if isinstance(item, dict) and "ticket_id" in item:
            raw_by_id[item["ticket_id"]] = item

    # Build one validated prediction per normalized ticket
    predictions = []
    for ticket in normalized_tickets:
        tid = ticket["ticket_id"]

        if tid in raw_by_id:
            try:
                validated = enforce_constraints(raw_by_id[tid], config)
                predictions.append(validated)
            except Exception as exc:
                # Batch resilience: individual record is malformed
                print(f"  [WARN] Ticket {tid} prediction malformed — using fallback: {exc}")
                predictions.append(make_fallback(tid, config, str(exc)))
        else:
            # Batch resilience: ticket absent from LLM response
            print(f"  [WARN] Ticket {tid} missing from LLM response — using fallback.")
            predictions.append(make_fallback(tid, config, "missing from LLM response"))

    # Write predictions to disk
    PREDICTIONS_PATH.write_text(
        json.dumps(predictions, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Escalation check — pure Python logic (SHOULD ATTEMPT §5)
    escalations = check_escalations(predictions)
    ESCALATIONS_PATH.write_text(
        json.dumps(escalations, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return predictions, escalations
