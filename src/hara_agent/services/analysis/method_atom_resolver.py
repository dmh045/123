from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hara_agent.contracts import MethodContract


@dataclass(frozen=True)
class MethodAtomResolution:
    status: str
    reason: str
    atom_id: str = ""
    canonical_atom_id: str = ""
    filled_dimensions: tuple[str, ...] = ()
    compatible_range_kph: tuple[float | None, float | None] | None = None
    candidate_atom_ids: tuple[str, ...] = ()
    provenance: dict[str, Any] | None = None


class MethodAtomResolver:
    """Resolve only compiler-governed Scenario atom identities and ranges."""

    def __init__(self, method: MethodContract):
        raw = method.metadata.get("scenario_atom_catalog", [])
        self.catalog = [item for item in raw if isinstance(item, dict)]
        self.by_id = {
            str(item.get("atom_id", "")).strip(): item
            for item in self.catalog if str(item.get("atom_id", "")).strip()
        }
        raw_aliases = method.metadata.get("scenario_aliases", [])
        self.scenario_aliases = [
            item for item in raw_aliases if isinstance(item, dict)
        ]

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"[^\w]+", "", str(value).casefold(), flags=re.UNICODE)

    @staticmethod
    def _range(value: dict[str, Any]) -> tuple[float | None, float | None] | None:
        raw = value.get("speed_range_kph")
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            return None
        return (
            float(raw[0]) if raw[0] is not None else None,
            float(raw[1]) if raw[1] is not None else None,
        )

    @staticmethod
    def _provenance(atom: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_asset": atom.get("source_asset", ""),
            "source_rule": atom.get("source_rule", ""),
            "source_tag": atom.get("source_tag", ""),
        }

    def _result(
        self, atom: dict[str, Any], *, reason: str,
        provenance: dict[str, Any] | None = None,
    ) -> MethodAtomResolution:
        canonical = str(
            atom.get("v2") or atom.get("v2_proper") or atom.get("atom_id") or ""
        ).strip()
        return MethodAtomResolution(
            status="RESOLVED",
            reason=reason,
            atom_id=str(atom.get("atom_id", "")).strip(),
            canonical_atom_id=canonical,
            filled_dimensions=tuple(map(str, atom.get("filled_dimensions", []))),
            candidate_atom_ids=(str(atom.get("atom_id", "")).strip(),),
            provenance={**self._provenance(atom), **(provenance or {})},
        )

    def resolve(self, source_value: str, dimension: str) -> MethodAtomResolution:
        value = str(source_value).strip()
        if not value:
            return MethodAtomResolution("PENDING", "NO_ITEM_FACT")
        exact_id = self.by_id.get(value)
        if exact_id is not None and dimension in exact_id.get("filled_dimensions", []):
            return self._result(
                exact_id,
                reason=(
                    "EXPLICIT_V1_V2_MAPPING"
                    if exact_id.get("v2") or exact_id.get("v2_proper")
                    else "EXACT_ATOM_ID"
                ),
            )
        actual_matches = [
            atom for atom in self.catalog
            if value == str(atom.get("actual_id", "")).strip()
            and dimension in atom.get("filled_dimensions", [])
        ]
        if len(actual_matches) == 1:
            return self._result(actual_matches[0], reason="EXPLICIT_V1_V2_MAPPING")
        normalized = self._normalize(value)
        label_matches = [
            atom for atom in self.catalog
            if dimension in atom.get("filled_dimensions", [])
            and normalized == self._normalize(str(atom.get("label", "")))
        ]
        if len(label_matches) == 1:
            return self._result(label_matches[0], reason="EXACT_CANONICAL_LABEL")
        candidates = tuple(sorted({
            str(atom.get("atom_id", "")) for atom in label_matches
            if str(atom.get("atom_id", ""))
        }))
        if len(label_matches) > 1:
            return MethodAtomResolution("AMBIGUOUS", "AMBIGUOUS_EXACT_MATCH", candidate_atom_ids=candidates)
        alias_matches = [
            alias for alias in self.scenario_aliases
            if str(alias.get("dimension", "")) == dimension
            and normalized == self._normalize(str(alias.get("source_term", "")))
        ]
        if len(alias_matches) == 1:
            alias = alias_matches[0]
            atom = self.by_id.get(str(alias.get("target_atom_id", "")))
            if atom is not None and dimension in atom.get("filled_dimensions", []):
                return self._result(
                    atom,
                    reason="APPROVED_GOVERNED_ALIAS",
                    provenance={
                        "alias_id": alias.get("alias_id", ""),
                        "alias_source_asset": alias.get("source_asset", ""),
                        "alias_source_rule": alias.get("source_rule", ""),
                        "target_atom": atom.get("atom_id", ""),
                        "alias_dimension": dimension,
                    },
                )
            return MethodAtomResolution(
                "PENDING", "APPROVED_ALIAS_CONTRACT_INVALID",
                candidate_atom_ids=(str(alias.get("target_atom_id", "")),),
            )
        if len(alias_matches) > 1:
            return MethodAtomResolution(
                "AMBIGUOUS", "AMBIGUOUS_APPROVED_ALIAS",
                candidate_atom_ids=tuple(sorted({
                    str(alias.get("target_atom_id", "")) for alias in alias_matches
                    if str(alias.get("target_atom_id", ""))
                })),
            )
        alias_matches = [
            atom for atom in self.catalog
            if dimension in atom.get("filled_dimensions", [])
            and normalized in {
                self._normalize(alias) for alias in atom.get("aliases", [])
            }
        ]
        if len(alias_matches) == 1:
            return self._result(alias_matches[0], reason="EXPLICIT_ALIAS")
        if len(alias_matches) > 1:
            return MethodAtomResolution(
                "AMBIGUOUS", "AMBIGUOUS_EXPLICIT_ALIAS",
                candidate_atom_ids=tuple(sorted({
                    str(atom.get("atom_id", "")) for atom in alias_matches
                    if str(atom.get("atom_id", ""))
                })),
            )
        dimension_candidates = tuple(sorted({
            str(atom.get("atom_id", "")) for atom in self.catalog
            if dimension in atom.get("filled_dimensions", [])
        }))
        return MethodAtomResolution(
            "PENDING", "NO_EXPLICIT_METHOD_ALIAS",
            candidate_atom_ids=dimension_candidates,
        )

    def intersect_speed_range(
        self,
        *,
        minimum_kph: float | None,
        maximum_kph: float | None,
        atom_id: str,
    ) -> MethodAtomResolution:
        atom = self.by_id.get(str(atom_id).strip())
        if atom is None:
            return MethodAtomResolution("PENDING", "UNKNOWN_METHOD_ATOM")
        atom_range = self._range(atom)
        if atom_range is None:
            return MethodAtomResolution("PENDING", "ATOM_SPEC_MISSING", atom_id=atom_id)
        lower = max(value for value in (minimum_kph, atom_range[0]) if value is not None) if any(
            value is not None for value in (minimum_kph, atom_range[0])
        ) else None
        upper_values = [value for value in (maximum_kph, atom_range[1]) if value is not None]
        upper = min(upper_values) if upper_values else None
        if lower is not None and upper is not None and lower > upper:
            return MethodAtomResolution(
                "PENDING", "RANGE_INCOMPATIBLE", atom_id=atom_id,
                candidate_atom_ids=(atom_id,), provenance=self._provenance(atom),
            )
        return MethodAtomResolution(
            "RESOLVED", "ATOM_SPEC_RANGE_INTERSECTION", atom_id=atom_id,
            canonical_atom_id=str(atom.get("v2") or atom_id),
            filled_dimensions=tuple(map(str, atom.get("filled_dimensions", []))),
            compatible_range_kph=(lower, upper), candidate_atom_ids=(atom_id,),
            provenance=self._provenance(atom),
        )

    def resolve_speed_range(
        self,
        *,
        minimum_kph: float | None,
        maximum_kph: float | None,
        dimension: str = "EGO_DYNAMICS",
    ) -> MethodAtomResolution:
        """Resolve an interval only when one governed atom contains it wholly.

        This intentionally does not select an endpoint or midpoint.  Partial
        intersections and multiple fully-containing atoms remain pending.
        """
        containing: list[dict[str, Any]] = []
        intersecting: list[dict[str, Any]] = []
        for atom in self.catalog:
            if dimension not in atom.get("filled_dimensions", []):
                continue
            atom_range = self._range(atom)
            if atom_range is None:
                continue
            atom_minimum, atom_maximum = atom_range
            lower_compatible = (
                minimum_kph is None or atom_minimum is None
                or atom_minimum <= minimum_kph
            )
            upper_compatible = (
                maximum_kph is None or atom_maximum is None
                or atom_maximum >= maximum_kph
            )
            if lower_compatible and upper_compatible:
                containing.append(atom)
                continue
            lower = max(
                item for item in (minimum_kph, atom_minimum) if item is not None
            ) if any(item is not None for item in (minimum_kph, atom_minimum)) else None
            upper_values = [
                item for item in (maximum_kph, atom_maximum) if item is not None
            ]
            upper = min(upper_values) if upper_values else None
            if lower is None or upper is None or lower <= upper:
                intersecting.append(atom)
        if len(containing) == 1:
            result = self._result(containing[0], reason="RANGE_CONTAINMENT")
            return MethodAtomResolution(
                **{
                    **result.__dict__,
                    "compatible_range_kph": (minimum_kph, maximum_kph),
                }
            )
        if len(containing) > 1:
            return MethodAtomResolution(
                "AMBIGUOUS", "MULTI_ATOM_RANGE_AMBIGUITY",
                candidate_atom_ids=tuple(sorted(
                    str(atom.get("atom_id", "")) for atom in containing
                    if str(atom.get("atom_id", ""))
                )),
            )
        candidates = tuple(sorted(
            str(atom.get("atom_id", "")) for atom in intersecting
            if str(atom.get("atom_id", ""))
        ))
        return MethodAtomResolution(
            "PENDING",
            "MULTI_ATOM_RANGE_AMBIGUITY" if len(candidates) > 1 else "RANGE_NOT_UNIQUE",
            candidate_atom_ids=candidates,
        )
