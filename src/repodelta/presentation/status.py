from __future__ import annotations

from repodelta.model.contracts import EvidenceProviderCoverage, StructuralCoverage


def format_structural_coverage(coverage: StructuralCoverage) -> str:
    """Format canonical coverage without inspecting provider diagnostics."""

    if coverage.state == "disabled":
        return "Structural mapping: disabled · change-relation fallback used"
    if coverage.state == "unavailable":
        return "Structural mapping: unavailable · change-relation fallback used"
    if coverage.state == "available":
        traversal = (
            f"{coverage.complete_seed_count}/{coverage.seed_count} seeds complete"
            + (
                f", {coverage.truncated_seed_count} truncated"
                if coverage.truncated_seed_count
                else ""
            )
        )
        return (
            "Structural mapping: Codegraph available · "
            f"{coverage.mapped_hunk_count}/{coverage.hunk_count} hunks mapped to "
            f"{coverage.symbol_count} symbols · {coverage.path_count} bounded paths · "
            f"{traversal} · "
            f"{_base_coverage(coverage)} · uncovered change relations retained"
        )
    if coverage.state == "partial":
        return (
            "Structural mapping: partial · "
            f"{coverage.indexed_files}/{coverage.requested_files} changed files indexed · "
            f"{_base_coverage(coverage)} · "
            "change-relation fallback used for uncovered changes"
        )
    reason = {
        "stale": "Codegraph index is stale",
        "invalid": "Codegraph index schema is incompatible",
        "error": "Codegraph index could not be read",
        "missing": (
            "Codegraph index not found"
            if coverage.missing_reason == "index_absent"
            else "no changed files are present in the Codegraph index"
        ),
    }[coverage.state]
    return f"Structural mapping: skipped · {reason} · change-relation fallback used"


def format_provider_coverage(coverage: EvidenceProviderCoverage) -> str:
    """Format one provider's canonical coverage row without interpreting it."""

    prefix = f"Evidence provider {coverage.provider}"
    if coverage.state == "not_requested":
        return (
            f"{prefix}: not requested · "
            f"{coverage.requested_file_count} matched files not examined"
        )
    if coverage.state == "not_applicable":
        return f"{prefix}: not applicable"
    if coverage.state == "unavailable":
        return f"{prefix}: unavailable"
    parts = [
        f"{prefix}: {coverage.state}",
        (
            f"{coverage.examined_file_count}/{coverage.requested_file_count} "
            "matched files examined"
        ),
        f"{coverage.fact_count} facts",
    ]
    if coverage.limits:
        parts.append(
            "limits: "
            + ", ".join(item.replace("_", " ") for item in coverage.limits)
        )
    declared = ", ".join(
        f"{item.name} {item.level}" for item in coverage.capabilities
    )
    if declared:
        parts.append(f"declares {declared}")
    return " · ".join(parts)


def format_unclaimed_files(unclaimed: tuple[str, ...]) -> str:
    """State where no declared provider can assert anything at all."""

    return (
        f"Evidence coverage gap: {len(unclaimed)} changed "
        f"file{'s' if len(unclaimed) != 1 else ''} matched no declared "
        "evidence provider"
    )


def _base_coverage(coverage: StructuralCoverage) -> str:
    if coverage.base_state in {"available", "partial"}:
        return (
            f"base {coverage.base_mapped_hunk_count}/"
            f"{coverage.base_hunk_count} hunks mapped to "
            f"{coverage.base_symbol_count} symbols"
        )
    return f"base {coverage.base_state}"
