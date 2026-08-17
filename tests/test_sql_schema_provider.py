from __future__ import annotations

from repodelta.changes.hunks import DiffHunkCollection, parse_unified_patch
from repodelta.providers.evidence import EvidenceProvider
from repodelta.providers.sql_schema import SqlSchemaProvider


def _changes(path: str, patch: str) -> DiffHunkCollection:
    return DiffHunkCollection(hunks=parse_unified_patch(path, patch))


def test_provider_satisfies_the_evidence_port() -> None:
    assert isinstance(SqlSchemaProvider(), EvidenceProvider)


def test_added_migration_ddl_becomes_schema_facts() -> None:
    patch = (
        "@@ -0,0 +1,9 @@\n"
        "+ALTER TABLE orders ADD COLUMN external_id text;\n"
        "+CREATE TABLE order_events (\n"
        "+    id bigint PRIMARY KEY,\n"
        "+    order_id bigint NOT NULL,\n"
        "+    payload jsonb,\n"
        "+    PRIMARY KEY (id)\n"
        "+);\n"
        "+CREATE UNIQUE INDEX idx_orders_external_id ON orders (external_id);\n"
        "+DROP TABLE legacy_orders;\n"
    )
    contribution = SqlSchemaProvider().contribute(
        _changes("migrations/042.sql", patch),
        matched_files=("migrations/042.sql",),
    )
    contribution.validate_consistency()
    facts = {
        (item.kind, item.subject, item.operation) for item in contribution.facts
    }
    assert ("column", "orders.external_id", "added") in facts
    assert ("table", "order_events", "added") in facts
    assert ("column", "order_events.id", "added") in facts
    assert ("column", "order_events.order_id", "added") in facts
    assert ("column", "order_events.payload", "added") in facts
    assert ("index", "idx_orders_external_id", "added") in facts
    assert ("table", "legacy_orders", "removed") in facts
    assert contribution.coverage.state == "complete"
    assert contribution.coverage.examined_files == ("migrations/042.sql",)
    column = next(
        item
        for item in contribution.facts
        if item.subject == "orders.external_id"
    )
    assert column.sources[0].path == "migrations/042.sql"
    assert column.sources[0].line_start == 1


def test_uninterpreted_statements_become_coverage_limits_not_silence() -> None:
    patch = (
        "@@ -0,0 +1,3 @@\n"
        "+ALTER TABLE orders ADD COLUMN note text;\n"
        "+INSERT INTO schema_meta VALUES (42);\n"
        "+UPDATE orders SET note = '';\n"
    )
    contribution = SqlSchemaProvider().contribute(
        _changes("migrations/043.sql", patch),
        matched_files=("migrations/043.sql",),
    )
    assert contribution.coverage.state == "partial"
    assert "unparsed_statements" in contribution.coverage.limits
    assert {
        (item.kind, item.subject, item.operation)
        for item in contribution.facts
    } == {("column", "orders.note", "added")}


def test_removed_migration_lines_are_reported_not_interpreted() -> None:
    patch = (
        "@@ -1,2 +1,1 @@\n"
        "-ALTER TABLE orders DROP COLUMN legacy_flag;\n"
        " CREATE TABLE noop_marker (id int);\n"
    )
    contribution = SqlSchemaProvider().contribute(
        _changes("migrations/040.sql", patch),
        matched_files=("migrations/040.sql",),
    )
    assert contribution.coverage.state == "partial"
    assert "removed_lines_not_interpreted" in contribution.coverage.limits
    assert contribution.facts == ()


def test_alter_clauses_cover_drop_rename_and_constraints() -> None:
    patch = (
        "@@ -0,0 +1,3 @@\n"
        "+ALTER TABLE orders DROP COLUMN legacy_flag;\n"
        "+ALTER TABLE orders RENAME COLUMN buyer TO customer;\n"
        "+ALTER TABLE orders ADD CONSTRAINT fk_customer "
        "FOREIGN KEY (customer_id) REFERENCES customers (id);\n"
    )
    contribution = SqlSchemaProvider().contribute(
        _changes("migrations/044.sql", patch),
        matched_files=("migrations/044.sql",),
    )
    facts = {
        (item.kind, item.subject, item.operation) for item in contribution.facts
    }
    assert ("column", "orders.legacy_flag", "removed") in facts
    assert ("column", "orders.customer", "renamed") in facts
    assert ("constraint", "orders.fk_customer", "added") in facts
    constraint = next(
        item for item in contribution.facts if item.kind == "constraint"
    )
    assert ("constraint_kind", "foreign_key") in constraint.attributes


def test_no_matched_files_is_not_applicable() -> None:
    contribution = SqlSchemaProvider().contribute(
        DiffHunkCollection(),
        matched_files=(),
    )
    assert contribution.coverage.state == "not_applicable"
    assert contribution.facts == ()


def test_duplicate_ddl_merges_sources_instead_of_duplicating_facts() -> None:
    patch = (
        "@@ -0,0 +1,2 @@\n"
        "+ALTER TABLE orders ADD COLUMN note text;\n"
        "+ALTER TABLE orders ADD COLUMN note text;\n"
    )
    contribution = SqlSchemaProvider().contribute(
        _changes("migrations/045.sql", patch),
        matched_files=("migrations/045.sql",),
    )
    contribution.validate_consistency()
    assert len(contribution.facts) == 1
    assert len(contribution.facts[0].sources) == 2
