from __future__ import annotations

from collections import defaultdict
import hashlib
import os
from pathlib import Path

import pytest

from hara_agent.workflow.checkpoints import CheckpointRepository
from hara_agent.workflow.scenario_causal_revalidation import (
    ScenarioCausalRevalidationRunner,
)


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.getenv("HARA_RUN_HISTORICAL_GUARDS") != "1",
    reason="historical runtime guard is opt-in",
)


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest().upper()


def test_completed_r3_artifacts_are_immutable_and_resumable():
    source_run_id = "hara-full-baseline-20260912-r1-synthesis-r3"
    target_run_id = f"{source_run_id}-causal-r2"
    checkpoint_path = ROOT / "runtime/agent" / f"{source_run_id}.checkpoint.json"
    trace_path = ROOT / "runtime/review" / target_run_id / "causal_revalidation_provider_trace.json"
    risk_path = ROOT / "runtime/agent" / (
        f"{target_run_id}-risk-rescore-r2.checkpoint.json"
    )
    assert _sha256(checkpoint_path) == "2DBBCBFCF7AECC09332A8BBC2642513D98A2558E1CD33305F432415351E7D357"
    assert _sha256(trace_path) == "8359E7A35EB4DF8E77D9E16D2CAD6151D4327CEC6D7EA57B48603BA159E25BC7"
    assert _sha256(risk_path) == "56150B23AA487FA780D1780E2F250035618DC057CFBA5F9F6F594916E57C0100"

    state = CheckpointRepository(ROOT / "runtime/agent").load(source_run_id)
    candidates_by_malfunction = defaultdict(list)
    for scenario in state.scenarios:
        candidates_by_malfunction[
            str(scenario.analysis_instance["malfunction_id"])
        ].append(scenario)
    recovered = ScenarioCausalRevalidationRunner._recover_completed(
        trace_path, source_run_id=source_run_id, target_run_id=target_run_id,
        candidates_by_malfunction=dict(candidates_by_malfunction),
    )
    assert len(recovered) == 57
    assert sum(len(value[0]) for value in recovered.values()) == 1236
