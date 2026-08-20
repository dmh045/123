from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_application_has_no_legacy_engineering_authority_imports():
    source = (ROOT / "src" / "hara_agent" / "application.py").read_text(
        encoding="utf-8"
    )

    forbidden = {
        "default_domain_registry",
        "DomainScenarioCandidateService",
        "DomainScoringService",
        "SafetyGoalCatalogService",
        "TemplateInputReader",
        "TemplateASILService",
    }
    assert all(name not in source for name in forbidden)
