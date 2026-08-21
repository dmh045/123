from __future__ import annotations

from hara_agent.contracts import MethodContract
from hara_agent.models import SourceRef


class MethodContractASILService:
    """Query the exact ASIL matrix compiled from the active template."""

    def __init__(self, method: MethodContract):
        if not method.engineering_rules_compiled:
            raise ValueError("MethodContract engineering rules are not compiled")
        self.method = method
        self.matrix = {
            (item.severity, item.exposure, item.controllability): item
            for item in method.asil.mappings
        }
        binding = method.asil.source_binding
        self.source = (
            f"method-contract:{method.metadata['template_hash']}#"
            f"{binding.sheet}!{binding.region}"
        )

    def _mapping(self, severity: str, exposure: str, controllability: str):
        key = tuple(
            str(value or "").strip().upper()
            for value in (severity, exposure, controllability)
        )
        if key not in self.matrix:
            raise ValueError(
                "Invalid S/E/C combination: "
                f"severity={severity!r}, exposure={exposure!r}, "
                f"controllability={controllability!r}"
            )
        return self.matrix[key]

    def determine(self, severity: str, exposure: str, controllability: str) -> str:
        return self._mapping(severity, exposure, controllability).result

    def evidence_source(
        self, severity: str, exposure: str, controllability: str
    ) -> SourceRef:
        source = self._mapping(severity, exposure, controllability).source_ref
        return SourceRef(
            "method_contract",
            source.workbook,
            f"{source.sheet}!{source.range}",
            f"template_hash={source.template_hash}; source_hash={source.source_hash}",
        )
