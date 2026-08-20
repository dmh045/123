#!/usr/bin/env python3
"""P0-2c2 bounded real-provider Scenario evaluation; never runs Full HARA."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hara_agent.config import LLMConfig
from hara_agent.contracts import (
    CausalMechanismDefinition, CausalMechanismPremise,
    InMemoryCausalMechanismCatalog, ScenarioEvidenceV2ContractError,
)
from hara_agent.domains import default_domain_registry
from hara_agent.evaluation.metrics.scenario_real_provider import (
    build_v1_v2_differential, compare_order_attempts, summarize_provider_attempts,
)
from hara_agent.infrastructure.llm import LLMRequest, LLMResponse, create_llm_client
from hara_agent.models import (
    EvidenceKind, FactProvenance, ItemDefinitionFacts, MalfunctionCandidate,
    ReviewStatus, ScenarioCandidate, SourceRef,
)
from hara_agent.services.analysis import AVPScenarioCandidateService, ProjectFactResolver
from hara_agent.services.semantic import (
    ScenarioFeasibilityAgent, ScenarioProviderContractError,
    build_project_evidence_registry,
)
from hara_agent.services.semantic.scenario_batching import ScenarioSchemaContractError
from hara_agent.services.semantic.scenario_evidence import (
    FactRegistry, ScenarioEvidenceContractError, build_fact_registry,
)
from hara_agent.services.semantic.scenario_provider_contract import validate_v2_envelope
from hara_agent.workflow import CheckpointRepository


REAL_MALFUNCTION_ID = "MF-F01-001"
REAL_SCENARIO_ID = (
    "SCN-NEAR-PEDESTRIAN-TRAJECTORY-FRONT-DRIVER-INSIDE-DIRECT-CONTROL"
)
EDGE_IDS = ("M_TO_B", "B_TO_I", "I_TO_H", "H_TO_HARM")
SYNTHETIC_SOURCE = SourceRef(
    "test_fixture", "P0-2c2", "controlled closed-world synthetic gold"
)


@dataclass
class ProviderCall:
    request: LLMRequest
    response: LLMResponse | None
    error: Exception | None
    elapsed_seconds: float
    evaluation_context: dict[str, Any]


class RecordingScenarioClient:
    """Record non-secret Provider metadata and raw parsed payloads for evaluation."""

    def __init__(self, client):
        self.client = client
        self.config = client.config
        self.calls: list[ProviderCall] = []
        self.evaluation_context: dict[str, Any] = {}

    def complete_json(self, request: LLMRequest) -> LLMResponse:
        if request.task != "assess_scenario_feasibility":
            raise RuntimeError(f"P0-2c2 prohibits non-Scenario task: {request.task}")
        started = time.monotonic()
        try:
            response = self.client.complete_json(request)
        except Exception as error:
            self.calls.append(ProviderCall(
                request, None, error, round(time.monotonic() - started, 3),
                dict(self.evaluation_context),
            ))
            raise
        self.calls.append(ProviderCall(
            request, response, None, round(time.monotonic() - started, 3),
            dict(self.evaluation_context),
        ))
        return response


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P0-2c2 bounded real-provider Scenario semantic evaluation"
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument(
        "--checkpoint-run-id", default="avp-downstream-smoke-v1"
    )
    parser.add_argument(
        "--project-facts-report", type=Path,
        default=ROOT / "runtime/evaluation/p0-1-9-fresh-20260817-007.json",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "runtime/evaluation"
    )
    parser.add_argument("--timestamp")
    parser.add_argument(
        "--experiments", nargs="+",
        choices=(
            "synthetic_negative", "synthetic_positive", "order_a", "order_b",
            "real_avp_v2", "real_avp_v1",
        ),
        help="Explicit bounded subset; omitted runs the complete experiment plan.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build identities/config only; make zero Provider calls.",
    )
    return parser


def _load_local_env(path: Path) -> None:
    """Load dotenv without logging or returning any credential value."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def _provider_metadata(config: LLMConfig) -> dict[str, Any]:
    parsed = urlsplit(config.base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "configured"
    return {
        "provider": config.provider,
        "model": config.model,
        "base_url_origin": origin,
        "base_url_sha256": hashlib.sha256(config.base_url.encode()).hexdigest(),
        "temperature": "provider_default",
        "top_p": "provider_default",
        "timeout_seconds": config.timeout_seconds,
        "max_tokens": config.max_tokens,
        "max_retries": config.max_retries,
        "retry_backoff_seconds": config.retry_backoff_seconds,
        "scenario_thinking": config.scenario_thinking,
    }


def _synthetic_fixtures():
    malfunction = MalfunctionCandidate(
        "MF-P0-2C2-SYNTHETIC", "FUN-P0-2C2", "TEST_ONLY",
        "TEST_ONLY closed-world malfunction M occurs.",
        "TEST_ONLY fixture defines the direct behavior transition B.",
        "TEST_ONLY hazard exists only when the fixture explicitly permits I_TO_H.",
        ["M", "B", "I", "H", "HARM"],
        sources=[SYNTHETIC_SOURCE], status=ReviewStatus.FINALIZED, confidence=1.0,
    )
    provenance = {
        key: {
            "provenance": FactProvenance.PROJECT_INPUT.value,
            "approval": ReviewStatus.FINALIZED.value,
            "source_refs": [SYNTHETIC_SOURCE],
            "classification": "TEST_ONLY",
        }
        for key in (
            "m_to_b_transition", "b_to_i_transition", "i_to_h_transition",
            "positive_chain_contract", "relative_distance", "relative_speed_kph",
        )
    }
    negative_facts = {
        "m_to_b_transition": "TRUE by TEST_ONLY fixture definition",
        "b_to_i_transition": "TRUE by TEST_ONLY fixture definition",
        "i_to_h_transition": (
            "FALSE by TEST_ONLY closed-world fixture definition; interaction I is explicitly "
            "isolated from every hazardous state H"
        ),
    }
    positive_facts = {
        "positive_chain_contract": (
            "TRUE by TEST_ONLY fixture definition for all four M_TO_B, B_TO_I, "
            "I_TO_H and H_TO_HARM transitions"
        ),
        "relative_distance": "10 m",
        "relative_speed_kph": 18.0,
    }
    negative = ScenarioCandidate(
        "SCN-P0-2C2-SYNTHETIC-NEGATIVE", "TEST_ONLY closed world",
        "M_TO_B and B_TO_I are true; I_TO_H is explicitly false.",
        "No AVP engineering judgment is used.", negative_facts,
        fact_provenance={key: provenance[key] for key in negative_facts},
        sources=[SYNTHETIC_SOURCE], status=ReviewStatus.FINALIZED,
        source_scenario_id="P0-2C2-SYNTHETIC-NEGATIVE",
        atomic_variant="closed_world_i_to_h_false",
        semantic_fingerprint=_fingerprint("negative", negative_facts),
    )
    positive = ScenarioCandidate(
        "SCN-P0-2C2-SYNTHETIC-POSITIVE", "TEST_ONLY closed world",
        "All causal transitions are true by fixture contract.",
        "The approved TEST_ONLY mechanism requires direct and derived evidence.",
        positive_facts,
        fact_provenance={key: provenance[key] for key in positive_facts},
        sources=[SYNTHETIC_SOURCE], status=ReviewStatus.FINALIZED,
        source_scenario_id="P0-2C2-SYNTHETIC-POSITIVE",
        atomic_variant="mixed_direct_derived_positive",
        semantic_fingerprint=_fingerprint("positive", positive_facts),
    )
    negative_mechanism = CausalMechanismDefinition(
        "TEST-MECH-CLOSED-WORLD-TRUE-EDGE", "1",
        "TEST_ONLY: supports one edge only when an explicit fixture transition fact says TRUE; "
        "never applies to a transition declared FALSE.",
        (CausalMechanismPremise(
            "transition_contract", EvidenceKind.DIRECT_FACT, True,
            "explicit TEST_ONLY declaration for the exact edge",
        ),),
        "The exact TEST_ONLY transition is supported", (SYNTHETIC_SOURCE,),
        ReviewStatus.FINALIZED, FactProvenance.DOMAIN_POLICY,
        {"classification": "TEST_ONLY"},
    )
    positive_mechanism = CausalMechanismDefinition(
        "TEST-MECH-MIXED-DIRECT-DERIVED", "1",
        "TEST_ONLY: applies only to the controlled positive fixture and requires both its "
        "explicit closed-world transition contract and its derived TTC evidence.",
        (
            CausalMechanismPremise(
                "transition_contract", EvidenceKind.DIRECT_FACT, True,
                "explicit TEST_ONLY positive-chain declaration",
            ),
            CausalMechanismPremise(
                "ttc", EvidenceKind.DERIVED_PHYSICS, True,
                "precomputed TEST_ONLY time-to-contact",
            ),
        ),
        "The requested positive-fixture causal transition is supported",
        (SYNTHETIC_SOURCE,), ReviewStatus.FINALIZED,
        FactProvenance.DOMAIN_POLICY, {"classification": "TEST_ONLY"},
    )
    return malfunction, negative, positive, negative_mechanism, positive_mechanism


def _fingerprint(name: str, facts: dict[str, Any]) -> str:
    material = json.dumps(
        {"fixture": name, "facts": facts}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _load_real_pair(args):
    state = CheckpointRepository(ROOT / "runtime/agent").load(args.checkpoint_run_id)
    raw_malfunction = next(
        item for item in state.malfunctions
        if item.get("malfunction_id") == REAL_MALFUNCTION_ID
    )
    malfunction = MalfunctionCandidate(
        malfunction_id=raw_malfunction["malfunction_id"],
        function_id=raw_malfunction["function_id"],
        guideword=raw_malfunction["guideword"],
        description=raw_malfunction["description"],
        functional_effect=raw_malfunction["functional_effect"],
        vehicle_level_hazard=raw_malfunction["vehicle_level_hazard"],
        causal_chain=list(raw_malfunction.get("causal_chain", [])),
        sources=[SourceRef(**item) for item in raw_malfunction.get("sources", [])],
        status=ReviewStatus(raw_malfunction.get("status", "PENDING")),
        confidence=float(raw_malfunction.get("confidence", 0.0)),
        model_local_id=str(raw_malfunction.get("model_local_id", "")),
    )
    project_report = json.loads(args.project_facts_report.read_text(encoding="utf-8"))
    facts = ItemDefinitionFacts.from_dict(project_report["project_facts"])
    project_registry = build_project_evidence_registry(facts)
    speed = ProjectFactResolver().resolve_speed_context(facts, "parking")
    runtime = default_domain_registry().create("avp", require_approved=False)
    candidates, candidate_audit = AVPScenarioCandidateService(runtime.policy).generate(
        operating_mode=speed.operating_mode,
        speed_resolution=speed,
        project_driver_contexts=None,
        project_exposure_inputs=list(facts.exposure_inputs),
        allow_legacy_speed_fallback=False,
    )
    scenario = next(item for item in candidates if item.scenario_id == REAL_SCENARIO_ID)
    return malfunction, scenario, project_registry, {
        "checkpoint_run_id": args.checkpoint_run_id,
        "project_facts_report": str(args.project_facts_report.resolve()),
        "project_facts_sha256": hashlib.sha256(args.project_facts_report.read_bytes()).hexdigest(),
        "candidate_audit": candidate_audit,
        "semantic_fingerprint": scenario.semantic_fingerprint,
        "available_production_mechanism_count": 0,
    }


def _run_experiment(
    *,
    run_id: str,
    client: RecordingScenarioClient,
    malfunction: MalfunctionCandidate,
    scenarios: list[ScenarioCandidate],
    contract: str,
    catalog: InMemoryCausalMechanismCatalog,
    repeat: int,
    project_registry: FactRegistry | None = None,
    batch_max_chars: int | None = None,
    gold_by_id: dict[str, dict[str, Any]] | None = None,
    progress_path: Path | None = None,
) -> dict[str, Any]:
    experiment_call_start = len(client.calls)
    agent = ScenarioFeasibilityAgent(
        client, assessment_contract=contract, mechanism_catalog=catalog,
        batch_max_chars=batch_max_chars,
    )
    attempt_records: list[dict[str, Any]] = []
    logical_calls = 0
    early_stop = None
    consecutive_schema_errors: list[str] = []
    for attempt_index in range(1, repeat + 1):
        logical_calls += 1
        call_start = len(client.calls)
        client.evaluation_context = {
            "run_id": run_id,
            "attempt_id": f"{run_id}-a{attempt_index:02d}",
            "scenario_identities": [{
                "scenario_id": item.scenario_id,
                "semantic_fingerprint": item.semantic_fingerprint,
            } for item in scenarios],
        }
        outer_error = None
        try:
            agent.assess(
                malfunction, scenarios, project_registry=project_registry,
            )
        except Exception as error:
            outer_error = error
        calls = client.calls[call_start:]
        records = _records_from_logical_attempt(
            run_id=run_id,
            attempt_index=attempt_index,
            contract=contract,
            agent=agent,
            malfunction=malfunction,
            scenarios=scenarios,
            project_registry=project_registry,
            catalog=catalog,
            calls=calls,
            outer_error=outer_error,
        )
        attempt_records.extend(records)
        if progress_path is not None:
            _write_report(progress_path, {
                "report_schema_version": "p0-2c2-scenario-real-provider-v1",
                "classification": "EVALUATION_ONLY",
                "status": "IN_PROGRESS",
                "run_id": run_id,
                "contract": contract,
                "prompt_version": agent.prompt_version,
                "assessment_contract_version": agent.assessment_contract_version,
                "catalog_fingerprint": agent.mechanism_catalog_fingerprint,
                "provider_schema_fingerprint": agent.provider_schema_fingerprint,
                "requested_repeat": repeat,
                "completed_logical_calls": logical_calls,
                "scenario_identities": [{
                    "scenario_id": item.scenario_id,
                    "semantic_fingerprint": item.semantic_fingerprint,
                } for item in scenarios],
                "attempts": attempt_records,
                "provider_calls": [
                    _public_call_audit(item)
                    for item in client.calls[experiment_call_start:]
                ],
            })
        schema_errors = sorted({
            str(item.get("error", {}).get("code", ""))
            for item in records if not item.get("schema_valid")
        })
        if records and len(schema_errors) == 1 and all(
            not item.get("schema_valid") for item in records
        ):
            consecutive_schema_errors.append(schema_errors[0])
        else:
            consecutive_schema_errors.clear()
        if (
            len(consecutive_schema_errors) >= 3
            and len(set(consecutive_schema_errors[-3:])) == 1
        ):
            early_stop = {
                "status": "EARLY_STOP_SCHEMA_FAILURE",
                "after_logical_calls": logical_calls,
                "error_code": consecutive_schema_errors[-1],
            }
            break
    scenario_metrics = {}
    for scenario in scenarios:
        actual = [
            item for item in attempt_records
            if item["scenario_id"] == scenario.scenario_id
            and item["semantic_fingerprint"] == scenario.semantic_fingerprint
        ]
        scenario_metrics[scenario.scenario_id] = summarize_provider_attempts(
            actual, gold=(gold_by_id or {}).get(scenario.scenario_id),
        )
    relevant_calls = [
        _public_call_audit(item) for item in client.calls[experiment_call_start:]
    ]
    return {
        "report_schema_version": "p0-2c2-scenario-real-provider-v1",
        "classification": "EVALUATION_ONLY",
        "run_id": run_id,
        "contract": contract,
        "prompt_version": agent.prompt_version,
        "assessment_contract_version": agent.assessment_contract_version,
        "provider_schema_version": agent.schema_name,
        "catalog_fingerprint": agent.mechanism_catalog_fingerprint,
        "provider_schema_fingerprint": agent.provider_schema_fingerprint,
        "contract_cache_fingerprint": agent.contract_cache_fingerprint,
        "available_mechanism_count": len(catalog.list_definitions()),
        "requested_repeat": repeat,
        "logical_calls": logical_calls,
        "provider_call_count": len(relevant_calls),
        "transport_attempts": sum(item.get("transport_attempts", 0) for item in relevant_calls),
        "transport_retries": sum(max(0, item.get("transport_attempts", 0) - 1) for item in relevant_calls),
        "format_retry_calls": sum(item.get("format_retry_calls", 0) for item in relevant_calls),
        "finish_reason_distribution": dict(sorted(Counter(
            item.get("finish_reason", "unknown") for item in relevant_calls
        ).items())),
        "early_stop": early_stop,
        "scenario_identities": [{
            "scenario_id": item.scenario_id,
            "semantic_fingerprint": item.semantic_fingerprint,
        } for item in scenarios],
        "scenario_metrics": scenario_metrics,
        "attempts": attempt_records,
        "provider_calls": relevant_calls,
    }


def _records_from_logical_attempt(
    *, run_id, attempt_index, contract, agent, malfunction, scenarios,
    project_registry, catalog, calls, outer_error,
) -> list[dict[str, Any]]:
    successful = [item for item in calls if item.response is not None]
    assessments: list[dict[str, Any]] = []
    envelope_error = None
    if not successful:
        envelope_error = outer_error or RuntimeError("Provider returned no parsed response")
    else:
        try:
            for call in successful:
                data = call.response.data
                if contract == "v2" and set(data) != {"assessments"}:
                    validate_v2_envelope(data, [item.scenario_id for item in scenarios])
                raw = data.get("assessments")
                if not isinstance(raw, list):
                    raise ScenarioSchemaContractError("Provider envelope lacks assessments array")
                assessments.extend(item for item in raw if isinstance(item, dict))
            ids = [str(item.get("scenario_id", "")) for item in assessments]
            expected = [item.scenario_id for item in scenarios]
            if contract == "v2":
                validate_v2_envelope({"assessments": assessments}, expected)
            elif len(ids) != len(set(ids)) or set(ids) != set(expected):
                raise ScenarioSchemaContractError(
                    f"v1 exact coverage failure expected={expected} actual={ids}"
                )
        except Exception as error:
            envelope_error = error
    raw_by_id = {
        str(item.get("scenario_id", "")): item for item in assessments
    }
    last_call = calls[-1] if calls else None
    response = last_call.response if last_call and last_call.response else None
    usage = dict(response.usage or {}) if response else {}
    records = []
    for scenario in scenarios:
        raw = raw_by_id.get(scenario.scenario_id)
        provider_verdict = (
            raw.get("causally_relevant")
            if isinstance(raw, dict) and isinstance(raw.get("causally_relevant"), bool)
            else None
        )
        schema_valid = envelope_error is None and raw is not None
        contract_valid = False
        error = envelope_error
        if schema_valid:
            try:
                if contract == "v2":
                    from hara_agent.services.semantic.scenario_provider_contract import (
                        parse_v2_assessment,
                    )
                    parse_v2_assessment(
                        raw, malfunction=malfunction, scenario=scenario,
                        registry=build_fact_registry(
                            malfunction, scenario, project_registry
                        ),
                        catalog=catalog,
                    )
                else:
                    agent._parse(
                        malfunction, raw, scenario=scenario,
                        batch=f"{attempt_index}/1", split_path="root", split_depth=0,
                        project_registry=project_registry,
                    )
                contract_valid = True
            except ScenarioProviderContractError as caught:
                schema_valid = False
                error = caught
            except ScenarioSchemaContractError as caught:
                schema_valid = False
                error = caught
            except (ScenarioEvidenceV2ContractError, ScenarioEvidenceContractError) as caught:
                error = caught
            except Exception as caught:
                schema_valid = False
                error = caught
        error_payload = _error_payload(error) if error else None
        records.append({
            "run_id": run_id,
            "attempt_id": f"{run_id}-a{attempt_index:02d}",
            "logical_attempt": attempt_index,
            "scenario_id": scenario.scenario_id,
            "semantic_fingerprint": scenario.semantic_fingerprint,
            "provider": str(getattr(agent.client.config, "provider", "")),
            "model": response.model if response else "",
            "request_id": response.request_id if response else "",
            "prompt_version": agent.prompt_version,
            "assessment_contract_version": agent.assessment_contract_version,
            "catalog_fingerprint": agent.mechanism_catalog_fingerprint,
            "finish_reason": usage.get("finish_reason", "error" if error else "unknown"),
            "transport_attempt_count": int(usage.get(
                "transport_attempts", getattr(error, "attempts", 0) or 0
            )),
            "format_retry_calls": int(usage.get("format_retry_calls", 0)),
            "schema_valid": schema_valid,
            "contract_valid": contract_valid,
            "provider_causal_verdict": provider_verdict,
            "provider_assessment": _assessment_summary(raw),
            "error": error_payload,
            "no_applicable_approved_mechanism": (
                contract == "v2" and len(catalog.list_definitions()) == 0
            ),
        })
    return records


def _assessment_summary(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    changes = raw.get("risk_dimension_changes", [])
    return {
        "breakpoint": raw.get("breakpoint"),
        "risk_dimensions": [
            item.get("dimension") for item in changes
            if isinstance(item, dict) and item.get("dimension")
        ] if isinstance(changes, list) else [],
        "hazardous_event_nonempty": bool(str(raw.get("hazardous_event", "")).strip()),
        "potential_harm_nonempty": bool(str(raw.get("potential_harm", "")).strip()),
        "edges": raw.get("edges", []) if isinstance(raw.get("edges"), list) else [],
        "v1_causal_chain": (
            raw.get("causal_chain", {})
            if isinstance(raw.get("causal_chain"), dict) else {}
        ),
    }


def _error_payload(error: Exception) -> dict[str, Any]:
    code = getattr(error, "code", type(error).__name__)
    return {
        "code": getattr(code, "value", str(code)),
        "type": type(error).__name__,
        "reason": getattr(error, "reason", str(error)),
        "edge_id": getattr(error, "edge_id", ""),
        "hop": getattr(error, "hop", ""),
        "evidence_ref": getattr(error, "evidence_ref", ""),
        "mechanism_code": getattr(error, "mechanism_code", ""),
    }


def _public_call_audit(call: ProviderCall) -> dict[str, Any]:
    response = call.response
    usage = dict(response.usage or {}) if response else {}
    error = call.error
    return {
        "run_id": call.evaluation_context.get("run_id", ""),
        "attempt_id": call.evaluation_context.get("attempt_id", ""),
        "scenario_identities": call.evaluation_context.get("scenario_identities", []),
        "task": call.request.task,
        "prompt_version": call.request.prompt_version,
        "assessment_contract_version": call.request.metadata.get(
            "assessment_contract_version", ""
        ),
        "catalog_fingerprint": call.request.metadata.get(
            "mechanism_catalog_fingerprint", ""
        ),
        "schema_name": call.request.schema_name,
        "request_id": response.request_id if response else "",
        "model": response.model if response else "",
        "finish_reason": usage.get("finish_reason", "error" if error else "unknown"),
        "transport_attempts": int(usage.get(
            "transport_attempts", getattr(error, "attempts", 0) or 0
        )),
        "format_retry_calls": int(usage.get("format_retry_calls", 0)),
        "transient_error_counts": usage.get("transient_error_counts", {}),
        "elapsed_seconds": call.elapsed_seconds,
        "error": _error_payload(error) if error else None,
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _single_metrics(report: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    return report["scenario_metrics"][scenario_id]


def _acceptance(summary: dict[str, Any]) -> dict[str, Any]:
    negative = summary["synthetic_negative"]["metrics"]
    positive = summary["synthetic_positive"]["metrics"]
    order = summary["batch_order"]["comparison"]
    real = summary["real_avp_v2"]["metrics"]
    neg_gold = negative.get("gold", {})
    pos_gold = positive.get("gold", {})
    schema_ready = all(
        metrics.get("attempt_count") == 5
        and metrics.get("schema_valid_rate") == 1.0
        for metrics in (negative, positive)
    )
    semantic_ready = (
        schema_ready
        and negative.get("contract_valid_rate") == 1.0
        and neg_gold.get("causal_gold_agreement") == 1.0
        and neg_gold.get("breakpoint_agreement") == 1.0
        and positive.get("contract_valid_rate") == 1.0
        and pos_gold.get("causal_gold_agreement") == 1.0
        and pos_gold.get("mechanism_selection_agreement") == 1.0
        and pos_gold.get("mechanism_version_agreement") == 1.0
        and pos_gold.get("required_binding_accuracy") == 1.0
        and order.get("order_causal_agreement") == 1.0
        and order.get("order_mechanism_agreement") == 1.0
        and order.get("order_binding_agreement") == 1.0
    )
    real_observable = (
        real.get("schema_valid_rate") == 1.0
        and (real.get("contract_valid_rate") or 0.0) >= 0.8
    )
    expressiveness_gap = (
        summary["real_avp_v2"]["available_production_mechanism_count"] == 0
        and bool(real.get("provider_causal_distribution", {}).get("false", 0))
    )
    knowledge_blocked = (
        summary["real_avp_v2"]["available_production_mechanism_count"] == 0
    )
    if not schema_ready:
        blocker = "PROVIDER_SCHEMA_INSTABILITY"
    elif not semantic_ready:
        pos_errors = positive.get("error_code_distribution", {})
        if any("SUPPORT" in key for key in pos_errors):
            blocker = "SUPPORT_SELECTION_INSTABILITY"
        elif any("MECHANISM" in key for key in pos_errors):
            blocker = "MECHANISM_BINDING_INSTABILITY"
        else:
            blocker = "PROVIDER_SEMANTIC_INSTABILITY"
    elif not real_observable:
        blocker = (
            "KNOWLEDGE_SUBSTRATE_BLOCKED"
            if knowledge_blocked and not real.get("unknown_mechanism_invention_count")
            else "PROVIDER_SEMANTIC_INSTABILITY"
        )
    elif expressiveness_gap:
        blocker = "CONTRACT_EXPRESSIVENESS_GAP"
    elif knowledge_blocked:
        blocker = "KNOWLEDGE_SUBSTRATE_BLOCKED"
    else:
        blocker = "NONE"
    return {
        "provider_schema_stability": "READY" if schema_ready else "NOT_READY",
        "controlled_semantic_stability": "READY" if semantic_ready else "NOT_READY",
        "real_avp_scenario_evidence": "OBSERVATIONAL_ONLY",
        "real_avp_observability_gate_passed": real_observable,
        "contract_expressiveness_gap": expressiveness_gap,
        "knowledge_substrate_blocked": knowledge_blocked,
        "primary_blocker": blocker,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _load_local_env(args.env_file)
    config = LLMConfig.from_env()
    config.validate()
    timestamp = args.timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir.resolve()
    synthetic_malfunction, negative, positive, negative_mech, positive_mech = (
        _synthetic_fixtures()
    )
    real_malfunction, real_scenario, project_registry, real_context = _load_real_pair(args)
    manifest = {
        "timestamp": timestamp,
        "provider": _provider_metadata(config),
        "prompt_modified": False,
        "provider_parameters_modified": False,
        "production_default": "v9/v1",
        "full_hara_run": False,
        "real_scenario": {
            "malfunction_id": real_malfunction.malfunction_id,
            "scenario_id": real_scenario.scenario_id,
            "semantic_fingerprint": real_scenario.semantic_fingerprint,
            **real_context,
        },
    }
    if args.dry_run:
        path = output_dir / f"p0-2c2-dry-run-{timestamp}.json"
        _write_report(path, {**manifest, "provider_logical_calls": 0})
        print(path)
        return 0

    recording = RecordingScenarioClient(create_llm_client(config))
    empty_catalog = InMemoryCausalMechanismCatalog()
    negative_catalog = InMemoryCausalMechanismCatalog((negative_mech,))
    positive_catalog = InMemoryCausalMechanismCatalog((positive_mech,))
    batch_catalog = InMemoryCausalMechanismCatalog((negative_mech, positive_mech))
    neg_gold = {
        negative.scenario_id: {
            "causally_relevant": False, "breakpoint": "I_TO_H",
            "required_edges": ["M_TO_B", "B_TO_I"],
        }
    }
    positive_bindings = {
        "transition_contract": "SCN.positive_chain_contract",
        "ttc": "DERIVED.ttc_s",
    }
    pos_gold = {
        positive.scenario_id: {
            "causally_relevant": True, "breakpoint": "NONE",
            "required_edges": list(EDGE_IDS),
            "mechanism_id": positive_mech.mechanism_id,
            "mechanism_version": positive_mech.version,
            "bindings": positive_bindings,
        }
    }
    all_gold = {**neg_gold, **pos_gold}
    reports = {}
    specs = [
        ("synthetic_negative", f"p0-2c2-synth-neg-{timestamp}",
         synthetic_malfunction, [negative], "v2", negative_catalog, 5, None, neg_gold),
        ("synthetic_positive", f"p0-2c2-synth-pos-{timestamp}",
         synthetic_malfunction, [positive], "v2", positive_catalog, 5, None, pos_gold),
        ("order_a", f"p0-2c2-order-a-{timestamp}",
         synthetic_malfunction, [negative, positive], "v2", batch_catalog, 3, 30000, all_gold),
        ("order_b", f"p0-2c2-order-b-{timestamp}",
         synthetic_malfunction, [positive, negative], "v2", batch_catalog, 3, 30000, all_gold),
        # The fixed atomic AVP request includes the complete P0-1.9 registry
        # snapshot and is ~16k characters.  Keep the evaluation-only batch
        # envelope above that deterministic payload size; this is a local
        # batching limit, not a Provider generation parameter.
        ("real_avp_v2", f"p0-2c2-avp-v2-{timestamp}",
         real_malfunction, [real_scenario], "v2", empty_catalog, 5, 30000, None),
        ("real_avp_v1", f"p0-2c2-avp-v1-{timestamp}",
         real_malfunction, [real_scenario], "v1", empty_catalog, 5, 30000, None),
    ]
    selected = set(args.experiments or [item[0] for item in specs])
    specs = [item for item in specs if item[0] in selected]
    for name, run_id, malfunction, scenarios, contract, catalog, repeat, batch_chars, gold in specs:
        call_start = len(recording.calls)
        report_path = output_dir / f"{run_id}.json"
        report = _run_experiment(
            run_id=run_id, client=recording, malfunction=malfunction,
            scenarios=scenarios, contract=contract, catalog=catalog, repeat=repeat,
            project_registry=(project_registry if name.startswith("real_avp") else None),
            batch_max_chars=batch_chars, gold_by_id=gold,
            progress_path=report_path,
        )
        # Exact slice prevents same-version calls from earlier experiments entering this report.
        sliced = [_public_call_audit(item) for item in recording.calls[call_start:]]
        report["provider_calls"] = sliced
        report["provider_call_count"] = len(sliced)
        report["transport_attempts"] = sum(item["transport_attempts"] for item in sliced)
        report["transport_retries"] = sum(
            max(0, item["transport_attempts"] - 1) for item in sliced
        )
        report["format_retry_calls"] = sum(item["format_retry_calls"] for item in sliced)
        report["finish_reason_distribution"] = dict(sorted(Counter(
            item["finish_reason"] for item in sliced
        ).items()))
        reports[name] = report
        _write_report(report_path, report)

    complete_names = {
        "synthetic_negative", "synthetic_positive", "order_a", "order_b",
        "real_avp_v2", "real_avp_v1",
    }
    if set(reports) != complete_names:
        phase_path = output_dir / f"p0-2c2-phase-{timestamp}.json"
        _write_report(phase_path, {
            "report_schema_version": "p0-2c2-phase-v1",
            "classification": "EVALUATION_ONLY",
            **manifest,
            "experiments": reports,
            "run_ids": [item["run_id"] for item in reports.values()],
            "provider_totals": {
                "logical_calls": sum(item["logical_calls"] for item in reports.values()),
                "provider_calls": len(recording.calls),
                "transport_attempts": sum(
                    _public_call_audit(call)["transport_attempts"]
                    for call in recording.calls
                ),
                "transport_retries": sum(
                    max(0, _public_call_audit(call)["transport_attempts"] - 1)
                    for call in recording.calls
                ),
                "finish_reason_distribution": dict(sorted(Counter(
                    _public_call_audit(call)["finish_reason"] for call in recording.calls
                ).items())),
            },
        })
        print(phase_path)
        return 0

    negative_metrics = _single_metrics(reports["synthetic_negative"], negative.scenario_id)
    positive_metrics = _single_metrics(reports["synthetic_positive"], positive.scenario_id)
    real_v2_metrics = _single_metrics(reports["real_avp_v2"], real_scenario.scenario_id)
    real_v1_metrics = _single_metrics(reports["real_avp_v1"], real_scenario.scenario_id)
    order_comparison = compare_order_attempts(
        reports["order_a"]["attempts"], reports["order_b"]["attempts"]
    )
    summary = {
        "report_schema_version": "p0-2c2-summary-v1",
        "classification": "EVALUATION_ONLY",
        **manifest,
        "synthetic_negative": {
            "run_id": reports["synthetic_negative"]["run_id"],
            "metrics": negative_metrics,
        },
        "synthetic_positive": {
            "run_id": reports["synthetic_positive"]["run_id"],
            "metrics": positive_metrics,
        },
        "batch_order": {
            "order_a_run_id": reports["order_a"]["run_id"],
            "order_b_run_id": reports["order_b"]["run_id"],
            "order_a_metrics": reports["order_a"]["scenario_metrics"],
            "order_b_metrics": reports["order_b"]["scenario_metrics"],
            "comparison": order_comparison,
        },
        "real_avp_v2": {
            "run_id": reports["real_avp_v2"]["run_id"],
            "available_production_mechanism_count": 0,
            "metrics": real_v2_metrics,
        },
        "real_avp_v1": {
            "run_id": reports["real_avp_v1"]["run_id"],
            "metrics": real_v1_metrics,
        },
        "v1_v2_differential": build_v1_v2_differential(
            real_v1_metrics, real_v2_metrics
        ),
        "provider_totals": {
            "logical_calls": sum(report["logical_calls"] for report in reports.values()),
            "provider_calls": len(recording.calls),
            "transport_attempts": sum(
                item["transport_attempts"]
                for item in (_public_call_audit(call) for call in recording.calls)
            ),
            "transport_retries": sum(
                max(0, item["transport_attempts"] - 1)
                for item in (_public_call_audit(call) for call in recording.calls)
            ),
            "format_retry_calls": sum(
                item["format_retry_calls"]
                for item in (_public_call_audit(call) for call in recording.calls)
            ),
            "finish_reason_distribution": dict(sorted(Counter(
                _public_call_audit(call)["finish_reason"] for call in recording.calls
            ).items())),
        },
        "run_ids": [report["run_id"] for report in reports.values()],
        "architecture": {
            "prompt_modified": False,
            "provider_parameters_modified": False,
            "formal_avp_mechanism_added": False,
            "production_default": "v9/v1",
            "full_hara_run": False,
        },
    }
    summary["acceptance"] = _acceptance(summary)
    summary_path = output_dir / f"p0-2c2-summary-{timestamp}.json"
    _write_report(summary_path, summary)
    print(summary_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
