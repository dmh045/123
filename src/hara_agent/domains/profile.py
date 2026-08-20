from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from hara_agent.models import FactProvenance


PROFILE_ROOT = Path(__file__).resolve().parents[3] / "config" / "domains"


@dataclass(frozen=True)
class DomainProfile:
    name: str
    version: str
    approval_status: str
    source: str
    data: dict[str, Any]
    path: Path

    @property
    def is_approved(self) -> bool:
        return self.approval_status.lower() == "approved"

    @property
    def fact_provenance(self) -> FactProvenance:
        """Authority label for facts supplied by this profile."""
        return (
            FactProvenance.DOMAIN_POLICY
            if self.is_approved
            else FactProvenance.LEGACY_MIGRATION
        )

    def section(self, name: str) -> dict[str, Any]:
        value = self.data.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f"Domain Profile section必须为object: {name}")
        return value


def load_domain_profile(name: str = "", path: Optional[str | Path] = None,
                        require_approved: bool = True) -> DomainProfile:
    profile_path = Path(path) if path else PROFILE_ROOT / name / "profile.json"
    if not profile_path.is_file():
        raise FileNotFoundError(f"Domain Profile不存在: {profile_path}")

    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    required = ("name", "version", "approval_status", "source")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError(f"Domain Profile缺少字段: {', '.join(missing)}")
    def contains_forbidden_asil_mapping(value: Any) -> bool:
        if isinstance(value, dict):
            for key, child in value.items():
                if str(key).strip().lower() in {"asil_table", "asil_matrix"}:
                    return True
                if contains_forbidden_asil_mapping(child):
                    return True
        elif isinstance(value, list):
            return any(contains_forbidden_asil_mapping(item) for item in value)
        return False

    if contains_forbidden_asil_mapping(raw):
        raise ValueError("Domain Profile不得包含ASIL矩阵；必须读取输入模板ASIL_Table")
    if "scoring_policy" in raw:
        raise ValueError(
            "Domain Profile旧scoring_policy已禁用；请使用risk_mapping_policy，S/E/C等级定义必须读取输入模板"
        )
    for section in ("scenario_policy", "risk_candidates", "risk_mapping_policy"):
        if not isinstance(raw.get(section), dict) or not raw[section]:
            raise ValueError(f"Domain Profile缺少有效section: {section}")

    profile = DomainProfile(
        name=str(raw["name"]),
        version=str(raw["version"]),
        approval_status=str(raw["approval_status"]),
        source=str(raw["source"]),
        data=raw,
        path=profile_path.resolve(),
    )
    if require_approved and not profile.is_approved:
        raise ValueError(f"Domain Profile未批准: {profile.approval_status}")
    return profile
