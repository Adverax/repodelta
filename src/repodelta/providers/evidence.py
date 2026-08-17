from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from repodelta.model.contracts import (
    ChangeOperation,
    Diagnostic,
    EvidenceClassification,
    FactProfile,
    SourceRef,
)
from repodelta.changes.hunks import DiffHunkCollection
from repodelta.providers.capabilities import ProviderCapability, ProviderDescriptor

EvidenceContributionState = Literal[
    "complete",
    "partial",
    "unavailable",
    "not_applicable",
]

_CHANGED_OPERATIONS = frozenset(
    {"added", "modified", "replaced", "removed", "renamed"}
)


@dataclass(frozen=True)
class ProviderFact:
    """One normalized fact asserted by an evidence provider.

    `kind` names the subject species (`column`, `api_operation`, `table`) and
    `operation` carries the observed delta, so contradictory assertions about
    one subject stay distinct facts instead of silently merging.
    """

    kind: str
    subject: str
    summary: str
    operation: ChangeOperation = "observed"
    classification: EvidenceClassification = "code"
    profile: FactProfile = "schema"
    sources: tuple[SourceRef, ...] = ()
    attributes: tuple[tuple[str, str], ...] = ()

    def validate_consistency(self) -> None:
        if not self.kind or not self.subject or not self.summary:
            raise ValueError("provider fact requires kind, subject, and summary")
        if (
            self.operation not in _CHANGED_OPERATIONS
            and self.operation != "observed"
        ):
            raise ValueError(
                f"{self.kind} {self.subject}: unsupported fact operation "
                f"{self.operation}"
            )

    @property
    def changed(self) -> bool:
        return self.operation in _CHANGED_OPERATIONS


@dataclass(frozen=True)
class EvidenceContributionCoverage:
    """Where the provider was actually able to assert anything."""

    state: EvidenceContributionState
    requested_files: tuple[str, ...] = ()
    examined_files: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if set(self.examined_files) - set(self.requested_files):
            raise ValueError("examined files must belong to the request")
        if self.state == "complete" and self.limits:
            raise ValueError("complete contribution coverage cannot carry limits")
        if self.state == "partial" and not self.limits:
            raise ValueError("partial contribution coverage requires a limit")


@dataclass(frozen=True)
class EvidenceContribution:
    """Read-only facts plus coverage from one provider. Never a conclusion."""

    provider: str
    facts: tuple[ProviderFact, ...] = ()
    coverage: EvidenceContributionCoverage = EvidenceContributionCoverage(
        state="not_applicable"
    )
    capabilities: tuple[ProviderCapability, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    schema_version: str = "evidence_contribution.v1"

    def validate_consistency(self) -> None:
        if not self.provider:
            raise ValueError("evidence contribution requires a provider name")
        identities = tuple(
            (item.kind, item.subject, item.operation) for item in self.facts
        )
        if len(set(identities)) != len(identities):
            raise ValueError(
                f"{self.provider}: contribution contains duplicate facts"
            )
        for fact in self.facts:
            fact.validate_consistency()
        if self.coverage.state in {"unavailable", "not_applicable"} and self.facts:
            raise ValueError(
                f"{self.provider}: {self.coverage.state} coverage cannot "
                "carry facts"
            )


@runtime_checkable
class EvidenceProvider(Protocol):
    """Capability-declaring fact provider. Providers never produce conclusions."""

    def descriptor(self) -> ProviderDescriptor: ...

    def contribute(
        self,
        changes: DiffHunkCollection,
        *,
        matched_files: tuple[str, ...],
    ) -> EvidenceContribution: ...
