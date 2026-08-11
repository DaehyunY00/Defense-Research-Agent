"""Deterministic page-aware publication chunking."""

import json
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import NonNegativeInt, PositiveInt

from defense_research_agent.domain import (
    Checksum,
    DomainModel,
    ExtractionProvenance,
    Label,
    PublicationChunk,
    PublicationPage,
    PublicationPageSpan,
    ResearchPublication,
)
from defense_research_agent.path_safety import ensure_outside_read_only_data

DEFAULT_CHUNKING_VERSION = "page-window-v1"
CHUNK_MANIFEST_VERSION = "publication-chunks-manifest-v2"
CHUNKS_FILENAME = "chunks.jsonl"
CHUNK_MANIFEST_FILENAME = "chunks.manifest.json"
PAGE_SEPARATOR = "\n\n"

type _ChunkBoundaryName = Literal[
    "blank-page",
    "section-title-change",
    "page-gap",
    "parser-provenance-change",
    "max-characters",
]


class ChunkingDocument(NamedTuple):
    """One publication and its ordered parser-produced pages."""

    publication: ResearchPublication
    pages: Sequence[PublicationPage]
    dropped_empty_page_count: int = 0


class ChunkingSettings(DomainModel):
    """Complete deterministic settings recorded with a chunk corpus."""

    max_characters: PositiveInt
    page_separator: Literal["\n\n"] = "\n\n"
    page_unit: Literal["whole-page"] = "whole-page"
    overlap_unit: Literal["none"] = "none"
    overlap_size: Literal[0] = 0
    table_handling: Literal["preserve-in-page-text"] = "preserve-in-page-text"
    footnote_handling: Literal["preserve-in-page-text"] = "preserve-in-page-text"
    bibliography_handling: Literal["preserve-in-page-text"] = "preserve-in-page-text"


class ChunkBoundaryFiringCounts(DomainModel):
    """Observed predicate matches while producing one complete chunk artifact."""

    blank_page: NonNegativeInt = 0
    section_title_change: NonNegativeInt = 0
    page_gap: NonNegativeInt = 0
    parser_provenance_change: NonNegativeInt = 0
    max_characters: NonNegativeInt = 0


class ParserProvenanceDistribution(DomainModel):
    """Counts grouped by the parser identity that produced pages and chunks."""

    parser_name: Label
    parser_version: Label
    document_count: NonNegativeInt
    page_count: NonNegativeInt
    chunk_count: NonNegativeInt


class ChunkArtifactManifest(DomainModel):
    """Content-bound manifest for a deterministic ``chunks.jsonl`` artifact."""

    manifest_version: Literal["publication-chunks-manifest-v2"] = "publication-chunks-manifest-v2"
    chunking_version: Label
    input_document_count: NonNegativeInt
    input_page_count: NonNegativeInt
    dropped_empty_page_count: NonNegativeInt
    chunk_count: NonNegativeInt
    boundary_firing_counts: ChunkBoundaryFiringCounts
    parser_provenance_distribution: list[ParserProvenanceDistribution]
    settings: ChunkingSettings
    chunks_filename: Literal["chunks.jsonl"] = "chunks.jsonl"
    chunks_sha256: Checksum
    chunks_size_bytes: NonNegativeInt


class _ChunkingRun(NamedTuple):
    chunks: list[PublicationChunk]
    boundary_firings: Counter[_ChunkBoundaryName]


class PublicationChunker(ABC):
    """Interface for deriving ordered chunks from parser-produced pages."""

    @abstractmethod
    def chunk(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> list[PublicationChunk]:
        """Return validated chunks linked to the supplied publication."""


class DeterministicPageChunker(PublicationChunker):
    """Group adjacent same-section pages without splitting page text."""

    def __init__(
        self,
        *,
        max_characters: int = 4_000,
        chunking_version: str = DEFAULT_CHUNKING_VERSION,
    ) -> None:
        if (
            isinstance(max_characters, bool)
            or not isinstance(max_characters, int)
            or max_characters <= 0
        ):
            raise ValueError("max_characters must be a positive integer")
        normalized_version = chunking_version.strip()
        if not normalized_version:
            raise ValueError("chunking_version must not be blank")
        self._max_characters = max_characters
        self._chunking_version = normalized_version

    @property
    def max_characters(self) -> int:
        """Return the configured whole-chunk character ceiling."""
        return self._max_characters

    @property
    def chunking_version(self) -> str:
        """Return the behavior version included in every chunk identity."""
        return self._chunking_version

    @property
    def settings(self) -> ChunkingSettings:
        """Return the complete reproducible policy for this chunker."""
        return ChunkingSettings(max_characters=self._max_characters)

    def chunk(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> list[PublicationChunk]:
        """Build stable chunks while preserving exact page text and page ranges."""
        return self._chunk_with_boundary_firings(publication, pages).chunks

    def _chunk_with_boundary_firings(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> _ChunkingRun:
        """Build chunks and independently count every matching boundary predicate."""
        _validate_page_order(pages)
        chunks: list[PublicationChunk] = []
        boundary_firings: Counter[_ChunkBoundaryName] = Counter()
        pending: list[PublicationPage] = []
        pending_characters = 0

        def emit_pending() -> None:
            nonlocal pending, pending_characters
            if not pending:
                return
            text = PAGE_SEPARATOR.join(page.text for page in pending)
            checksum = sha256(text.encode("utf-8")).hexdigest()
            chunk_index = len(chunks)
            page_start = pending[0].page_number
            page_end = pending[-1].page_number
            chunks.append(
                PublicationChunk(
                    chunk_id=_chunk_id(
                        publication.publication_id,
                        chunk_index,
                        page_start,
                        page_end,
                        checksum,
                        self._chunking_version,
                        pending[0].provenance,
                    ),
                    publication_id=publication.publication_id,
                    text=text,
                    page_start=page_start,
                    page_end=page_end,
                    page_spans=_page_spans(pending),
                    provenance=pending[0].provenance,
                    section_title=pending[0].section_title,
                    chunk_index=chunk_index,
                    checksum=checksum,
                    chunking_version=self._chunking_version,
                )
            )
            pending = []
            pending_characters = 0

        for page in pages:
            if not page.text.strip():
                boundary_firings["blank-page"] += 1
                emit_pending()
                continue

            separator_characters = len(PAGE_SEPARATOR) if pending else 0
            crosses_page_gap = bool(pending and page.page_number != pending[-1].page_number + 1)
            changes_section = bool(pending and page.section_title != pending[0].section_title)
            # Keep OCR fallback pages in their own chunk. Mixing extractor outputs
            # would make a chunk's provenance non-singular, so evidence traceability
            # takes precedence over preserving a paragraph across this boundary.
            changes_provenance = bool(pending and page.provenance != pending[0].provenance)
            exceeds_limit = bool(
                pending
                and pending_characters + separator_characters + len(page.text)
                > self._max_characters
            )
            # Every predicate is observed independently because more than one can
            # match the same page transition. One emission is sufficient regardless
            # of how many match, and parser text is never classified or rewritten.
            if changes_section:
                boundary_firings["section-title-change"] += 1
            if crosses_page_gap:
                boundary_firings["page-gap"] += 1
            if changes_provenance:
                boundary_firings["parser-provenance-change"] += 1
            if exceeds_limit:
                boundary_firings["max-characters"] += 1
            if changes_section or crosses_page_gap or changes_provenance or exceeds_limit:
                emit_pending()
                separator_characters = 0

            pending.append(page)
            pending_characters += separator_characters + len(page.text)
            if pending_characters >= self._max_characters:
                boundary_firings["max-characters"] += 1
                emit_pending()

        emit_pending()
        return _ChunkingRun(chunks=chunks, boundary_firings=boundary_firings)


def write_chunk_artifacts(
    documents: Sequence[ChunkingDocument],
    output_directory: Path,
    *,
    chunker: DeterministicPageChunker | None = None,
) -> ChunkArtifactManifest:
    """Write canonical chunks and a content-bound manifest outside read-only ``data/``.

    Documents are sorted by publication ID, so caller iteration order cannot affect
    artifact bytes. Duplicate publication IDs and invalid parser-drop counts are
    rejected because they would make the manifest's audit totals ambiguous.
    """
    ensure_outside_read_only_data(output_directory)
    resolved_output = output_directory.resolve()

    selected_chunker = chunker or DeterministicPageChunker()
    ordered_documents = sorted(documents, key=lambda document: document.publication.publication_id)
    publication_ids = [document.publication.publication_id for document in ordered_documents]
    if len(publication_ids) != len(set(publication_ids)):
        raise ValueError("publication_id must be unique within chunk artifacts")
    for document in ordered_documents:
        if (
            isinstance(document.dropped_empty_page_count, bool)
            or not isinstance(document.dropped_empty_page_count, int)
            or document.dropped_empty_page_count < 0
        ):
            raise ValueError("dropped_empty_page_count must be a non-negative integer")

    chunks: list[PublicationChunk] = []
    boundary_firings: Counter[_ChunkBoundaryName] = Counter()
    for document in ordered_documents:
        chunking_run = selected_chunker._chunk_with_boundary_firings(
            document.publication,
            document.pages,
        )
        chunks.extend(chunking_run.chunks)
        boundary_firings.update(chunking_run.boundary_firings)

    chunks_payload = b"".join(_canonical_json_line(chunk) for chunk in chunks)
    manifest = ChunkArtifactManifest(
        chunking_version=selected_chunker.chunking_version,
        input_document_count=len(ordered_documents),
        input_page_count=sum(len(document.pages) for document in ordered_documents),
        dropped_empty_page_count=sum(
            document.dropped_empty_page_count for document in ordered_documents
        ),
        chunk_count=len(chunks),
        boundary_firing_counts=_boundary_firing_counts(boundary_firings),
        parser_provenance_distribution=_parser_provenance_distribution(
            ordered_documents,
            chunks,
        ),
        settings=selected_chunker.settings,
        chunks_sha256=sha256(chunks_payload).hexdigest(),
        chunks_size_bytes=len(chunks_payload),
    )
    manifest_payload = _canonical_json_line(manifest)

    resolved_output.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(resolved_output / CHUNKS_FILENAME, chunks_payload)
    _atomic_write_bytes(resolved_output / CHUNK_MANIFEST_FILENAME, manifest_payload)
    return manifest


def _boundary_firing_counts(
    counts: Counter[_ChunkBoundaryName],
) -> ChunkBoundaryFiringCounts:
    return ChunkBoundaryFiringCounts(
        blank_page=counts["blank-page"],
        section_title_change=counts["section-title-change"],
        page_gap=counts["page-gap"],
        parser_provenance_change=counts["parser-provenance-change"],
        max_characters=counts["max-characters"],
    )


def _canonical_json_line(model: DomainModel) -> bytes:
    payload = json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{payload}\n".encode()


def _parser_provenance_distribution(
    documents: Sequence[ChunkingDocument],
    chunks: Sequence[PublicationChunk],
) -> list[ParserProvenanceDistribution]:
    document_counts: Counter[tuple[str, str]] = Counter()
    page_counts: Counter[tuple[str, str]] = Counter()
    chunk_counts: Counter[tuple[str, str]] = Counter()

    for document in documents:
        document_parser_keys: set[tuple[str, str]] = set()
        for page in document.pages:
            key = (page.provenance.parser_name, page.provenance.parser_version)
            document_parser_keys.add(key)
            page_counts[key] += 1
        document_counts.update(document_parser_keys)

    for chunk in chunks:
        key = (chunk.provenance.parser_name, chunk.provenance.parser_version)
        chunk_counts[key] += 1

    parser_keys = sorted(document_counts.keys() | page_counts.keys() | chunk_counts.keys())
    return [
        ParserProvenanceDistribution(
            parser_name=parser_name,
            parser_version=parser_version,
            document_count=document_counts[(parser_name, parser_version)],
            page_count=page_counts[(parser_name, parser_version)],
            chunk_count=chunk_counts[(parser_name, parser_version)],
        )
        for parser_name, parser_version in parser_keys
    ]


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)


def _validate_page_order(pages: Sequence[PublicationPage]) -> None:
    previous_page_number = 0
    for page in pages:
        if page.page_number <= previous_page_number:
            raise ValueError("pages must be in strictly increasing page-number order")
        previous_page_number = page.page_number


def _page_spans(pages: Sequence[PublicationPage]) -> list[PublicationPageSpan]:
    """Map the join separator to its preceding page and preserve next-page starts."""
    spans: list[PublicationPageSpan] = []
    start_offset = 0
    last_index = len(pages) - 1
    for index, page in enumerate(pages):
        end_offset = start_offset + len(page.text)
        if index < last_index:
            end_offset += len(PAGE_SEPARATOR)
        spans.append(
            PublicationPageSpan(
                page_number=page.page_number,
                start_offset=start_offset,
                end_offset=end_offset,
            )
        )
        start_offset = end_offset
    return spans


def _chunk_id(
    publication_id: str,
    chunk_index: int,
    page_start: int,
    page_end: int,
    checksum: str,
    chunking_version: str,
    provenance: ExtractionProvenance,
) -> str:
    identity = "\0".join(
        (
            "publication-chunk:v2",
            chunking_version,
            provenance.parser_name,
            provenance.parser_version,
            provenance.source_checksum,
            publication_id,
            str(chunk_index),
            str(page_start),
            str(page_end),
            checksum,
        )
    )
    return f"chunk:{sha256(identity.encode('utf-8')).hexdigest()[:32]}"
