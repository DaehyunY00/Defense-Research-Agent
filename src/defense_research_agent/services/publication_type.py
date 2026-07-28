"""Deterministic classification of observed KIDA publication types."""

from collections.abc import Mapping
from pathlib import Path
from unicodedata import normalize

from pydantic import JsonValue

from defense_research_agent.domain import PublicationType

_FOLDER_ALIASES = {
    "brief": PublicationType.KIDA_BRIEF,
    "kida brief": PublicationType.KIDA_BRIEF,
    "국방논단": PublicationType.DEFENSE_FORUM,
    "국방정책연구": PublicationType.DEFENSE_POLICY_RESEARCH,
    "연구보고서": PublicationType.RESEARCH_REPORT,
    "안보전략포커스": PublicationType.SECURITY_STRATEGY_FOCUS,
}

_CONTENT_SIGNALS = (
    ("안보전략포커스", PublicationType.SECURITY_STRATEGY_FOCUS),
    ("kida brief", PublicationType.KIDA_BRIEF),
    ("국방정책연구", PublicationType.DEFENSE_POLICY_RESEARCH),
    ("국방논단", PublicationType.DEFENSE_FORUM),
)


def classify_publication_type(
    path: Path,
    metadata: Mapping[str, JsonValue] | None = None,
    content: str | None = None,
) -> PublicationType:
    """Classify using metadata, folder, filename, then content in that order."""
    metadata_category = metadata.get("category") if metadata is not None else None
    if isinstance(metadata_category, str):
        classified = _from_alias(metadata_category)
        if classified is not None:
            return classified

    for part in reversed(path.parts[:-1]):
        classified = _from_alias(part)
        if classified is not None:
            return classified

    filename_text = _normalize_text(path.stem)
    for signal, publication_type in _CONTENT_SIGNALS:
        if signal in filename_text:
            return publication_type

    if content is not None:
        content_text = _normalize_text(content[:50_000])
        for signal, publication_type in _CONTENT_SIGNALS:
            if signal in content_text:
                return publication_type

    return PublicationType.OTHER


def _from_alias(value: str) -> PublicationType | None:
    return _FOLDER_ALIASES.get(_normalize_text(value))


def _normalize_text(value: str) -> str:
    return normalize("NFC", value).strip().casefold()
