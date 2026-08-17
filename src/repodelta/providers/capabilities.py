from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Literal

CapabilityLevel = Literal["full", "partial", "unavailable"]
FileSelectorKind = Literal["suffix", "exact", "basename_glob", "path_glob"]


@dataclass(frozen=True)
class ProviderCapability:
    """One declared kind of fact a provider can produce, with an honesty level.

    A capability names what the provider can actually know, never a review
    conclusion. `partial` means the provider observes the dimension without
    guaranteeing completeness; `unavailable` documents a known blind spot.
    """

    name: str
    level: CapabilityLevel = "full"


@dataclass(frozen=True)
class LanguageCapabilities:
    """Per-language refinement of a provider's declared capabilities."""

    language: str
    capabilities: tuple[ProviderCapability, ...] = ()


@dataclass(frozen=True)
class FileSelector:
    """One deterministic changed-path predicate used for provider planning."""

    kind: FileSelectorKind
    pattern: str

    def matches(self, path: str) -> bool:
        normalized = path.casefold().replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        if self.kind == "suffix":
            return name.endswith(self.pattern)
        if self.kind == "exact":
            return normalized == self.pattern
        if self.kind == "basename_glob":
            return fnmatchcase(name, self.pattern)
        return fnmatchcase(normalized, self.pattern)


@dataclass(frozen=True)
class ProviderDescriptor:
    """What one evidence provider can honestly claim, declared before any run.

    Selectors route changed files toward the provider; the provider keeps
    final authority over per-file applicability and reports the difference as
    coverage, so selector over-inclusion never fabricates evidence.
    """

    provider: str
    capabilities: tuple[ProviderCapability, ...] = ()
    languages: tuple[LanguageCapabilities, ...] = ()
    selectors: tuple[FileSelector, ...] = ()
    schema_version: str = "provider_descriptor.v1"

    def capability_level(self, name: str) -> CapabilityLevel:
        return next(
            (item.level for item in self.capabilities if item.name == name),
            "unavailable",
        )

    def matches(self, path: str) -> bool:
        return any(selector.matches(path) for selector in self.selectors)

    def validate_consistency(self) -> None:
        if not self.provider:
            raise ValueError("provider descriptor requires a provider name")
        capability_names = tuple(item.name for item in self.capabilities)
        if len(set(capability_names)) != len(capability_names):
            raise ValueError(
                f"{self.provider}: descriptor declares duplicate capabilities"
            )
        languages = tuple(item.language for item in self.languages)
        if len(set(languages)) != len(languages):
            raise ValueError(
                f"{self.provider}: descriptor declares duplicate languages"
            )
        for language in self.languages:
            names = tuple(item.name for item in language.capabilities)
            if len(set(names)) != len(names):
                raise ValueError(
                    f"{self.provider}: {language.language} declares "
                    "duplicate capabilities"
                )
