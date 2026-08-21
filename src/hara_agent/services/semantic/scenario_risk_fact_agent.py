from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from typing import Any, Sequence

from hara_agent.contracts import FactOrigin, RequiredFactSpec
from hara_agent.infrastructure.llm import LLMClient, LLMRequest
from hara_agent.models import (
    FactProvenance, MalfunctionCandidate, ReviewStatus, RiskFact,
    ScenarioCandidate, SourceRef,
)


class ScenarioRiskFactAgent:
    """Interpret only template-requested scenario facts from exact evidence.

    The output is deliberately template-independent ``RiskFact`` data. Template
    identity is introduced later by ``MethodRiskFactBindingService``.
    HUMAN_EVIDENCE and DERIVED_FACT requirements are never guessed here.
    """

    PROMPT_VERSION = "scenario-risk-facts-v1"

    def __init__(self, client: LLMClient):
        self.client = client

    @staticmethod
    def _allowed_values(spec: RequiredFactSpec) -> list[str]:
        values: list[str] = []
        for constraint in spec.constraints:
            values.extend(re.findall(r"'([^']+)'", constraint))
            match = re.search(r"\bEQ\s+([A-Z][A-Z0-9_]*)", constraint)
            if match:
                values.append(match.group(1))
        return list(dict.fromkeys(values))

    @staticmethod
    def _evidence(
        candidates: Sequence[ScenarioCandidate],
        malfunctions: Sequence[MalfunctionCandidate],
    ) -> tuple[list[dict[str, str]], dict[str, SourceRef]]:
        refs = list(dict.fromkeys(
            source
            for item in [*candidates, *malfunctions]
            for source in item.sources
            if source.source_id and source.excerpt
        ))
        registry = {f"E{index:04d}": source for index, source in enumerate(refs, start=1)}
        return ([
            {
                "evidence_id": evidence_id,
                "source_type": source.source_type,
                "source_id": source.source_id,
                "location": source.location,
                "excerpt": source.excerpt,
            }
            for evidence_id, source in registry.items()
        ], registry)

    def interpret(
        self,
        assessments: Sequence[dict[str, Any]],
        candidates: Sequence[ScenarioCandidate],
        malfunctions: Sequence[MalfunctionCandidate],
        required_specs: Sequence[RequiredFactSpec],
    ) -> tuple[list[RiskFact], dict[str, Any]]:
        specs = [item for item in required_specs if item.origin is FactOrigin.SCENARIO_FACT]
        retained = [item for item in assessments if all((
            item.get("physically_feasible") is True,
            item.get("functionally_relevant") is True,
            item.get("causally_relevant") is True,
            bool(item.get("risk_dimensions_changed")),
        ))]
        if not specs or not retained:
            return [], {
                "prompt_version": self.PROMPT_VERSION,
                "requested_pair_count": len(retained),
                "requested_fact_type_count": len(specs),
                "found_fact_count": 0,
                "missing_fact_count": len(retained) * len(specs),
                "llm_call_count": 0,
            }

        candidate_by_id = {item.scenario_id: item for item in candidates}
        malfunction_by_id = {item.malfunction_id: item for item in malfunctions}
        evidence, registry = self._evidence(candidates, malfunctions)
        requests = []
        expected = set()
        for assessment in retained:
            scenario_id = str(assessment.get("scenario_id", ""))
            malfunction_id = str(assessment.get("malfunction_id", ""))
            if scenario_id not in candidate_by_id or malfunction_id not in malfunction_by_id:
                raise ValueError(
                    f"Risk fact interpretation foreign key missing: {malfunction_id}/{scenario_id}"
                )
            candidate = candidate_by_id[scenario_id]
            malfunction = malfunction_by_id[malfunction_id]
            for spec in specs:
                expected.add((malfunction_id, scenario_id, spec.fact_type.value))
            requests.append({
                "malfunction_id": malfunction_id,
                "scenario_id": scenario_id,
                "malfunction": malfunction.description,
                "hazard": malfunction.vehicle_level_hazard,
                "scenario": candidate.situational_description,
                "scenario_detail": candidate.situational_detailing,
                "hazardous_event": str(assessment.get("hazardous_event", "")),
                "potential_harm": str(assessment.get("potential_harm", "")),
            })
        schema = [{
            "fact_type": spec.fact_type.value,
            "unit": spec.unit,
            "allowed_values": self._allowed_values(spec),
            "constraints": list(spec.constraints),
        } for spec in specs]
        request = LLMRequest(
            task="interpret_scenario_risk_facts",
            schema_name="ScenarioRiskFacts",
            prompt_version=self.PROMPT_VERSION,
            system_prompt=(
                "You are a bounded evidence interpreter. Return only facts explicitly supported by "
                "the supplied evidence excerpts. Do not estimate speed, frequency, duration, or "
                "avoidability. Do not use template identity or invent engineering rules."
            ),
            user_prompt=(
                "Return JSON {\"results\":[...]}. Return exactly one result for every "
                "malfunction_id/scenario_id/fact_type request. status is FOUND or NOT_FOUND. "
                "FOUND requires value, unit, and one or more evidence_ids. Categorical values must "
                "come from allowed_values; numeric values must be JSON numbers. Evidence IDs must "
                "come from evidence. No Markdown.\n"
                f"fact_specs={json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n"
                f"scenario_pairs={json.dumps(requests, ensure_ascii=False, separators=(',', ':'))}\n"
                f"evidence={json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}"
            ),
            metadata={"pair_count": len(retained), "fact_type_count": len(specs)},
            max_tokens=min(
                int(getattr(getattr(self.client, "config", None), "max_tokens", 4096)), 4096
            ),
        )
        response = self.client.complete_json(request)
        raw_results = response.data.get("results")
        if not isinstance(raw_results, list):
            raise ValueError("Scenario risk fact response.results must be an array")
        seen = set()
        facts: list[RiskFact] = []
        spec_by_type = {item.fact_type.value: item for item in specs}
        for raw in raw_results:
            if not isinstance(raw, dict):
                raise ValueError("Scenario risk fact result must be an object")
            key = (
                str(raw.get("malfunction_id", "")),
                str(raw.get("scenario_id", "")),
                str(raw.get("fact_type", "")),
            )
            if key not in expected or key in seen:
                raise ValueError(f"Unexpected or duplicate scenario risk fact result: {key}")
            seen.add(key)
            status = str(raw.get("status", "")).upper()
            if status == "NOT_FOUND":
                continue
            if status != "FOUND":
                raise ValueError(f"Invalid scenario risk fact status: {status!r}")
            spec = spec_by_type[key[2]]
            value = raw.get("value")
            allowed = self._allowed_values(spec)
            if allowed:
                value = str(value).strip().upper()
                if value not in allowed:
                    raise ValueError(f"Risk fact value is outside the compiled ontology: {key}")
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Numeric scenario risk fact requires a JSON number: {key}")
            unit = str(raw.get("unit", ""))
            if unit != spec.unit:
                raise ValueError(f"Scenario risk fact unit mismatch: {key}")
            evidence_ids = raw.get("evidence_ids")
            if not isinstance(evidence_ids, list) or not evidence_ids:
                raise ValueError(f"Scenario risk fact requires evidence IDs: {key}")
            try:
                sources = list(dict.fromkeys(registry[str(item)] for item in evidence_ids))
            except KeyError as exc:
                raise ValueError(f"Unknown scenario risk fact evidence ID: {exc.args[0]}") from exc
            material = json.dumps(
                {"key": key, "value": value, "unit": unit},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            )
            facts.append(RiskFact(
                fact_id=f"RF-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20].upper()}",
                parameter=spec.fact_type.value,
                value=value,
                unit=unit,
                context={"malfunction_id": key[0], "scenario_id": key[1]},
                source_refs=sources,
                provenance=FactProvenance.LLM_INFERENCE,
                approval=ReviewStatus.PENDING,
                produced_by=self.PROMPT_VERSION,
            ))
        missing_contract_results = sorted(expected - seen)
        if missing_contract_results:
            raise ValueError(
                f"Scenario risk fact response is incomplete: {missing_contract_results[:5]}"
            )
        return facts, {
            "prompt_version": self.PROMPT_VERSION,
            "model": response.model,
            "request_id": response.request_id,
            "usage": response.usage,
            "requested_pair_count": len(retained),
            "requested_fact_type_count": len(specs),
            "found_fact_count": len(facts),
            "missing_fact_count": len(expected) - len(facts),
            "llm_call_count": 1,
            "facts": [asdict(item) for item in facts],
        }
