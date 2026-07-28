"""Tests for deterministic publication type classification."""

from pathlib import Path

import pytest

from defense_research_agent.domain import JsonObject, PublicationType
from defense_research_agent.services.publication_type import classify_publication_type


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("Brief", PublicationType.KIDA_BRIEF),
        ("국방논단", PublicationType.DEFENSE_FORUM),
        ("국방정책연구", PublicationType.DEFENSE_POLICY_RESEARCH),
        ("연구보고서", PublicationType.RESEARCH_REPORT),
    ],
)
def test_metadata_category_has_highest_priority(
    category: str,
    expected: PublicationType,
) -> None:
    metadata: JsonObject = {"category": category}

    result = classify_publication_type(
        Path("data/국방논단/file.pdf"),
        metadata,
        "KIDA Brief",
    )

    assert result is expected


def test_classifier_uses_folder_filename_and_content_fallbacks() -> None:
    assert classify_publication_type(Path("data/Brief/file.pdf")) is PublicationType.KIDA_BRIEF
    assert (
        classify_publication_type(Path("data/mixed/안보전략포커스_시험.pdf"))
        is PublicationType.SECURITY_STRATEGY_FOCUS
    )
    assert (
        classify_publication_type(Path("data/mixed/file.json"), content="국방정책연구")
        is PublicationType.DEFENSE_POLICY_RESEARCH
    )
    assert classify_publication_type(Path("data/mixed/file.pdf")) is PublicationType.OTHER
