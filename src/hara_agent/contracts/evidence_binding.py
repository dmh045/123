from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from hara_agent.models.common import ReviewStatus, SourceRef
from hara_agent.models.evidence import EvidenceKind


@dataclass(frozen=True)
class EvidenceBinding:
    """Evidence attached to one causal edge, not to an entire Scenario."""

    edge_id: str
    evidence_refs: tuple[str, ...]
    basis_type: EvidenceKind
    source_refs: tuple[SourceRef, ...] = ()
    status: ReviewStatus = ReviewStatus.PENDING

    def __post_init__(self) -> None:
        if not self.edge_id.strip():
            raise ValueError("EvidenceBinding.edge_id must not be empty")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("EvidenceBinding.evidence_refs must be unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("EvidenceBinding.evidence_refs values must not be empty")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_refs"] = list(self.evidence_refs)
        result["basis_type"] = self.basis_type.value
        result["source_refs"] = [asdict(item) for item in self.source_refs]
        result["status"] = self.status.value
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvidenceBinding":
        return cls(
            edge_id=str(value["edge_id"]),
            evidence_refs=tuple(str(item) for item in value.get("evidence_refs", [])),
            basis_type=EvidenceKind(value["basis_type"]),
            source_refs=tuple(SourceRef(**item) for item in value.get("source_refs", [])),
            status=ReviewStatus(value.get("status", ReviewStatus.PENDING.value)),
        )
