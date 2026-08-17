from __future__ import annotations

from dataclasses import dataclass

from repodelta.providers.capabilities import ProviderDescriptor


@dataclass(frozen=True)
class ProviderPlanEntry:
    """One provider selected for the review with its matched changed files."""

    provider: str
    matched_files: tuple[str, ...]


@dataclass(frozen=True)
class ProviderPlan:
    """Deterministic routing of changed files to declared evidence providers.

    The plan is a dispatch decision, never a coverage claim: a matched file
    may still be reported not applicable by its provider, and unclaimed files
    document where no registered provider can assert anything.
    """

    requested_files: tuple[str, ...] = ()
    entries: tuple[ProviderPlanEntry, ...] = ()
    unclaimed_files: tuple[str, ...] = ()
    schema_version: str = "provider_plan.v1"

    def entry_for(self, provider: str) -> ProviderPlanEntry | None:
        return next(
            (item for item in self.entries if item.provider == provider),
            None,
        )

    def validate_consistency(self) -> None:
        providers = tuple(item.provider for item in self.entries)
        if len(set(providers)) != len(providers):
            raise ValueError("provider plan contains duplicate providers")
        if providers != tuple(sorted(providers)):
            raise ValueError("provider plan entries must use deterministic order")
        requested = set(self.requested_files)
        claimed: set[str] = set()
        for entry in self.entries:
            if not entry.matched_files:
                raise ValueError(
                    f"{entry.provider}: plan entry requires matched files"
                )
            if entry.matched_files != tuple(sorted(set(entry.matched_files))):
                raise ValueError(
                    f"{entry.provider}: matched files must be sorted and unique"
                )
            outside = set(entry.matched_files) - requested
            if outside:
                raise ValueError(
                    f"{entry.provider}: matched files outside the request: "
                    f"{sorted(outside)}"
                )
            claimed.update(entry.matched_files)
        if set(self.unclaimed_files) != requested - claimed:
            raise ValueError(
                "unclaimed files must be exactly the unmatched requested files"
            )
        if self.unclaimed_files != tuple(sorted(set(self.unclaimed_files))):
            raise ValueError("unclaimed files must be sorted and unique")


def plan_providers(
    changed_paths: tuple[str, ...],
    descriptors: tuple[ProviderDescriptor, ...],
) -> ProviderPlan:
    """Route changed paths to providers by declared selectors, deterministically."""

    names = tuple(item.provider for item in descriptors)
    if len(set(names)) != len(names):
        raise ValueError("provider planning requires unique provider names")
    requested = tuple(sorted({path for path in changed_paths if path}))
    entries = []
    claimed: set[str] = set()
    for descriptor in sorted(descriptors, key=lambda item: item.provider):
        descriptor.validate_consistency()
        matched = tuple(
            path for path in requested if descriptor.matches(path)
        )
        if not matched:
            continue
        claimed.update(matched)
        entries.append(
            ProviderPlanEntry(
                provider=descriptor.provider,
                matched_files=matched,
            )
        )
    plan = ProviderPlan(
        requested_files=requested,
        entries=tuple(entries),
        unclaimed_files=tuple(
            path for path in requested if path not in claimed
        ),
    )
    plan.validate_consistency()
    return plan
