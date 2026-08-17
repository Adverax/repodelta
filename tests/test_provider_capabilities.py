from __future__ import annotations

import pytest

from repodelta.providers.capabilities import (
    FileSelector,
    LanguageCapabilities,
    ProviderCapability,
    ProviderDescriptor,
)
from repodelta.providers.codegraph import codegraph_descriptor
from repodelta.providers.planning import ProviderPlan, plan_providers
from repodelta.providers.sql_schema import sql_schema_descriptor


def _descriptor(provider: str, *selectors: FileSelector) -> ProviderDescriptor:
    return ProviderDescriptor(
        provider=provider,
        capabilities=(ProviderCapability(name="facts", level="partial"),),
        selectors=selectors,
    )


def test_selectors_match_deterministically() -> None:
    assert FileSelector(kind="suffix", pattern=".go").matches("cmd/api/main.go")
    assert not FileSelector(kind="suffix", pattern=".go").matches("a/b.gone")
    assert FileSelector(kind="exact", pattern="conf/routes").matches("conf/routes")
    assert not FileSelector(kind="exact", pattern="conf/routes").matches(
        "src/conf/routes/extra"
    )
    assert FileSelector(kind="basename_glob", pattern="application*.yml").matches(
        "config/application-prod.yml"
    )
    assert FileSelector(kind="path_glob", pattern="*/migrations/*.sql").matches(
        "internal/db/migrations/042.sql"
    )
    assert not FileSelector(
        kind="path_glob", pattern="*/migrations/*.sql"
    ).matches("migrations.sql")


def test_descriptor_reports_capability_levels_without_scores() -> None:
    descriptor = ProviderDescriptor(
        provider="openapi",
        capabilities=(
            ProviderCapability(name="api_operations", level="full"),
            ProviderCapability(name="breaking_change_detection", level="partial"),
        ),
        languages=(
            LanguageCapabilities(
                language="go",
                capabilities=(
                    ProviderCapability(name="symbols", level="full"),
                    ProviderCapability(name="data_flow", level="unavailable"),
                ),
            ),
        ),
    )
    descriptor.validate_consistency()
    assert descriptor.capability_level("api_operations") == "full"
    assert descriptor.capability_level("breaking_change_detection") == "partial"
    assert descriptor.capability_level("sql_effects") == "unavailable"


def test_descriptor_rejects_duplicate_capabilities() -> None:
    descriptor = ProviderDescriptor(
        provider="x",
        capabilities=(
            ProviderCapability(name="a"),
            ProviderCapability(name="a", level="partial"),
        ),
    )
    with pytest.raises(ValueError, match="duplicate capabilities"):
        descriptor.validate_consistency()


def test_plan_routes_changed_files_and_keeps_unclaimed_explicit() -> None:
    plan = plan_providers(
        (
            "cmd/api/main.go",
            "internal/orders/service.go",
            "migrations/042.sql",
            "deploy/main.tf",
            "README.md",
        ),
        (codegraph_descriptor(), sql_schema_descriptor()),
    )
    codegraph = plan.entry_for("codegraph")
    sql = plan.entry_for("sql_schema")
    assert codegraph is not None
    assert codegraph.matched_files == (
        "cmd/api/main.go",
        "deploy/main.tf",
        "internal/orders/service.go",
    )
    assert sql is not None
    assert sql.matched_files == ("migrations/042.sql",)
    assert plan.unclaimed_files == ("README.md",)
    plan.validate_consistency()


def test_plan_is_deterministic_for_shuffled_input() -> None:
    paths = ("b/migrations/2.sql", "a.go", "z.md", "a.go", "")
    first = plan_providers(paths, (codegraph_descriptor(), sql_schema_descriptor()))
    second = plan_providers(
        tuple(reversed(paths)),
        (sql_schema_descriptor(), codegraph_descriptor()),
    )
    assert first == second
    assert first.requested_files == ("a.go", "b/migrations/2.sql", "z.md")


def test_plan_rejects_duplicate_provider_names() -> None:
    duplicate = _descriptor("dup", FileSelector(kind="suffix", pattern=".x"))
    with pytest.raises(ValueError, match="unique provider names"):
        plan_providers(("a.x",), (duplicate, duplicate))


def test_plan_validation_rejects_wrong_unclaimed_accounting() -> None:
    plan = ProviderPlan(
        requested_files=("a.sql", "b.md"),
        entries=(),
        unclaimed_files=("a.sql",),
    )
    with pytest.raises(ValueError, match="unclaimed files"):
        plan.validate_consistency()


def test_codegraph_declares_blind_spots_honestly() -> None:
    descriptor = codegraph_descriptor()
    descriptor.validate_consistency()
    assert descriptor.capability_level("calls") == "full"
    assert descriptor.capability_level("data_flow") == "unavailable"
    assert descriptor.capability_level("sql_effects") == "unavailable"
    assert descriptor.matches("src/service.py")
    assert descriptor.matches("conf/routes")
    assert not descriptor.matches("migrations/042.sql")
