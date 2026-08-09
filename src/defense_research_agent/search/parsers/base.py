"""Document parser contract.

A parser turns one read-only source file into page-level text plus an explicit
account of what failed. Expected failures are returned as :class:`ParserFailure`
entries rather than raised, so a partially extracted document still yields the
pages it did produce. Only programming errors propagate as exceptions.

Provider-specific libraries stay inside adapter implementations. Nothing in this
module may import a PDF or OCR library.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from pathlib import Path

from pydantic import Field, PositiveInt, model_validator

from defense_research_agent.domain.common import Checksum, DomainModel, Label
from defense_research_agent.domain.provenance import ExtractionProvenance
from defense_research_agent.domain.publication import PublicationPage


class ParserCapability(StrEnum):
    """What a parser implementation is able to produce."""

    TEXT = "text"
    PAGE_TEXT = "page_text"
    TABLES = "tables"
    OCR_SIGNAL = "ocr_signal"


class ParserErrorCode(StrEnum):
    """Stable failure taxonomy shared by every parser adapter.

    These codes are part of the contract. Adapters map provider-specific errors
    onto them and must not invent new codes, so quality gates and re-extraction
    queues can branch on a fixed set.
    """

    UNSUPPORTED_FORMAT = "unsupported_format"
    UNREADABLE_SOURCE = "unreadable_source"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    ENCRYPTED = "encrypted"
    CORRUPT_STRUCTURE = "corrupt_structure"
    DECODE_ERROR = "decode_error"
    EMPTY_PAGE = "empty_page"
    EMPTY_DOCUMENT = "empty_document"
    TIMEOUT = "timeout"


class ParserFailure(DomainModel):
    """One recoverable extraction failure, scoped to a page when applicable.

    ``message`` is for operators. It must not contain provider raw responses,
    credentials, or file contents.
    """

    code: ParserErrorCode
    message: Label
    page_number: PositiveInt | None = None


class ParseResult(DomainModel):
    """Outcome of one parse run over one source file."""

    provenance: ExtractionProvenance
    pages: list[PublicationPage] = Field(default_factory=list)
    failures: list[ParserFailure] = Field(default_factory=list)
    requires_ocr: bool = False

    @property
    def is_empty(self) -> bool:
        """Whether the run produced no page text at all."""
        return not self.pages

    @model_validator(mode="after")
    def empty_result_must_explain_itself(self) -> "ParseResult":
        """Reject a silent empty parse."""
        if not self.pages and not self.failures:
            raise ValueError("a parse producing no pages must record at least one failure")
        return self

    @model_validator(mode="after")
    def page_numbers_must_be_unique_and_ordered(self) -> "ParseResult":
        """Keep page locators usable as citation anchors."""
        numbers = [page.page_number for page in self.pages]
        if numbers != sorted(numbers):
            raise ValueError("pages must be ordered by ascending page_number")
        if len(numbers) != len(set(numbers)):
            raise ValueError("page_number must not repeat within one parse result")
        return self


class DocumentParser(ABC):
    """Interface implemented by every source-format adapter."""

    @property
    @abstractmethod
    def name(self) -> Label:
        """Stable adapter name recorded in provenance."""

    @property
    @abstractmethod
    def version(self) -> Label:
        """Adapter version. Bump whenever extracted text can change."""

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[ParserCapability]:
        """What this adapter can produce."""

    @abstractmethod
    def supports(self, source_path: Path) -> bool:
        """Whether this adapter claims the given source file."""

    @abstractmethod
    def parse(self, source_path: Path, source_checksum: Checksum) -> ParseResult:
        """Extract pages from a read-only source file.

        Implementations must not write to ``source_path`` or anywhere under the
        repository ``data/`` tree, and must record ``source_checksum`` in the
        returned provenance so derived artifacts stay reproducible.
        """
