"""Deterministic page-aware publication chunking."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from hashlib import sha256

from defense_research_agent.domain import PublicationChunk, PublicationPage, ResearchPublication

DEFAULT_CHUNKING_VERSION = "page-window-v1"


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

    def chunk(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
    ) -> list[PublicationChunk]:
        """Build stable chunks while preserving exact page text and page ranges."""
        _validate_page_order(pages)
        chunks: list[PublicationChunk] = []
        pending: list[PublicationPage] = []
        pending_characters = 0

        def emit_pending() -> None:
            nonlocal pending, pending_characters
            if not pending:
                return
            text = "\n\n".join(page.text for page in pending)
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
                    ),
                    publication_id=publication.publication_id,
                    text=text,
                    page_start=page_start,
                    page_end=page_end,
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
                emit_pending()
                continue

            separator_characters = 2 if pending else 0
            crosses_page_gap = bool(pending and page.page_number != pending[-1].page_number + 1)
            changes_section = bool(pending and page.section_title != pending[0].section_title)
            exceeds_limit = bool(
                pending
                and pending_characters + separator_characters + len(page.text)
                > self._max_characters
            )
            if crosses_page_gap or changes_section or exceeds_limit:
                emit_pending()
                separator_characters = 0

            pending.append(page)
            pending_characters += separator_characters + len(page.text)
            if pending_characters >= self._max_characters:
                emit_pending()

        emit_pending()
        return chunks


def _validate_page_order(pages: Sequence[PublicationPage]) -> None:
    previous_page_number = 0
    for page in pages:
        if page.page_number <= previous_page_number:
            raise ValueError("pages must be in strictly increasing page-number order")
        previous_page_number = page.page_number


def _chunk_id(
    publication_id: str,
    chunk_index: int,
    page_start: int,
    page_end: int,
    checksum: str,
    chunking_version: str,
) -> str:
    identity = "\0".join(
        (
            "publication-chunk:v1",
            chunking_version,
            publication_id,
            str(chunk_index),
            str(page_start),
            str(page_end),
            checksum,
        )
    )
    return f"chunk:{sha256(identity.encode('utf-8')).hexdigest()[:32]}"
