from __future__ import annotations

import hashlib
import json
import os
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

    PROMPT_VERSION = "scenario-risk-facts-v2"

    def __init__(self, client: LLMClient, *, batch_max_results: int | None = None):
        self.client = client
        self.batch_max_results = batch_max_results or int(os.getenv(
            "HARA_SCENARIO_RISK_FACT_BATCH_MAX_RESULTS", "16"
        ))
        if self.batch_max_results <= 0:
            raise ValueError("HARA_SCENARIO_RISK_FACT_BATCH_MAX_RESULTS must be positive")

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
            if (
                source.source_type == "item_definition"
                and source.source_id
                and source.excerpt
            )
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
            item.get("status") == ReviewStatus.FINALIZED.value,
            item.get("physically_feasible") is True,
            item.get("functionally_relevant") is True,
            item.get("causally_relevant") is True,
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
        pair_material = []
        for assessment in retained:
            scenario_id = str(assessment.get("scenario_id", ""))
            malfunction_id = str(assessment.get("malfunction_id", ""))
            if scenario_id not in candidate_by_id or malfunction_id not in malfunction_by_id:
                raise ValueError(
                    f"Risk fact interpretation foreign key missing: {malfunction_id}/{scenario_id}"
                )
            candidate = candidate_by_id[scenario_id]
            malfunction = malfunction_by_id[malfunction_id]
            pair_material.append({
                "malfunction_id": malfunction_id,
                "scenario_id": scenario_id,
                "malfunction": malfunction.description,
                "hazard": malfunction.vehicle_level_hazard,
                "scenario": candidate.situational_description,
                "scenario_detail": candidate.situational_detailing,
                "hazardous_event": str(assessment.get("hazardous_event", "")),
                "potential_harm": str(assessment.get("potential_harm", "")),
                "candidate": candidate,
                "malfunction_candidate": malfunction,
            })
        pairs_per_batch = max(1, self.batch_max_results // len(specs))
        facts: list[RiskFact] = []
        batch_audits = []
        for start in range(0, len(pair_material), pairs_per_batch):
            batch_facts, batch_audit = self._interpret_batch(
                pair_material[start:start + pairs_per_batch], specs,
            )
            facts.extend(batch_facts)
            batch_audits.append(batch_audit)
        expected_count = len(pair_material) * len(specs)
        return facts, {
            "prompt_version": self.PROMPT_VERSION,
            "models": [item["model"] for item in batch_audits],
            "request_ids": [item["request_id"] for item in batch_audits],
            "usage": {"batches": [item["usage"] for item in batch_audits]},
            "requested_pair_count": len(retained),
            "requested_fact_type_count": len(specs),
            "found_fact_count": len(facts),
            "missing_fact_count": expected_count - len(facts),
            "batch_count": len(batch_audits),
            "coverage_repair_count": sum(
                item["coverage_repair_count"] for item in batch_audits
            ),
            "llm_call_count": sum(item["llm_call_count"] for item in batch_audits),
            "facts": [asdict(item) for item in facts],
        }

    def _interpret_batch(
        self,
        pairs: Sequence[dict[str, Any]],
        specs: Sequence[RequiredFactSpec],
    ) -> tuple[list[RiskFact], dict[str, Any]]:
        candidates = [item["candidate"] for item in pairs]
        malfunctions = [item["malfunction_candidate"] for item in pairs]
        evidence, registry = self._evidence(candidates, malfunctions)
        fact_requests = []
        expected: set[tuple[str, str, str]] = set()
        spec_by_type = {item.fact_type.value: item for item in specs}
        for pair in pairs:
            context = {
                key: value for key, value in pair.items()
                if key not in {"candidate", "malfunction_candidate"}
            }
            for spec in specs:
                key = (
                    str(pair["malfunction_id"]),
                    str(pair["scenario_id"]),
                    spec.fact_type.value,
                )
                expected.add(key)
                fact_requests.append({
                    **context,
                    "fact_type": spec.fact_type.value,
                    "unit": spec.unit,
                    "allowed_values": self._allowed_values(spec),
                    "constraints": list(spec.constraints),
                })
        request = self._request(fact_requests, evidence, coverage_repair=False)
        response = self.client.complete_json(request)
        facts, seen = self._parse_results(
            response.data, expected, spec_by_type, registry,
        )
        missing = sorted(expected - seen)
        repair_response = None
        if missing:
            missing_set = set(missing)
            repair_requests = [
                item for item in fact_requests
                if (
                    str(item["malfunction_id"]),
                    str(item["scenario_id"]),
                    str(item["fact_type"]),
                ) in missing_set
            ]
            repair_request = self._request(
                repair_requests, evidence, coverage_repair=True,
            )
            repair_response = self.client.complete_json(repair_request)
            repaired, repair_seen = self._parse_results(
                repair_response.data, missing_set, spec_by_type, registry,
            )
            facts.extend(repaired)
            seen.update(repair_seen)
        unresolved = sorted(expected - seen)
        if unresolved:
            raise ValueError(
                f"Scenario risk fact response is incomplete after bounded repair: {unresolved[:5]}"
            )
        return facts, {
            "model": response.model,
            "request_id": response.request_id,
            "usage": {
                "primary": response.usage,
                "repair": repair_response.usage if repair_response else {},
            },
            "coverage_repair_count": int(repair_response is not None),
            "llm_call_count": 1 + int(repair_response is not None),
        }

    def _request(
        self,
        fact_requests: Sequence[dict[str, Any]],
        evidence: Sequence[dict[str, str]],
        *,
        coverage_repair: bool,
    ) -> LLMRequest:
        return LLMRequest(
            task="interpret_scenario_risk_facts",
            schema_name="ScenarioRiskFacts",
            prompt_version=self.PROMPT_VERSION,
            system_prompt=(
                "You are a bounded evidence interpreter. Return only facts explicitly supported by "
                "the supplied evidence excerpts. Do not estimate speed, frequency, duration, or "
                "avoidability. Do not use template identity or invent engineering rules."
            ),
            user_prompt=(
                "Return JSON {\"results\":[...]}. Return exactly one result for every item in "
                "fact_requests. Do not return any unrequested identity. status is FOUND or NOT_FOUND. "
                "FOUND requires value, unit, and one or more evidence_ids. Categorical values must "
                "come from allowed_values; numeric values must be JSON numbers. Evidence IDs must "
                "come from evidence. No Markdown.\n"
                + (
                    "This is the single bounded coverage repair. Return only the explicitly "
                    "listed missing fact_requests.\n" if coverage_repair else ""
                )
                + f"fact_requests={json.dumps(fact_requests, ensure_ascii=False, separators=(',', ':'))}\n"
                f"evidence={json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))}"
            ),
            metadata={
                "result_count": len(fact_requests),
                "coverage_repair": coverage_repair,
            },
            max_tokens=min(
                int(getattr(getattr(self.client, "config", None), "max_tokens", 4096)), 4096
            ),
        )

    def _parse_results(
        self,
        data: dict[str, Any],
        expected: set[tuple[str, str, str]],
        spec_by_type: dict[str, RequiredFactSpec],
        registry: dict[str, SourceRef],
    ) -> tuple[list[RiskFact], set[tuple[str, str, str]]]:
        raw_results = data.get("results")
        if not isinstance(raw_results, list):
            raise ValueError("Scenario risk fact response.results must be an array")
        seen: set[tuple[str, str, str]] = set()
        facts: list[RiskFact] = []
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
                provenance=FactProvenance.DERIVED,
                approval=ReviewStatus.FINALIZED,
                produced_by=self.PROMPT_VERSION,
            ))
        return facts, seen
