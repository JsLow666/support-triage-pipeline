"""
Stage: TICKETS_NORMALIZED

Deterministically normalizes raw tickets into a standard form
suitable for LLM consumption. No LLM calls are made in this stage.

Output: normalized_tickets.json
"""

import json
import re
from pathlib import Path

OUTPUT_PATH = Path(__file__).parent.parent / "normalized_tickets.json"


def build_text_for_model(subject: str, message: str) -> str:
    """
    Construct the text_for_model field from subject and message.

    Strips leading/trailing whitespace from both fields, collapses
    internal whitespace sequences to a single space, then formats
    them as a labelled block. This is fully deterministic — no LLM.

    Args:
        subject: Raw ticket subject line.
        message: Raw ticket message body.

    Returns:
        A single formatted string ready to embed in an LLM prompt.
    """
    subject = re.sub(r"\s+", " ", subject.strip())
    message = re.sub(r"\s+", " ", message.strip())
    return f"Subject: {subject}\nMessage: {message}"


def run(tickets: list) -> list:
    """
    Normalize all raw tickets and write normalized_tickets.json.

    For each ticket, produces a record that includes the original
    fields plus text_for_model (deterministic concatenation) and
    char_count (length of text_for_model in characters).

    This stage must complete and be written to disk before any
    LLM call is made.

    Args:
        tickets: List of raw ticket dicts loaded from tickets.json.

    Returns:
        List of normalized ticket dicts.
    """
    normalized = []

    for ticket in tickets:
        text = build_text_for_model(ticket["subject"], ticket["message"])

        normalized.append({
            "ticket_id":      ticket["ticket_id"],
            "subject":        ticket["subject"].strip(),
            "message":        ticket["message"].strip(),
            "channel":        ticket["channel"],
            "created_at":     ticket["created_at"],
            "text_for_model": text,
            "char_count":     len(text),
        })

    OUTPUT_PATH.write_text(
        json.dumps(normalized, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return normalized
