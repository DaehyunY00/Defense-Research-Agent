"""Normalization and TopicSignal conversion for untrusted external sources."""

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from hashlib import sha256
from typing import cast
from unicodedata import normalize
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import HttpUrl, JsonValue

from defense_research_agent.domain import (
    ExternalIssueNormalizationResult,
    ExternalIssueSearchResult,
    ExternalSource,
    ExternalSourceRelationship,
    JsonObject,
    ReliabilityTier,
    SourceRelationType,
    TopicSignal,
)
from defense_research_agent.issues.priority import external_source_priority

_TRACKING_QUERY_PARAMETERS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
    }
)
_PUBLISHER_ALIASES = {
    "국방부": "대한민국 국방부",
    "국방부(mnd)": "대한민국 국방부",
    "대한민국 국방부": "대한민국 국방부",
    "ministry of national defense": "대한민국 국방부",
    "국회 국방위원회": "대한민국 국회 국방위원회",
    "감사원": "대한민국 감사원",
    "csis": "전략국제문제연구소(CSIS)",
    "center for strategic and international studies": "전략국제문제연구소(CSIS)",
}
_CONFIDENCE_BY_TIER = {
    ReliabilityTier.TIER_1_OFFICIAL: 0.95,
    ReliabilityTier.TIER_2_INSTITUTIONAL: 0.8,
    ReliabilityTier.TIER_3_NEWS: 0.65,
    ReliabilityTier.TIER_4_UNVERIFIED: 0.4,
}
_TITLE_CHARACTER_PATTERN = re.compile(r"[^0-9a-z가-힣]+")
_SIMILAR_TITLE_THRESHOLD = 0.92


class ExternalIssueNormalizationService:
    """Normalize provenance fields without interpreting source instructions."""

    def normalize_search_result(
        self,
        search_result: ExternalIssueSearchResult,
        limit: int | None = None,
    ) -> ExternalIssueNormalizationResult:
        """Normalize, de-duplicate, rank, and convert a provider result."""
        normalized_sources, duplicates_removed = self.normalize_sources(search_result.sources)
        effective_limit = search_result.requested_limit if limit is None else max(limit, 0)
        limited_sources = normalized_sources[:effective_limit]
        return ExternalIssueNormalizationResult(
            search_status=search_result.status,
            sources=limited_sources,
            topic_signals=[self.to_topic_signal(source) for source in limited_sources],
            errors=search_result.errors,
            duplicates_removed=duplicates_removed,
        )

    def normalize_sources(
        self,
        sources: Sequence[ExternalSource],
    ) -> tuple[list[ExternalSource], int]:
        """Normalize sources and remove duplicate URLs or highly similar titles."""
        normalized_sources = sorted(
            (self.normalize_source(source) for source in sources),
            key=external_source_priority,
        )
        retained_sources: list[ExternalSource] = []
        duplicates_removed = 0
        for source in normalized_sources:
            duplicate_index = _find_duplicate_index(retained_sources, source)
            if duplicate_index is None:
                retained_sources.append(source)
                continue

            duplicates_removed += 1
            retained_source = retained_sources[duplicate_index]
            duplicate_relationship = ExternalSourceRelationship(
                relation_type=SourceRelationType.HAS_DUPLICATE,
                target_source_id=source.source_id,
            )
            relationships = _unique_relationships(
                [*retained_source.relationships, duplicate_relationship]
            )
            retained_sources[duplicate_index] = retained_source.model_copy(
                update={"relationships": relationships}
            )

        retained_sources.sort(key=external_source_priority)
        return retained_sources, duplicates_removed

    def normalize_source(self, source: ExternalSource) -> ExternalSource:
        """Canonicalize URL, publisher, labels, language, and relationships."""
        return ExternalSource(
            source_id=source.source_id,
            title=_collapse_whitespace(source.title),
            publisher=_normalize_publisher(source.publisher),
            publication_date=source.publication_date,
            url=_normalize_url(source.url),
            source_type=source.source_type,
            snippet=source.snippet,
            language=source.language.casefold() if source.language is not None else None,
            policy_domains=_unique_labels(source.policy_domains),
            countries=_unique_labels(source.countries),
            reliability_tier=source.reliability_tier,
            relationships=_unique_relationships(source.relationships),
            content_trust=source.content_trust,
            reviewed=source.reviewed,
        )

    def to_topic_signal(self, source: ExternalSource) -> TopicSignal:
        """Convert one untrusted source without executing or interpreting its text."""
        identity = f"external-signal:v1\0{source.source_id}\0{source.url}".encode()
        signal_digest = sha256(identity).hexdigest()[:24]
        external_metadata: JsonObject = {
            "publisher": source.publisher,
            "source_type": source.source_type.value,
            "reliability_tier": source.reliability_tier.value,
            "content_trust": source.content_trust.value,
            "reviewed": source.reviewed,
            "relationships": [
                relationship.model_dump(mode="json") for relationship in source.relationships
            ],
        }
        return TopicSignal(
            signal_id=f"signal:external:{signal_digest}",
            signal_type=f"external_{source.source_type.value}",
            title=source.title,
            summary=source.snippet,
            event_date=source.publication_date,
            policy_domains=source.policy_domains,
            countries=source.countries,
            organizations=[source.publisher],
            keywords=source.policy_domains,
            confidence=_CONFIDENCE_BY_TIER[source.reliability_tier],
            source_ids=[source.source_id],
            source_urls=[source.url],
            raw_metadata={"external_source": cast(JsonValue, external_metadata)},
        )


def _find_duplicate_index(
    retained_sources: Sequence[ExternalSource],
    candidate: ExternalSource,
) -> int | None:
    candidate_url = str(candidate.url)
    candidate_title = _canonical_title(candidate.title)
    for index, retained in enumerate(retained_sources):
        if str(retained.url) == candidate_url:
            return index
        retained_title = _canonical_title(retained.title)
        if (
            candidate_title
            and retained_title
            and SequenceMatcher(None, candidate_title, retained_title).ratio()
            >= _SIMILAR_TITLE_THRESHOLD
        ):
            return index
    return None


def _normalize_url(url: HttpUrl) -> HttpUrl:
    parsed = urlsplit(str(url))
    hostname = (parsed.hostname or "").casefold()
    port = parsed.port
    netloc = hostname
    if (
        port is not None
        and not (parsed.scheme.casefold() == "http" and port == 80)
        and not (parsed.scheme.casefold() == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"

    query_parameters = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in _TRACKING_QUERY_PARAMETERS
    ]
    query_parameters.sort()
    normalized_url = urlunsplit(
        (
            parsed.scheme.casefold(),
            netloc,
            parsed.path or "/",
            urlencode(query_parameters),
            "",
        )
    )
    return HttpUrl(normalized_url)


def _normalize_publisher(value: str) -> str:
    collapsed = _collapse_whitespace(value)
    return _PUBLISHER_ALIASES.get(collapsed.casefold(), collapsed)


def _unique_labels(values: Sequence[str]) -> list[str]:
    normalized_values = (_collapse_whitespace(value) for value in values if value.strip())
    return list(dict.fromkeys(normalized_values))


def _unique_relationships(
    relationships: Sequence[ExternalSourceRelationship],
) -> list[ExternalSourceRelationship]:
    unique_by_key = {
        (relationship.relation_type.value, relationship.target_source_id): relationship
        for relationship in relationships
    }
    return [unique_by_key[key] for key in sorted(unique_by_key)]


def _canonical_title(value: str) -> str:
    normalized_title = normalize("NFC", value).casefold()
    return _TITLE_CHARACTER_PATTERN.sub("", normalized_title)


def _collapse_whitespace(value: str) -> str:
    return " ".join(normalize("NFC", value).strip().split())
