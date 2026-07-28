"""Lazy, integrity-checked GCS repository for a reviewed public corpus."""

from collections.abc import Sequence
from datetime import date
from hashlib import sha256
from threading import Lock
from typing import Protocol, cast

from defense_research_agent.domain import (
    CorpusIndexManifest,
    PublicationDistribution,
    PublicationSearchFilters,
    PublicationSearchResult,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.repositories.base import ResearchPublicationRepository
from defense_research_agent.repositories.in_memory import (
    InMemoryResearchPublicationRepository,
)
from defense_research_agent.repositories.publication_index import (
    DEFAULT_MAX_CORPUS_BYTES,
    DEFAULT_MAX_CORPUS_PUBLICATIONS,
    parse_publication_index,
)

_MAX_MANIFEST_BYTES = 64 * 1024


class _StorageBlob(Protocol):
    size: int | None

    def reload(self, *, timeout: int) -> object: ...

    def download_as_bytes(self, *, timeout: int) -> object: ...


class _StorageBucket(Protocol):
    def blob(self, blob_name: str) -> _StorageBlob: ...


class _StorageClient(Protocol):
    def bucket(self, bucket_name: str) -> _StorageBucket: ...


class GcsResearchPublicationRepository(ResearchPublicationRepository):
    """Load one exact reviewed manifest and index, then search locally."""

    def __init__(
        self,
        bucket_name: str,
        manifest_object: str,
        *,
        client: _StorageClient | None = None,
        timeout_seconds: int = 60,
        max_index_bytes: int = DEFAULT_MAX_CORPUS_BYTES,
        max_publications: int = DEFAULT_MAX_CORPUS_PUBLICATIONS,
    ) -> None:
        if not bucket_name.strip() or timeout_seconds <= 0:
            raise ValueError("corpus bucket and positive timeout are required")
        if max_index_bytes <= 0 or max_publications <= 0:
            raise ValueError("corpus limits must be positive")
        self._manifest_object = _validate_manifest_object(manifest_object)
        if client is None:
            from google.cloud import storage  # type: ignore[attr-defined]

            client = cast(_StorageClient, storage.Client())
        self._bucket = client.bucket(bucket_name.strip())
        self._timeout_seconds = timeout_seconds
        self._max_index_bytes = max_index_bytes
        self._max_publications = max_publications
        self._loaded: InMemoryResearchPublicationRepository | None = None
        self._load_lock = Lock()

    def search(
        self,
        query: str,
        filters: PublicationSearchFilters | None = None,
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        return self._repository().search(query, filters, limit)

    def get_by_id(self, publication_id: str) -> ResearchPublication | None:
        return self._repository().get_by_id(publication_id)

    def find_similar(
        self,
        title: str | None,
        abstract: str | None,
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        return self._repository().find_similar(title, abstract, limit)

    def get_recent_publications(
        self,
        limit: int = 10,
        publication_types: Sequence[PublicationType] | None = None,
    ) -> list[ResearchPublication]:
        return self._repository().get_recent_publications(limit, publication_types)

    def get_publication_distribution(
        self,
        start_date: date | None,
        end_date: date | None,
    ) -> PublicationDistribution:
        return self._repository().get_publication_distribution(start_date, end_date)

    def find_related_by_keywords(
        self,
        keywords: Sequence[str],
        limit: int = 10,
    ) -> list[PublicationSearchResult]:
        return self._repository().find_related_by_keywords(keywords, limit)

    def _repository(self) -> InMemoryResearchPublicationRepository:
        if self._loaded is not None:
            return self._loaded
        with self._load_lock:
            if self._loaded is None:
                self._loaded = self._load()
            return self._loaded

    def _load(self) -> InMemoryResearchPublicationRepository:
        manifest_payload = self._download_bounded(
            self._manifest_object,
            _MAX_MANIFEST_BYTES,
        )
        try:
            manifest = CorpusIndexManifest.model_validate_json(manifest_payload)
        except ValueError as error:
            raise ValueError("corpus manifest schema is invalid") from error
        index_payload = self._download_bounded(
            manifest.index_object,
            self._max_index_bytes,
        )
        if len(index_payload) != manifest.index_size_bytes:
            raise ValueError("corpus index size does not match its reviewed manifest")
        if sha256(index_payload).hexdigest() != manifest.index_sha256:
            raise ValueError("corpus index checksum does not match its reviewed manifest")
        publications = parse_publication_index(
            index_payload,
            max_bytes=self._max_index_bytes,
            max_publications=self._max_publications,
        )
        if len(publications) != manifest.publication_count:
            raise ValueError("corpus publication count does not match its reviewed manifest")
        return InMemoryResearchPublicationRepository(publications)

    def _download_bounded(self, object_name: str, max_bytes: int) -> bytes:
        blob = self._bucket.blob(_validate_object_name(object_name))
        blob.reload(timeout=self._timeout_seconds)
        if blob.size is None or blob.size < 0:
            raise ValueError("corpus object size metadata is unavailable")
        if blob.size > max_bytes:
            raise ValueError("corpus object exceeds the configured byte limit")
        payload = blob.download_as_bytes(timeout=self._timeout_seconds)
        if not isinstance(payload, bytes):
            raise TypeError("Cloud Storage returned non-bytes corpus content")
        if len(payload) != blob.size:
            raise ValueError("corpus object download size does not match metadata")
        return payload


def _validate_manifest_object(object_name: str) -> str:
    normalized = _validate_object_name(object_name)
    if not normalized.startswith("corpus/manifests/") or not normalized.endswith(".json"):
        raise ValueError("corpus manifest must be under corpus/manifests")
    return normalized


def _validate_object_name(object_name: str) -> str:
    normalized = object_name.strip()
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("corpus object name is invalid")
    return normalized
