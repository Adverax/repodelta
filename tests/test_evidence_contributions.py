from __future__ import annotations

import pytest

from repodelta.model.contracts import (
    AnalysisInput,
    ChangedFile,
    ReviewSourcePacket,
    SourceRef,
)
from repodelta.changes.hunks import parse_changed_files
from repodelta.facts.catalog import build_evidence_catalog, contributed_evidence
from repodelta.pipeline import DeterministicAnalyzer
from repodelta.presentation.status import (
    format_provider_coverage,
    format_unclaimed_files,
)
from repodelta.providers.capabilities import ProviderCapability
from repodelta.providers.evidence import (
    EvidenceContribution,
    EvidenceContributionCoverage,
    ProviderFact,
)
from repodelta.providers.planning import plan_providers
from repodelta.providers.sql_schema import sql_schema_descriptor


_MIGRATION_PATCH = (
    "@@ -0,0 +1,1 @@\n"
    "+ALTER TABLE orders ADD COLUMN external_id text;\n"
)


def _packet() -> ReviewSourcePacket:
    return ReviewSourcePacket(
        repository="acme/widget",
        pull_request=42,
        title="Add external order identity",
        source_records=(),
        changed_files=(
            ChangedFile(
                base_path=None,
                head_path="migrations/042.sql",
                status="added",
                patch=_MIGRATION_PATCH,
            ),
        ),
    ).with_revision()


def _fact(
    subject: str = "orders.external_id",
    operation: str = "added",
) -> ProviderFact:
    verb = {"added": "Added", "removed": "Removed"}.get(operation, "Observed")
    return ProviderFact(
        kind="column",
        subject=subject,
        summary=f"{verb} column: {subject}",
        operation=operation,
        sources=(
            SourceRef(
                label="migration DDL",
                path="migrations/042.sql",
                line_start=1,
                line_end=1,
            ),
        ),
    )


def _contribution(
    provider: str,
    *facts: ProviderFact,
) -> EvidenceContribution:
    return EvidenceContribution(
        provider=provider,
        facts=facts,
        coverage=EvidenceContributionCoverage(
            state="complete",
            requested_files=("migrations/042.sql",),
            examined_files=("migrations/042.sql",),
        ),
        capabilities=(ProviderCapability(name="columns", level="partial"),),
    )


def test_contributed_facts_carry_provider_identity() -> None:
    packet = _packet()
    catalog = build_evidence_catalog(
        packet,
        parse_changed_files(packet.changed_files),
        contributions=(_contribution("sql_schema", _fact()),),
    )
    item = next(
        item for item in catalog.items if item.authority == "evidence_provider"
    )
    assert item.provider == "sql_schema"
    assert item.kind == "column"
    assert item.operation == "added"
    assert item.revision_side == "head"
    assert item.changed is True
    assert item.role == "changed_anchor"
    assert item.metadata["subject"] == "orders.external_id"
    assert item.id.startswith("E:column:")


def test_agreeing_providers_corroborate_one_fact() -> None:
    packet = _packet()
    catalog = build_evidence_catalog(
        packet,
        parse_changed_files(packet.changed_files),
        contributions=(
            _contribution("sql_schema", _fact()),
            _contribution("postgres_catalog", _fact()),
        ),
    )
    columns = tuple(item for item in catalog.items if item.kind == "column")
    assert len(columns) == 1
    assert columns[0].metadata["corroborating_providers"] == (
        "postgres_catalog",
        "sql_schema",
    )
    assert not any(
        diagnostic.code == "evidence_provider_conflict"
        for diagnostic in catalog.diagnostics
    )


def test_contradicting_providers_yield_conflict_not_merge() -> None:
    packet = _packet()
    catalog = build_evidence_catalog(
        packet,
        parse_changed_files(packet.changed_files),
        contributions=(
            _contribution("sql_schema", _fact(operation="added")),
            _contribution("postgres_catalog", _fact(operation="removed")),
        ),
    )
    columns = tuple(item for item in catalog.items if item.kind == "column")
    assert len(columns) == 2
    assert {item.operation for item in columns} == {"added", "removed"}
    for item in columns:
        other = next(value for value in columns if value.id != item.id)
        assert item.metadata["conflicting_evidence_ids"] == (other.id,)
    conflict = next(
        diagnostic
        for diagnostic in catalog.diagnostics
        if diagnostic.code == "evidence_provider_conflict"
    )
    assert "postgres_catalog" in conflict.message
    assert "sql_schema" in conflict.message
    assert conflict.severity == "warning"


def test_same_provider_contradiction_is_not_a_federation_conflict() -> None:
    packet = _packet()
    catalog = build_evidence_catalog(
        packet,
        parse_changed_files(packet.changed_files),
        contributions=(
            _contribution(
                "sql_schema",
                _fact(operation="added"),
                _fact(operation="removed"),
            ),
        ),
    )
    assert not any(
        diagnostic.code == "evidence_provider_conflict"
        for diagnostic in catalog.diagnostics
    )


def test_contributed_evidence_requires_provider_identity() -> None:
    with pytest.raises(ValueError, match="provider identity"):
        contributed_evidence("", _fact()).validate_consistency()


def test_overview_reports_per_provider_coverage_and_unclaimed_files() -> None:
    packet = _packet()
    plan = plan_providers(
        ("migrations/042.sql", "docs/notes.md"),
        (sql_schema_descriptor(),),
    )
    brief = DeterministicAnalyzer().analyze(
        AnalysisInput(
            packet=packet,
            provider_plan=plan,
            provider_contributions=(_contribution("sql_schema", _fact()),),
        )
    )
    assert brief.overview.unclaimed_changed_files == ("docs/notes.md",)
    row = next(
        item
        for item in brief.overview.provider_coverage
        if item.provider == "sql_schema"
    )
    assert row.state == "complete"
    assert row.requested_file_count == 1
    assert row.examined_file_count == 1
    assert row.fact_count == 1
    assert row.capabilities == (
        ProviderCapability(name="columns", level="partial"),
    )


def test_planned_but_unexecuted_provider_is_reported_not_requested() -> None:
    packet = _packet()
    plan = plan_providers(
        ("migrations/042.sql",),
        (sql_schema_descriptor(),),
    )
    brief = DeterministicAnalyzer().analyze(
        AnalysisInput(packet=packet, provider_plan=plan)
    )
    row = next(
        item
        for item in brief.overview.provider_coverage
        if item.provider == "sql_schema"
    )
    assert row.state == "not_requested"
    assert row.requested_file_count == 1


def test_status_lines_render_coverage_without_conclusions() -> None:
    packet = _packet()
    plan = plan_providers(
        ("migrations/042.sql", "docs/notes.md"),
        (sql_schema_descriptor(),),
    )
    brief = DeterministicAnalyzer().analyze(
        AnalysisInput(
            packet=packet,
            provider_plan=plan,
            provider_contributions=(_contribution("sql_schema", _fact()),),
        )
    )
    row = brief.overview.provider_coverage[0]
    line = format_provider_coverage(row)
    assert line == (
        "Evidence provider sql_schema: complete · 1/1 matched files "
        "examined · 1 facts · declares columns partial"
    )
    assert format_unclaimed_files(brief.overview.unclaimed_changed_files) == (
        "Evidence coverage gap: 1 changed file matched no declared "
        "evidence provider"
    )
