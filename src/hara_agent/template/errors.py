from __future__ import annotations

from hara_agent.contracts import TemplateRole


class TemplateRoleDiscoveryError(ValueError):
    pass


class TemplateRoleMissingError(TemplateRoleDiscoveryError):
    def __init__(self, roles: list[TemplateRole]):
        self.roles = tuple(roles)
        super().__init__("Template roles missing: " + ", ".join(role.value for role in roles))


class TemplateRoleAmbiguityError(TemplateRoleDiscoveryError):
    def __init__(self, role: TemplateRole, candidates: list[tuple[str, str, float]]):
        self.role = role
        self.candidates = tuple(candidates)
        rendered = ", ".join(
            f"{sheet}!{region}@{confidence:.3f}"
            for sheet, region, confidence in candidates
        )
        super().__init__(f"Ambiguous template role {role.value}: {rendered}")
