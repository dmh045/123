"""Thin generic entry point for reusable offline evaluation modules."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.evaluation.pre_r4_selector import run_pre_r4_selector_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evaluation", choices=("pre-r4-selector",))
    args = parser.parse_args()
    result = run_pre_r4_selector_audit(ROOT)
    print(
        f"evaluation={args.evaluation} groups={result['parent_groups']} "
        f"provider_calls={result['provider_calls']} decision={result['decision']}"
    )


if __name__ == "__main__":
    main()
