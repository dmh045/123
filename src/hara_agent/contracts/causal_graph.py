from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class CausalNodeType(str, Enum):
    """Domain-neutral stages in a HARA causal argument."""

    MALFUNCTION = "MALFUNCTION"
    SYSTEM_BEHAVIOR_CHANGE = "SYSTEM_BEHAVIOR_CHANGE"
    OPERATIONAL_CONSEQUENCE = "OPERATIONAL_CONSEQUENCE"
    HAZARD = "HAZARD"
    HARM = "HARM"


class CausalRelation(str, Enum):
    CAUSES = "CAUSES"
    CONTRIBUTES_TO = "CONTRIBUTES_TO"


@dataclass(frozen=True)
class CausalNode:
    node_id: str
    node_type: CausalNodeType
    description: str
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise ValueError("CausalNode.node_id must not be empty")
        if not self.description.strip():
            raise ValueError("CausalNode.description must not be empty")
        if any(not value.strip() for value in self.provenance):
            raise ValueError("CausalNode.provenance values must not be empty")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["node_type"] = self.node_type.value
        result["provenance"] = list(self.provenance)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CausalNode":
        return cls(
            node_id=str(value["node_id"]),
            node_type=CausalNodeType(value["node_type"]),
            description=str(value["description"]),
            provenance=tuple(str(item) for item in value.get("provenance", [])),
        )


@dataclass(frozen=True)
class CausalEdge:
    edge_id: str
    source: str
    target: str
    relation: CausalRelation
    description: str
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.edge_id.strip() or not self.source.strip() or not self.target.strip():
            raise ValueError("CausalEdge requires non-empty edge_id/source/target")
        if self.source == self.target:
            raise ValueError("CausalEdge cannot be a self-loop")
        if not self.description.strip():
            raise ValueError("CausalEdge.description must not be empty")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("CausalEdge.evidence_refs must be unique")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("CausalEdge.evidence_refs values must not be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("CausalEdge.confidence must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["relation"] = self.relation.value
        result["evidence_refs"] = list(self.evidence_refs)
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CausalEdge":
        confidence = value.get("confidence")
        return cls(
            edge_id=str(value["edge_id"]),
            source=str(value["source"]),
            target=str(value["target"]),
            relation=CausalRelation(value["relation"]),
            description=str(value["description"]),
            evidence_refs=tuple(str(item) for item in value.get("evidence_refs", [])),
            confidence=float(confidence) if confidence is not None else None,
        )


@dataclass(frozen=True)
class CausalGraph:
    nodes: tuple[CausalNode, ...]
    edges: tuple[CausalEdge, ...]

    def __post_init__(self) -> None:
        node_ids = [item.node_id for item in self.nodes]
        edge_ids = [item.edge_id for item in self.edges]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("CausalGraph node IDs must be unique")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("CausalGraph edge IDs must be unique")
        known = set(node_ids)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(
                    f"CausalGraph edge endpoint is unresolved: {edge.edge_id}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [item.to_dict() for item in self.nodes],
            "edges": [item.to_dict() for item in self.edges],
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "CausalGraph":
        return cls(
            nodes=tuple(CausalNode.from_dict(item) for item in value.get("nodes", [])),
            edges=tuple(CausalEdge.from_dict(item) for item in value.get("edges", [])),
        )

    def has_path(self, node_ids: tuple[str, ...]) -> bool:
        """Check an explicit ordered path; no domain or business rule is inferred."""

        pairs = {(item.source, item.target) for item in self.edges}
        return all(pair in pairs for pair in zip(node_ids, node_ids[1:]))
