"""Adapter for page text already extracted into document metadata JSON files."""

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import cast

from defense_research_agent.domain.common import Checksum, Label
from defense_research_agent.domain.provenance import ExtractionProvenance
from defense_research_agent.domain.publication import PublicationPage
from defense_research_agent.search.parsers.base import (
    DocumentParser,
    ParserCapability,
    ParserErrorCode,
    ParseResult,
    ParserFailure,
)


class JsonPageParser(DocumentParser):
    """Expose observed ``page_texts`` records through ``DocumentParser``.

    This adapter never parses the referenced PDF. The JSON ``text`` value is the
    canonical page content; the advisory ``char_count`` value is not trusted or
    propagated because its actual value is always reproducible as ``len(text)``.
    """

    @property
    def name(self) -> Label:
        """Return the stable provenance name for this adapter."""
        return "json-page-texts"

    @property
    def version(self) -> Label:
        """Return the version of the page conversion behavior."""
        return "1.0.0"

    @property
    def capabilities(self) -> frozenset[ParserCapability]:
        """Declare page text production and evidence-based OCR signalling."""
        return frozenset(
            {
                ParserCapability.TEXT,
                ParserCapability.PAGE_TEXT,
                ParserCapability.OCR_SIGNAL,
            }
        )

    def supports(self, source_path: Path) -> bool:
        """Claim JSON sources regardless of extension casing."""
        return source_path.suffix.casefold() == ".json"

    def parse(self, source_path: Path, source_checksum: Checksum) -> ParseResult:
        """Convert valid JSON page records and return expected failures as data."""
        if not self.supports(source_path):
            return self._failed_result(
                source_checksum,
                ParserErrorCode.UNSUPPORTED_FORMAT,
                "source file is not a JSON document",
            )

        try:
            source_bytes = source_path.read_bytes()
        except OSError:
            return self._failed_result(
                source_checksum,
                ParserErrorCode.UNREADABLE_SOURCE,
                "source JSON file could not be read",
            )

        actual_checksum = sha256(source_bytes).hexdigest()
        provenance = self._provenance(actual_checksum)
        if actual_checksum != source_checksum:
            return ParseResult(
                provenance=provenance,
                failures=[
                    ParserFailure(
                        code=ParserErrorCode.CHECKSUM_MISMATCH,
                        message="source checksum does not match the file bytes",
                    )
                ],
            )

        try:
            source_text = source_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return self._failed_result(
                actual_checksum,
                ParserErrorCode.DECODE_ERROR,
                "source JSON is not valid UTF-8",
            )

        try:
            payload = cast(object, json.loads(source_text))
        except (json.JSONDecodeError, RecursionError):
            return self._failed_result(
                actual_checksum,
                ParserErrorCode.CORRUPT_STRUCTURE,
                "source file is not valid JSON",
            )

        if not isinstance(payload, dict):
            return self._failed_result(
                actual_checksum,
                ParserErrorCode.CORRUPT_STRUCTURE,
                "JSON document must be an object",
            )

        if "page_texts" not in payload or payload["page_texts"] == []:
            return self._failed_result(
                actual_checksum,
                ParserErrorCode.EMPTY_DOCUMENT,
                "page_texts is missing or empty",
                requires_ocr=True,
            )

        raw_pages = payload["page_texts"]
        if not isinstance(raw_pages, list):
            return self._failed_result(
                actual_checksum,
                ParserErrorCode.CORRUPT_STRUCTURE,
                "page_texts must be a list",
            )

        return self._parse_pages(raw_pages, provenance)

    def _parse_pages(
        self,
        raw_pages: list[object],
        provenance: ExtractionProvenance,
    ) -> ParseResult:
        page_number_counts = Counter(
            page_number
            for raw_page in raw_pages
            if isinstance(raw_page, dict)
            if (page_number := self._page_number(raw_page.get("page"))) is not None
        )
        duplicate_numbers = {
            page_number for page_number, count in page_number_counts.items() if count > 1
        }
        reported_duplicates: set[int] = set()
        pages: list[PublicationPage] = []
        failures: list[ParserFailure] = []

        for raw_page in raw_pages:
            if not isinstance(raw_page, dict):
                failures.append(
                    ParserFailure(
                        code=ParserErrorCode.CORRUPT_STRUCTURE,
                        message="page_texts entry must be an object",
                    )
                )
                continue

            page_number = self._page_number(raw_page.get("page"))
            if page_number is None:
                failures.append(
                    ParserFailure(
                        code=ParserErrorCode.CORRUPT_STRUCTURE,
                        message="page must be an integer greater than or equal to 1",
                    )
                )
                continue

            if page_number in duplicate_numbers:
                if page_number not in reported_duplicates:
                    failures.append(
                        ParserFailure(
                            code=ParserErrorCode.CORRUPT_STRUCTURE,
                            message="duplicate page number",
                            page_number=page_number,
                        )
                    )
                    reported_duplicates.add(page_number)
                continue

            text = raw_page.get("text")
            if not isinstance(text, str):
                failures.append(
                    ParserFailure(
                        code=ParserErrorCode.CORRUPT_STRUCTURE,
                        message="page text must be a string",
                        page_number=page_number,
                    )
                )
                continue

            # The actual string length is authoritative; ``char_count`` is advisory.
            actual_char_count = len(text)
            if actual_char_count == 0 or not text.strip():
                failures.append(
                    ParserFailure(
                        code=ParserErrorCode.EMPTY_PAGE,
                        message="page text is blank",
                        page_number=page_number,
                    )
                )
                continue

            pages.append(
                PublicationPage(
                    page_number=page_number,
                    text=text,
                    provenance=provenance,
                    section_title=None,
                )
            )

        pages.sort(key=lambda page: page.page_number)
        return ParseResult(
            provenance=provenance,
            pages=pages,
            failures=failures,
            requires_ocr=any(failure.code is ParserErrorCode.EMPTY_PAGE for failure in failures),
        )

    def _failed_result(
        self,
        source_checksum: Checksum,
        code: ParserErrorCode,
        message: Label,
        *,
        requires_ocr: bool = False,
    ) -> ParseResult:
        return ParseResult(
            provenance=self._provenance(source_checksum),
            failures=[ParserFailure(code=code, message=message)],
            requires_ocr=requires_ocr,
        )

    def _provenance(self, source_checksum: Checksum) -> ExtractionProvenance:
        return ExtractionProvenance(
            parser_name=self.name,
            parser_version=self.version,
            source_checksum=source_checksum,
        )

    @staticmethod
    def _page_number(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None
        return value
