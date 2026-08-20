"""Regenerate the compact MethodContract regression fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.template import TemplateRoleCompiler  # noqa: E402


TEMPLATE = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"


def main() -> None:
    method = TemplateRoleCompiler().compile_method(TEMPLATE, use_manifest=False)
    target = ROOT / "tests" / "fixtures" / "method_contract" / (
        f"{method.metadata['template_hash']}.method-contract.golden.json"
    )
    target.write_text(
        json.dumps(method.audit_snapshot(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
