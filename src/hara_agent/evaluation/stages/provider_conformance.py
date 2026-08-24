from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any


class ProviderConformanceHarness:
    """Validate Provider-shaped payloads through one injected production parser."""

    def __init__(self, parser: Callable[[Any], Any]):
        self.parser = parser

    def evaluate(self, payloads: Mapping[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        fingerprints: list[str] = []
        for provider_name, payload in payloads.items():
            try:
                parsed = self.parser(payload)
                causal = parsed.causal_assessment
                if causal is None:
                    raise ValueError("parser returned no ScenarioCausalAssessment")
                canonical = json.dumps(
                    causal.to_dict(), ensure_ascii=False,
                    sort_keys=True, separators=(",", ":"),
                )
                fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                fingerprints.append(fingerprint)
                results.append({
                    "provider": provider_name,
                    "schema_conformant": True,
                    "causal_status": causal.status.value,
                    "engineering_review_status": parsed.status.value,
                    "contract_fingerprint": fingerprint,
                })
            except Exception as error:
                code = getattr(getattr(error, "code", None), "value", type(error).__name__)
                errors.append({
                    "provider": provider_name,
                    "code": str(code),
                    "reason": str(error),
                })
        return {
            "stage": "provider_conformance",
            "classification": "EVALUATION_ONLY",
            "provider_count": len(payloads),
            "conformant_count": len(results),
            "rejected_count": len(errors),
            "differential": {
                "comparable_count": len(fingerprints),
                "canonical_contracts_equal": (
                    len(set(fingerprints)) == 1 if fingerprints else None
                ),
            },
            "results": results,
            "errors": errors,
        }
