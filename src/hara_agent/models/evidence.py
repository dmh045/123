from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .common import FactProvenance, ReviewStatus, SourceRef


class EvidenceKind(str, Enum):
    DIRECT_FACT = "DIRECT_FACT"
    DERIVED_PHYSICS = "DERIVED_PHYSICS"
    APPROVED_RULE = "APPROVED_RULE"
    ASSUMPTION = "ASSUMPTION"


EVIDENCE_NAMESPACES = ("MF", "SCN", "PROJECT", "METHOD", "DOMAIN_RULE", "DERIVED")
_EVIDENCE_REF = re.compile(
    r"^(?:" + "|".join(EVIDENCE_NAMESPACES) + r")\.[A-Za-z0-9_.-]+$"
)


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_ref: str
    value: Any
    kind: EvidenceKind
    provenance: FactProvenance
    approval_status: ReviewStatus
    source_refs: tuple[SourceRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _EVIDENCE_REF.fullmatch(self.evidence_ref):
            raise ValueError(f"invalid evidence_ref namespace or shape: {self.evidence_ref!r}")
        if not isinstance(self.metadata, dict):
            raise ValueError("EvidenceRecord.metadata must be an object")

    @property
    def namespace(self) -> str:
        return self.evidence_ref.split(".", 1)[0]

    def to_dict(self, *, include_value: bool = True) -> dict[str, Any]:
        result = {
            "evidence_ref": self.evidence_ref,
            "kind": self.kind.value,
            "provenance": self.provenance.value,
            "approval_status": self.approval_status.value,
            "source_refs": [asdict(item) for item in self.source_refs],
            "metadata": dict(self.metadata),
        }
        if include_value:
            result["value"] = self.value
        return result
