from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src", ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from hara_agent.workflow import ReviewArtifactWriter


@pytest.fixture(scope="session", autouse=True)
def materialize_c9f_r3_review_fixture() -> None:
    """Provide the deterministic R3 audit projection required by audit tests.

    These tests previously relied on an ignored runtime directory created by a
    prior manual validation run.  Materializing the fixture at test time keeps
    a fresh clone self-contained without any provider calls.
    """
    review_root = Path(__file__).resolve().parents[1] / "runtime" / "review"
    run_id = "hara-c9f-validation-r3"
    target = review_root / run_id / "scenario_feasibility.jsonl"
    if target.is_file():
        return

    writer = ReviewArtifactWriter(run_id, review_root)
    for index in range(54):
        scenario_id = f"SCN-R3-{index:02d}"
        writer.record_scenario_candidate({
            "scenario_id": scenario_id,
            "operating_scenario": "fixture",
            "situational_description": "deterministic audit fixture",
            "situational_detailing": "no scored risk inputs are supplied",
            "facts": {},
            "status": "FINALIZED",
        })
        writer.record_scenario_feasibility({
            "malfunction_id": "MF-R3",
            "scenario_id": scenario_id,
            "status": "FINALIZED",
            "physically_feasible": True,
            "functionally_relevant": True,
            "causally_relevant": True,
            "causal_assessment": {"causal_chain": ["M", "B", "I", "H"]},
        })
