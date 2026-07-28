"""Integrity and lazy-loading tests for the reviewed GCS corpus."""

import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from defense_research_agent.domain import PublicationType, ResearchPublication
from defense_research_agent.repositories import GcsResearchPublicationRepository
from defense_research_agent.services import (
    build_corpus_index_manifest,
    corpus_manifest_object_name,
)

NOW = datetime(2026, 7, 28, tzinfo=UTC)


class FakeBlob:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.size: int | None = len(payload)
        self.reloads = 0
        self.downloads = 0

    def reload(self, *, timeout: int) -> None:
        assert timeout == 60
        self.reloads += 1

    def download_as_bytes(self, *, timeout: int) -> bytes:
        assert timeout == 60
        self.downloads += 1
        return self.payload


class FakeBucket:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.blobs = {name: FakeBlob(payload) for name, payload in payloads.items()}
        self.requested_names: list[str] = []

    def blob(self, blob_name: str) -> FakeBlob:
        self.requested_names.append(blob_name)
        return self.blobs[blob_name]


class FakeStorageClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self.bucket_value = bucket
        self.bucket_names: list[str] = []

    def bucket(self, bucket_name: str) -> FakeBucket:
        self.bucket_names.append(bucket_name)
        return self.bucket_value


def _index_payload() -> bytes:
    publication = ResearchPublication(
        publication_id="pub:gcs:1",
        publication_type=PublicationType.DEFENSE_POLICY_RESEARCH,
        title="국방 인공지능 정책",
        content="공개자료 기반 정책 연구 내용",
    )
    return f"{publication.model_dump_json()}\n".encode()


def _repository(
    index_payload: bytes,
    *,
    mutate_manifest: dict[str, object] | None = None,
) -> tuple[GcsResearchPublicationRepository, FakeBucket]:
    manifest = build_corpus_index_manifest(
        index_payload,
        reviewed_by="reviewer@example.com",
        reviewed_at=NOW,
    )
    manifest_payload = manifest.model_dump(mode="json")
    if mutate_manifest:
        manifest_payload.update(mutate_manifest)
    manifest_bytes = (json.dumps(manifest_payload, sort_keys=True) + "\n").encode()
    manifest_object = corpus_manifest_object_name(manifest)
    bucket = FakeBucket(
        {
            manifest_object: manifest_bytes,
            manifest.index_object: index_payload,
        }
    )
    repository = GcsResearchPublicationRepository(
        "private-corpus-bucket",
        manifest_object,
        client=cast(Any, FakeStorageClient(bucket)),
    )
    return repository, bucket


def test_repository_lazy_loads_exact_objects_and_searches_validated_index() -> None:
    repository, bucket = _repository(_index_payload())

    assert bucket.requested_names == []
    results = repository.search("인공지능")
    repeated = repository.search("인공지능")

    assert [result.publication.publication_id for result in results] == ["pub:gcs:1"]
    assert repeated == results
    assert len(bucket.requested_names) == 2
    assert all(blob.downloads == 1 for blob in bucket.blobs.values())


@pytest.mark.parametrize(
    ("mutate_manifest", "error"),
    [
        ({"index_sha256": "0" * 64}, "checksum"),
        ({"index_size_bytes": 1}, "size"),
        ({"publication_count": 2}, "publication count"),
        ({"review_status": "pending"}, "schema"),
    ],
)
def test_repository_rejects_manifest_or_index_mismatch(
    mutate_manifest: dict[str, object],
    error: str,
) -> None:
    repository, _ = _repository(
        _index_payload(),
        mutate_manifest=mutate_manifest,
    )

    with pytest.raises(ValueError, match=error):
        repository.search("인공지능")


def test_repository_rejects_oversize_metadata_before_download() -> None:
    repository, bucket = _repository(_index_payload())
    index_blob = next(blob for name, blob in bucket.blobs.items() if name.endswith(".jsonl"))
    index_blob.size = 101 * 1024 * 1024

    with pytest.raises(ValueError, match="byte limit"):
        repository.search("인공지능")
    assert index_blob.downloads == 0
