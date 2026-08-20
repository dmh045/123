from __future__ import annotations

import json
from pathlib import Path

from hara_agent.cli import main as cli_main
from hara_agent.services.analysis import MethodContractASILService
from hara_agent.template import TemplateRoleCompiler
from hara_agent.workflow import HARAState


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "references" / "HARA_Template_AI_20260327.xlsx"


def _method():
    return TemplateRoleCompiler().compile_method(TEMPLATE, use_manifest=False)


def test_runtime_asil_lookup_preserves_compiled_na_and_exact_source():
    method = _method()
    service = MethodContractASILService(method)

    assert service.determine("S0", "E0", "C0") == "NA"
    source = service.evidence_source("S0", "E0", "C0")
    assert source.source_type == "method_contract"
    assert source.location.startswith("ASIL_Table!")
    assert method.metadata["template_hash"] in source.excerpt


def test_checkpoint_round_trip_retains_method_contract_identity():
    state = HARAState(
        run_id="method-contract-state",
        method_contract={
            "template_hash": "abc",
            "contract_version": "method-contract-v2",
        },
    )

    loaded = HARAState.from_dict(state.to_dict())

    assert loaded.method_contract == state.method_contract


def test_doctor_reports_method_contract_and_never_claims_release_ready(
    monkeypatch, capsys
):
    class ValidLLMConfig:
        def validate(self):
            return None

    monkeypatch.setattr(
        "hara_agent.cli.LLMConfig.from_env", lambda: ValidLLMConfig()
    )

    result = cli_main(["doctor", "--template", str(TEMPLATE)])
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert payload["ready_for_draft"] is True
    assert payload["ready_for_release"] is False
    assert payload["checks"]["method_contract"]["ok"] is True
    assert "domain_profile" not in payload["checks"]
    assert "template_hash" in payload["checks"]["method_contract"]
