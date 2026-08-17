from __future__ import annotations

import re
from dataclasses import dataclass

from repodelta.model.contracts import SourceRef
from repodelta.changes.hunks import ChangedLine, DiffHunkCollection
from repodelta.providers.capabilities import (
    FileSelector,
    ProviderCapability,
    ProviderDescriptor,
)
from repodelta.providers.evidence import (
    EvidenceContribution,
    EvidenceContributionCoverage,
    ProviderFact,
)

_PROVIDER = "sql_schema"

_CREATE_TABLE = re.compile(
    r"^create\s+table\s+(?:if\s+not\s+exists\s+)?(?P<table>[\w.\"`\[\]]+)",
    re.IGNORECASE,
)
_DROP_TABLE = re.compile(
    r"^drop\s+table\s+(?:if\s+exists\s+)?(?P<tables>[\w.\"`\[\],\s]+?)"
    r"(?:\s+cascade|\s+restrict)?$",
    re.IGNORECASE,
)
_ALTER_TABLE = re.compile(
    r"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?(?P<table>[\w.\"`\[\]]+)"
    r"\s+(?P<body>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_ADD_COLUMN = re.compile(
    r"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?(?P<column>[\w\"`\[\]]+)",
    re.IGNORECASE,
)
_DROP_COLUMN = re.compile(
    r"drop\s+(?:column\s+)?(?:if\s+exists\s+)?(?P<column>[\w\"`\[\]]+)",
    re.IGNORECASE,
)
_ADD_CONSTRAINT = re.compile(
    r"add\s+constraint\s+(?P<name>[\w\"`\[\]]+)",
    re.IGNORECASE,
)
_RENAME_COLUMN = re.compile(
    r"rename\s+(?:column\s+)?(?P<old>[\w\"`\[\]]+)\s+to\s+(?P<new>[\w\"`\[\]]+)",
    re.IGNORECASE,
)
_CREATE_INDEX = re.compile(
    r"^create\s+(?:unique\s+)?index\s+(?:concurrently\s+)?"
    r"(?:if\s+not\s+exists\s+)?(?P<index>[\w.\"`\[\]]+)\s+on\s+"
    r"(?:only\s+)?(?P<table>[\w.\"`\[\]]+)",
    re.IGNORECASE,
)
_DROP_INDEX = re.compile(
    r"^drop\s+index\s+(?:concurrently\s+)?(?:if\s+exists\s+)?"
    r"(?P<index>[\w.\"`\[\]]+)",
    re.IGNORECASE,
)
_NON_COLUMN_LEADS = frozenset(
    {
        "primary",
        "foreign",
        "unique",
        "constraint",
        "check",
        "exclude",
        "like",
        "index",
        "key",
    }
)


def sql_schema_descriptor() -> ProviderDescriptor:
    """Declare what migration-diff parsing can honestly assert."""

    return ProviderDescriptor(
        provider=_PROVIDER,
        capabilities=(
            ProviderCapability(name="tables", level="partial"),
            ProviderCapability(name="columns", level="partial"),
            ProviderCapability(name="indexes", level="partial"),
            ProviderCapability(name="constraints", level="partial"),
            ProviderCapability(name="migration_effects", level="partial"),
            ProviderCapability(name="data_effects", level="unavailable"),
            ProviderCapability(name="runtime_schema", level="unavailable"),
        ),
        selectors=(
            FileSelector(kind="path_glob", pattern="migrations/*.sql"),
            FileSelector(kind="path_glob", pattern="*/migrations/*.sql"),
            FileSelector(kind="path_glob", pattern="*/migrate/*.sql"),
        ),
    )


@dataclass(frozen=True)
class _Statement:
    text: str
    path: str
    line_start: int
    line_end: int


class SqlSchemaProvider:
    """Read schema effects from added migration DDL in the reviewed diff.

    The provider interprets only the added lines of matched migration files;
    everything it cannot interpret is reported as coverage limits, never
    silently dropped.
    """

    def descriptor(self) -> ProviderDescriptor:
        return sql_schema_descriptor()

    def contribute(
        self,
        changes: DiffHunkCollection,
        *,
        matched_files: tuple[str, ...],
    ) -> EvidenceContribution:
        requested = tuple(sorted(set(matched_files)))
        descriptor = self.descriptor()
        if not requested:
            return EvidenceContribution(
                provider=_PROVIDER,
                coverage=EvidenceContributionCoverage(state="not_applicable"),
                capabilities=descriptor.capabilities,
            )
        added_by_file: dict[str, list[ChangedLine]] = {}
        removed_line_files: set[str] = set()
        for hunk in changes.hunks:
            head_path = hunk.head_path or ""
            base_path = hunk.base_path or ""
            for relation in hunk.relations:
                if head_path in requested and relation.added:
                    added_by_file.setdefault(head_path, []).extend(
                        relation.added
                    )
                if base_path in requested and relation.removed:
                    removed_line_files.add(base_path)
        examined = tuple(sorted(added_by_file))
        limits: list[str] = []
        facts: dict[tuple[str, str, str], ProviderFact] = {}
        unparsed = 0
        for path in examined:
            lines = sorted(added_by_file[path], key=lambda item: item.number)
            for statement in _split_statements(path, lines):
                statement_facts = _interpret_statement(statement)
                if statement_facts is None:
                    unparsed += 1
                    continue
                for fact in statement_facts:
                    _merge_fact(facts, fact)
        if unparsed:
            limits.append("unparsed_statements")
        if removed_line_files & set(requested):
            limits.append("removed_lines_not_interpreted")
        missing_patch = tuple(
            path for path in requested if path not in set(examined)
        )
        if missing_patch and not removed_line_files & set(missing_patch):
            limits.append("files_without_added_lines")
        state = "partial" if limits else "complete"
        contribution = EvidenceContribution(
            provider=_PROVIDER,
            facts=tuple(
                sorted(
                    facts.values(),
                    key=lambda item: (item.kind, item.subject, item.operation),
                )
            ),
            coverage=EvidenceContributionCoverage(
                state=state,
                requested_files=requested,
                examined_files=examined,
                limits=tuple(dict.fromkeys(limits)),
            ),
            capabilities=descriptor.capabilities,
        )
        contribution.validate_consistency()
        return contribution


def _merge_fact(
    facts: dict[tuple[str, str, str], ProviderFact],
    fact: ProviderFact,
) -> None:
    key = (fact.kind, fact.subject, fact.operation)
    existing = facts.get(key)
    if existing is None:
        facts[key] = fact
        return
    merged_sources = existing.sources + tuple(
        source for source in fact.sources if source not in existing.sources
    )
    facts[key] = ProviderFact(
        kind=existing.kind,
        subject=existing.subject,
        summary=existing.summary,
        operation=existing.operation,
        classification=existing.classification,
        profile=existing.profile,
        sources=merged_sources,
        attributes=existing.attributes,
    )


def _split_statements(
    path: str, lines: list[ChangedLine]
) -> tuple[_Statement, ...]:
    statements: list[_Statement] = []
    buffer: list[str] = []
    line_start = 0
    line_end = 0

    def close() -> None:
        nonlocal buffer, line_start
        text = " ".join(part for part in buffer if part).strip()
        buffer = []
        if text:
            statements.append(
                _Statement(
                    text=text,
                    path=path,
                    line_start=line_start,
                    line_end=line_end,
                )
            )
        line_start = 0

    for line in lines:
        text = line.text.split("--", 1)[0]
        if not text.strip():
            continue
        if not buffer:
            line_start = line.number
        line_end = line.number
        while ";" in text:
            fragment, text = text.split(";", 1)
            buffer.append(fragment)
            close()
            if text.strip() and not buffer:
                line_start = line.number
        if text.strip():
            buffer.append(text)
    close()
    return tuple(statements)


def _interpret_statement(
    statement: _Statement,
) -> tuple[ProviderFact, ...] | None:
    text = " ".join(statement.text.split())
    source = SourceRef(
        label=f"migration DDL · {statement.path}",
        path=statement.path,
        line_start=statement.line_start,
        line_end=statement.line_end,
    )
    match = _CREATE_TABLE.match(text)
    if match:
        table = _identifier(match.group("table"))
        facts = [
            ProviderFact(
                kind="table",
                subject=table,
                summary=f"Added table: {table}",
                operation="added",
                sources=(source,),
            )
        ]
        facts.extend(
            ProviderFact(
                kind="column",
                subject=f"{table}.{column}",
                summary=f"Added column: {table}.{column}",
                operation="added",
                sources=(source,),
            )
            for column in _created_columns(text)
        )
        return tuple(facts)
    match = _DROP_TABLE.match(text)
    if match:
        return tuple(
            ProviderFact(
                kind="table",
                subject=_identifier(name),
                summary=f"Removed table: {_identifier(name)}",
                operation="removed",
                sources=(source,),
            )
            for name in match.group("tables").split(",")
            if name.strip()
        )
    match = _CREATE_INDEX.match(text)
    if match:
        index = _identifier(match.group("index"))
        table = _identifier(match.group("table"))
        return (
            ProviderFact(
                kind="index",
                subject=index,
                summary=f"Added index: {index} on {table}",
                operation="added",
                sources=(source,),
                attributes=(("table", table),),
            ),
        )
    match = _DROP_INDEX.match(text)
    if match:
        index = _identifier(match.group("index"))
        return (
            ProviderFact(
                kind="index",
                subject=index,
                summary=f"Removed index: {index}",
                operation="removed",
                sources=(source,),
            ),
        )
    match = _ALTER_TABLE.match(text)
    if match:
        return _interpret_alter(
            _identifier(match.group("table")),
            match.group("body"),
            source,
        )
    return None


def _interpret_alter(
    table: str, body: str, source: SourceRef
) -> tuple[ProviderFact, ...] | None:
    facts: list[ProviderFact] = []
    for clause in _split_clauses(body):
        lead = clause.split(None, 1)[0].casefold() if clause.split() else ""
        if lead == "add":
            constraint = _ADD_CONSTRAINT.match(clause)
            if constraint:
                name = _identifier(constraint.group("name"))
                kind_label = (
                    "foreign_key"
                    if re.search(r"foreign\s+key", clause, re.IGNORECASE)
                    else "constraint"
                )
                facts.append(
                    ProviderFact(
                        kind="constraint",
                        subject=f"{table}.{name}",
                        summary=f"Added constraint: {name} on {table}",
                        operation="added",
                        sources=(source,),
                        attributes=(("constraint_kind", kind_label),),
                    )
                )
                continue
            column_match = _ADD_COLUMN.match(clause)
            if column_match:
                column = _identifier(column_match.group("column"))
                facts.append(
                    ProviderFact(
                        kind="column",
                        subject=f"{table}.{column}",
                        summary=f"Added column: {table}.{column}",
                        operation="added",
                        sources=(source,),
                    )
                )
                continue
            return None
        elif lead == "drop":
            if re.match(r"drop\s+constraint", clause, re.IGNORECASE):
                continue
            column_match = _DROP_COLUMN.match(clause)
            if column_match:
                column = _identifier(column_match.group("column"))
                facts.append(
                    ProviderFact(
                        kind="column",
                        subject=f"{table}.{column}",
                        summary=f"Removed column: {table}.{column}",
                        operation="removed",
                        sources=(source,),
                    )
                )
                continue
            return None
        elif lead == "rename":
            rename_match = _RENAME_COLUMN.match(clause)
            if rename_match:
                old = _identifier(rename_match.group("old"))
                new = _identifier(rename_match.group("new"))
                facts.append(
                    ProviderFact(
                        kind="column",
                        subject=f"{table}.{new}",
                        summary=(
                            f"Renamed column: {table}.{old} → {table}.{new}"
                        ),
                        operation="renamed",
                        sources=(source,),
                        attributes=(("renamed_from", f"{table}.{old}"),),
                    )
                )
                continue
            return None
        elif lead == "alter":
            continue
        else:
            return None
    return tuple(facts) if facts else None


def _split_clauses(body: str) -> tuple[str, ...]:
    clauses: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            clauses.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        clauses.append(tail)
    return tuple(item for item in clauses if item)


def _created_columns(text: str) -> tuple[str, ...]:
    open_index = text.find("(")
    if open_index < 0:
        return ()
    depth = 0
    close_index = -1
    for position in range(open_index, len(text)):
        if text[position] == "(":
            depth += 1
        elif text[position] == ")":
            depth -= 1
            if depth == 0:
                close_index = position
                break
    body = text[open_index + 1 : close_index if close_index > 0 else len(text)]
    columns = []
    for clause in _split_clauses(body):
        lead = clause.split(None, 1)[0] if clause.split() else ""
        if not lead or lead.casefold() in _NON_COLUMN_LEADS:
            continue
        columns.append(_identifier(lead))
    return tuple(dict.fromkeys(columns))


def _identifier(raw: str) -> str:
    return raw.strip().strip('"`[]')
