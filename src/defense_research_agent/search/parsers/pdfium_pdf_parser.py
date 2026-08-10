"""Direct page-text extraction from PDF files with pypdfium2."""

from hashlib import sha256
from pathlib import Path
from typing import Final, cast

import pypdfium2 as pdfium  # type: ignore[import-untyped]

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

_SCAN_IMAGE_COVERAGE_THRESHOLD: Final = 0.8


class PdfiumPdfParser(DocumentParser):
    """Extract exact PDFium page text while keeping recoverable failures as data.

    ``version`` is the adapter behavior version recorded as
    ``ExtractionProvenance.parser_version``. It must be bumped whenever a code or
    PDFium behavior change can alter extracted text or page inclusion.

    PDFium returns UTF-16 text. Decoding uses ``errors="strict"`` so an unpaired
    surrogate becomes a page-scoped ``DECODE_ERROR`` rather than disappearing.
    Valid Unicode scalar values, including code points classified as unassigned
    by this Python Unicode database, are preserved exactly: assignment can change
    between Unicode versions and is not sufficient evidence of corruption.
    """

    @property
    def name(self) -> Label:
        """Return the stable provenance name for this adapter."""
        return "pypdfium2-pdf"

    @property
    def version(self) -> Label:
        """Return the extraction behavior version used in provenance."""
        return "1.0.0"

    @property
    def capabilities(self) -> frozenset[ParserCapability]:
        """Declare text, page mapping, and evidence-based OCR signalling only."""
        return frozenset(
            {
                ParserCapability.TEXT,
                ParserCapability.PAGE_TEXT,
                ParserCapability.OCR_SIGNAL,
            }
        )

    def supports(self, source_path: Path) -> bool:
        """Claim PDF sources regardless of extension casing."""
        return source_path.suffix.casefold() == ".pdf"

    def parse(self, source_path: Path, source_checksum: Checksum) -> ParseResult:
        """Extract ordered pages without changing or retaining a handle to the PDF."""
        if not self.supports(source_path):
            return self._failed_result(
                source_checksum,
                ParserErrorCode.UNSUPPORTED_FORMAT,
                "source file is not a PDF document",
            )

        try:
            source_bytes = source_path.read_bytes()
        except OSError:
            return self._failed_result(
                source_checksum,
                ParserErrorCode.UNREADABLE_SOURCE,
                "source PDF file could not be read",
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

        if not source_bytes.startswith(b"%PDF-"):
            return self._failed_result(
                actual_checksum,
                ParserErrorCode.CORRUPT_STRUCTURE,
                "source file does not have a valid PDF header",
            )

        try:
            document = pdfium.PdfDocument(source_bytes)
        except pdfium.PdfiumError as error:
            return self._document_open_failure(actual_checksum, error)

        try:
            return self._parse_document(document, provenance)
        finally:
            self._close_document(document)

    def _parse_document(
        self,
        document: pdfium.PdfDocument,
        provenance: ExtractionProvenance,
    ) -> ParseResult:
        pages: list[PublicationPage] = []
        failures: list[ParserFailure] = []
        scanned_empty_page_found = False

        for page_index in range(len(document)):
            page_number = page_index + 1
            page: pdfium.PdfPage | None = None
            text_page: pdfium.PdfTextPage | None = None
            try:
                try:
                    page = document.get_page(page_index)
                    text_page = page.get_textpage()
                    text = self._extract_page_text(text_page)
                except (pdfium.PdfiumError, UnicodeError):
                    failures.append(
                        ParserFailure(
                            code=ParserErrorCode.DECODE_ERROR,
                            message="PDF page text could not be decoded",
                            page_number=page_number,
                        )
                    )
                    continue
                finally:
                    if text_page is not None:
                        self._close_text_page(text_page)

                assert page is not None
                if not text.strip():
                    scanned_empty_page_found = scanned_empty_page_found or self._is_scan_page(page)
                    failures.append(
                        ParserFailure(
                            code=ParserErrorCode.EMPTY_PAGE,
                            message="PDF page has no extractable body text",
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
            finally:
                if page is not None:
                    self._close_page(page)

        if not pages:
            failures.append(
                ParserFailure(
                    code=ParserErrorCode.EMPTY_DOCUMENT,
                    message="PDF document produced no page text",
                )
            )

        return ParseResult(
            provenance=provenance,
            pages=pages,
            failures=failures,
            requires_ocr=scanned_empty_page_found,
        )

    @staticmethod
    def _extract_page_text(text_page: pdfium.PdfTextPage) -> str:
        """Decode the complete page strictly and preserve PDFium's exact text."""
        return cast(str, text_page.get_text_bounded(errors="strict"))

    @staticmethod
    def _close_document(document: pdfium.PdfDocument) -> None:
        document.close()

    @staticmethod
    def _close_page(page: pdfium.PdfPage) -> None:
        page.close()

    @staticmethod
    def _close_text_page(text_page: pdfium.PdfTextPage) -> None:
        text_page.close()

    @staticmethod
    def _is_scan_page(page: pdfium.PdfPage) -> bool:
        """Identify a likely scan by a single image covering at least 80% of a page."""
        try:
            page_left, page_bottom, page_right, page_top = page.get_bbox()
            page_area = max(0.0, page_right - page_left) * max(0.0, page_top - page_bottom)
            if page_area == 0.0:
                return False

            image_type = pdfium.raw.FPDF_PAGEOBJ_IMAGE
            for image in page.get_objects(filter=[image_type]):
                left, bottom, right, top = image.get_bounds()
                width = max(0.0, min(right, page_right) - max(left, page_left))
                height = max(0.0, min(top, page_top) - max(bottom, page_bottom))
                if (width * height) / page_area >= _SCAN_IMAGE_COVERAGE_THRESHOLD:
                    return True
        except pdfium.PdfiumError:
            return False
        return False

    def _document_open_failure(
        self,
        source_checksum: Checksum,
        error: pdfium.PdfiumError,
    ) -> ParseResult:
        encrypted_codes = {
            pdfium.raw.FPDF_ERR_PASSWORD,
            pdfium.raw.FPDF_ERR_SECURITY,
        }
        if error.err_code in encrypted_codes:
            return self._failed_result(
                source_checksum,
                ParserErrorCode.ENCRYPTED,
                "encrypted PDF cannot be opened without credentials",
            )
        return self._failed_result(
            source_checksum,
            ParserErrorCode.CORRUPT_STRUCTURE,
            "PDF structure could not be loaded",
        )

    def _failed_result(
        self,
        source_checksum: Checksum,
        code: ParserErrorCode,
        message: Label,
    ) -> ParseResult:
        return ParseResult(
            provenance=self._provenance(source_checksum),
            failures=[ParserFailure(code=code, message=message)],
        )

    def _provenance(self, source_checksum: Checksum) -> ExtractionProvenance:
        return ExtractionProvenance(
            parser_name=self.name,
            parser_version=self.version,
            source_checksum=source_checksum,
        )
