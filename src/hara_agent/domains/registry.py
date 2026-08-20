from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .avp import AVPDomainPolicy
from .profile import DomainProfile, load_domain_profile
from .protocol import DomainPolicy


PolicyFactory = Callable[[DomainProfile], DomainPolicy]


@dataclass(frozen=True)
class DomainRuntime:
    name: str
    profile: DomainProfile
    policy: DomainPolicy


class DomainRegistry:
    def __init__(self):
        self._factories: dict[str, PolicyFactory] = {}

    def register(self, name: str, factory: PolicyFactory) -> "DomainRegistry":
        key = name.strip().lower()
        if not key:
            raise ValueError("Domain注册名为空")
        if key in self._factories:
            raise ValueError(f"Domain重复注册: {key}")
        self._factories[key] = factory
        return self

    def create(self, name: str, profile_path: Optional[str | Path] = None,
               require_approved: bool = False) -> DomainRuntime:
        key = name.strip().lower()
        factory = self._factories.get(key)
        if factory is None:
            raise ValueError(f"未注册Domain: {name}; available={sorted(self._factories)}")
        profile = load_domain_profile(
            key, path=profile_path, require_approved=require_approved
        )
        return DomainRuntime(key, profile, factory(profile))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


def default_domain_registry() -> DomainRegistry:
    return DomainRegistry().register("avp", AVPDomainPolicy)
