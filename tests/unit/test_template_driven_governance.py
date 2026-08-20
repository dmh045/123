from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_governance_declares_template_method_and_project_fact_authority():
    agents = _read("AGENTS.md")
    router = _read("SKILL.md")
    migration = _read("docs/architecture/TEMPLATE_DRIVEN_GOVERNANCE_MIGRATION.md")

    for text in (agents, router, migration):
        assert "MethodContract" in text
        assert "ProjectFacts" in text
        assert "PENDING" in text or "fail closed" in text
    assert "do not add" in agents.casefold()
    assert "new business rules" in agents.casefold()
    assert "third customer-maintained business input" in " ".join(router.split())
    assert "not" in router


def test_stage_skills_do_not_make_domain_profile_future_authority():
    skill_paths = (
        "skills/hara-orchestrate/SKILL.md",
        "skills/review-hara/SKILL.md",
        "skills/extract-item-definition/SKILL.md",
        "skills/analyze-hara/SKILL.md",
        "skills/render-hara-report/SKILL.md",
        "skills/hara-orchestrate/agents/openai.yaml",
        "skills/review-hara/agents/openai.yaml",
        "skills/analyze-hara/agents/openai.yaml",
        "skills/render-hara-report/agents/openai.yaml",
    )
    combined = "\n".join(_read(path) for path in skill_paths)

    assert "compiled" in combined.casefold()
    assert "migration-only" in combined.casefold()
    assert "Domain Profiles for approved domain/project knowledge" not in combined


def test_new_template_foundation_has_no_domain_rule_dependency():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "hara_agent" / "template").glob("*.py")
    )

    assert "hara_agent.domains" not in source
    assert "SG_AVP_" not in source
    assert "AVPDomainPolicy" not in source
