"""
Standalone validation script (SHOULD ATTEMPT §6).

Checks all generated pipeline artifacts for correctness without
re-running the full pipeline. Useful for CI or post-run verification.

Usage:
    python validate.py

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from stages.validate_stage import run as run_checks


def main() -> None:
    """Load config and run all validation checks, then report results."""
    load_dotenv()

    config_path = Path(__file__).parent / "triage_config.json"
    if not config_path.exists():
        print("[ERROR] triage_config.json not found in the project root.")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    print("\nRunning pipeline artifact validation checks...")
    print("─" * 50)

    try:
        run_checks(config)
        print("✓  All validation checks passed.\n")
        sys.exit(0)
    except ValueError as exc:
        print(f"\n✗  {exc}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
