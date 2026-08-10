"""Bibliographic metadata extraction interface and deterministic implementation.

The rule-based extractor is deliberately conservative. It recognizes the four
layouts recorded in docs/DATA_QUALITY_REPORT.md and keeps every resolved value
attached to the page, file name, or processing metadata that supplied it. A weaker
candidate is discarded when a stronger candidate exists; equally strong conflicting
candidates result in an explicit unresolved value instead of an arbitrary choice.
"""

import hashlib
import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final, Protocol

from defense_research_agent.domain.common import Label
from defense_research_agent.domain.metadata import (
    EVIDENCE_SOURCE_STRENGTH,
    DatePrecision,
    ExtractedAuthor,
    ExtractedMetadataValue,
    ExtractedPublicationMetadata,
    MetadataEvidence,
    MetadataEvidenceSource,
    MetadataField,
    PublicationDates,
)
from defense_research_agent.domain.provenance import ExtractionProvenance
from defense_research_agent.domain.publication import (
    PublicationPage,
    PublicationType,
    ResearchPublication,
)

METADATA_NORMALIZATION_VERSION: Final = "nfc-whitespace-v1"
"""Version of the Unicode and whitespace normalization rules below."""

RULE_BASED_METADATA_EXTRACTOR_VERSION: Final = "1.0.4"
_CONFLICT_REASON: Final = "동일한 우선순위의 메타데이터 근거가 충돌함"
_DATE_CONFLICT_REASON: Final = "동일한 우선순위의 발행일 근거가 충돌함"
_MISSING_DATE_REASON: Final = "표지에서 발행일 근거를 찾을 수 없음"
_BODY_DATE_REJECTED_REASON: Final = "표지 발행일이 없고 본문 날짜는 발행일 근거로 사용할 수 없음"
_MISSING_REASONS: Final[dict[MetadataField, str]] = {
    MetadataField.TITLE: "표지, 본문, 파일명에서 제목을 확정할 수 없음",
    MetadataField.SUBTITLE: "명시적인 부제 표기를 찾을 수 없음",
    MetadataField.ORGANIZATION: "발행 기관을 확정할 수 없음",
    MetadataField.ISSUE_NUMBER: "호 표기를 찾을 수 없음",
    MetadataField.VOLUME: "권 표기를 찾을 수 없음",
    MetadataField.DOI: "DOI 표기를 찾을 수 없음",
    MetadataField.ABSTRACT: "명시적인 초록/요약 표기를 찾을 수 없음",
    MetadataField.KEYWORDS: "명시적인 키워드 표기를 찾을 수 없음",
}

_DAY_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*년\s*"
    r"(?P<month>1[0-2]|0?[1-9])\s*월\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*일"
)
_KOREAN_MONTH_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*년\s*"
    r"(?P<month>1[0-2]|0?[1-9])\s*월(?!\s*\d)"
)
_DOTTED_MONTH_RE = re.compile(
    r"(?<![\d.])(?P<year>(?:19|20)\d{2})\s*\.\s*"
    r"(?P<month>1[0-2]|0?[1-9])\s*\.(?!\s*\d)"
)
_SEASON_RE = re.compile(
    r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*년\s*"
    r"(?P<season>봄|여름|가을|겨울)\s*(?:\((?P<issue>[^)]+)\))?"
)
_YEAR_RE = re.compile(r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*년(?!\s*(?:월|봄|여름|가을|겨울))")
_DOI_RE = re.compile(
    r"(?:https?://(?:dx\.)?doi\.org/)?(?P<doi>10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
)
_JOURNAL_ISSUE_RE = re.compile(r"\((?P<volume>\d{1,3})\s*-\s*(?P<issue>\d{1,3})\)")
_SERIAL_ISSUE_RE = re.compile(r"제\s*(?P<issue>\d+)\s*호(?:\s*\([^)]+\))?")
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_JOURNAL_AUTHOR_RE = re.compile(r"(?P<name>[가-힣]{2,5})\s*(?P<marker>\*+)(?:\s*\d+\))?")
_FOOTNOTE_RE = re.compile(r"^(?P<marker>\*+)\s*(?P<details>.+)$")
_ROLE_RE = re.compile(
    r"(?P<name>[가-힣]{2,5})\s+"
    r"(?P<role>책임연구위원|선임연구위원|전문연구원|연구위원|연구원|"
    r"수석연구원|부연구위원|객원연구원|교수|부교수|조교수|교관|학생장교)"
)
_STANDALONE_AUTHOR_RE = re.compile(r"^[가-힣]{2,5}(?:\s*[,·]\s*[가-힣]{2,5})*$")
_KEYWORD_RE = re.compile(
    r"^(?:key\s*words?|keywords|주제어|핵심어)\s*[:\uFF1A]\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_ABSTRACT_HEADING_RE = re.compile(
    r"^(?:abstract|초\s*록|요\s*약)\s*[:\uFF1A]?$",
    re.IGNORECASE,
)
_SECTION_HEADING_RE = re.compile(r"^(?:[IVXLCDM]+\.|\d+\.|[가-힣]\.)\s+")
_TRAILING_TITLE_NOTE_RE = re.compile(r"(?:[†‡]+|\d+\))+\s*$")
_LEADING_TITLE_NOTE_RE = re.compile(r"^(?:[†‡]+|\d+\))+\s*")
_DASH_SUBTITLE_RE = re.compile(
    r"^[-\u2010\u2011\u2012\u2013\u2014]\s*"
    r"(?P<subtitle>.+?)\s*[-\u2010\u2011\u2012\u2013\u2014]$"
)
_BRIEF_COVER_HEADER_RE = re.compile(
    r"^(?:배경과\s*목적|수행\s*결과|KIDA\s+Brief(?:\s+\d+)?)$",
    re.IGNORECASE,
)
_REPORT_IDENTIFIER_RE = re.compile(r"^연구\s*보고서(?:\s+[가-힣]+(?:\s*(?:19|20)\d{2}-\d+)?)?$")
_ISBN_RE = re.compile(r"^ISBN(?:\s|$)", re.IGNORECASE)
_REPORT_AUTHOR_TOKEN_RE = re.compile(r"[가-힣]{2,5}(?:\(자문\))?")
_THREE_SYLLABLE_AUTHOR_TOKEN_RE = re.compile(r"[가-힣]{3}(?:\(자문\))?")

_SEASON_MONTH: Final[dict[str, int]] = {
    "봄": 3,
    "여름": 6,
    "가을": 9,
    "겨울": 12,
}
_AFFILIATION_TERMS: Final[tuple[str, ...]] = (
    "대학교",
    "대학원",
    "사관학교",
    "연세대",
    "한국국방연구원",
    "연구소",
    "연구센터",
    "센터",
    "학과",
    "국방부",
    "합참",
    "본부",
)


def normalize_metadata_text(value: str) -> str:
    """Apply NFC, control-character separation, and whitespace collapsing.

    Incomplete Hangul jamo are preserved rather than guessed.
    """
    composed = unicodedata.normalize("NFC", value)
    separated = "".join(
        " " if character.isspace() or unicodedata.category(character) in {"Cc", "Cf"} else character
        for character in composed
    )
    return " ".join(separated.split())


def _split_title_and_subtitle(normalized_lines: Sequence[str]) -> tuple[str, str | None]:
    if len(normalized_lines) >= 2:
        dash_subtitle = _DASH_SUBTITLE_RE.fullmatch(normalized_lines[-1])
        if dash_subtitle is not None:
            return " ".join(normalized_lines[:-1]), dash_subtitle.group("subtitle").strip()

    normalized = " ".join(normalized_lines)
    parts = re.split(r"\s*[:\uFF1A]\s*", normalized, maxsplit=1)
    if len(parts) == 2 and parts[1]:
        return parts[0], parts[1]
    return normalized, None


class PublicationMetadataExtractor(ABC):
    """Interface implemented by every metadata extraction strategy."""

    @property
    @abstractmethod
    def name(self) -> Label:
        """Stable extractor name recorded in provenance."""

    @property
    @abstractmethod
    def version(self) -> Label:
        """Extractor version. Bump whenever resolved values can change."""

    @abstractmethod
    def extract(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        source_path: Path | None = None,
    ) -> ExtractedPublicationMetadata:
        """Resolve bibliographic fields from page text and optional file name.

        source_path is evidence of last resort. When a cover page and a file
        name disagree, the cover page wins and the file-name reading is dropped
        rather than merged.
        """


@dataclass(frozen=True)
class _Line:
    raw: str
    normalized: str


@dataclass(frozen=True)
class _PageView:
    page_number: int
    source: MetadataEvidenceSource
    lines: tuple[_Line, ...]


@dataclass(frozen=True)
class _Candidate:
    normalized: str
    raw_text: str
    source: MetadataEvidenceSource
    confidence: float
    page_number: int | None = None

    def evidence(self) -> MetadataEvidence:
        return MetadataEvidence(
            source=self.source,
            raw_text=self.raw_text,
            page_number=self.page_number,
        )


@dataclass(frozen=True)
class _FilenameMetadata:
    year: int | None
    author: str | None
    raw_author: str | None
    title: str | None
    raw_title: str | None
    title_is_probably_truncated: bool


@dataclass(frozen=True)
class _DateCandidate:
    published_at: date
    precision: DatePrecision
    issue_label: str | None
    raw_text: str
    source: MetadataEvidenceSource
    page_number: int


class _HashWriter(Protocol):
    def update(self, value: bytes) -> object:
        """Add bytes to the digest."""


class RuleBasedPublicationMetadataExtractor(PublicationMetadataExtractor):
    """Extract metadata from the four deterministic KIDA corpus layouts."""

    @property
    def name(self) -> str:
        """Stable concrete extractor name."""
        return "kida-rule-based-metadata"

    @property
    def version(self) -> str:
        """Rule-set version, including the normalization contract version."""
        return f"{RULE_BASED_METADATA_EXTRACTOR_VERSION}+{METADATA_NORMALIZATION_VERSION}"

    def extract(
        self,
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        source_path: Path | None = None,
    ) -> ExtractedPublicationMetadata:
        """Extract traceable metadata without reading or mutating the source file."""
        ordered_pages = sorted(pages, key=lambda page: page.page_number)
        views = tuple(self._page_view(page) for page in ordered_pages)
        filename = self._parse_filename(source_path)

        candidates: dict[MetadataField, list[_Candidate]] = {
            field: [] for field in MetadataField if field is not MetadataField.AUTHORS
        }
        self._collect_title_candidates(
            publication.publication_type,
            views,
            filename,
            candidates,
        )
        self._collect_organization_candidates(views, candidates)
        self._collect_issue_candidates(publication.publication_type, views, candidates)
        self._collect_doi_candidates(views, candidates)
        self._collect_abstract_candidates(views, candidates)
        self._collect_keyword_candidates(views, candidates)

        return ExtractedPublicationMetadata(
            publication_id=publication.publication_id,
            provenance=ExtractionProvenance(
                parser_name=self.name,
                parser_version=self.version,
                source_checksum=self._source_checksum(publication, ordered_pages, source_path),
            ),
            values=self._resolve_values(candidates),
            authors=self._extract_authors(publication.publication_type, views, filename),
            dates=self._extract_dates(publication, views, filename),
        )

    @staticmethod
    def _page_view(page: PublicationPage) -> _PageView:
        lines: list[_Line] = []
        for raw in page.text.splitlines():
            normalized = normalize_metadata_text(raw)
            if normalized:
                lines.append(_Line(raw=raw, normalized=normalized))
        source = (
            MetadataEvidenceSource.COVER_PAGE
            if page.page_number == 1
            else MetadataEvidenceSource.BODY
        )
        return _PageView(page_number=page.page_number, source=source, lines=tuple(lines))

    @staticmethod
    def _parse_filename(source_path: Path | None) -> _FilenameMetadata:
        if source_path is None:
            return _FilenameMetadata(None, None, None, None, None, False)
        parts = source_path.stem.split("_", maxsplit=2)
        if len(parts) != 3 or not parts[0].isdigit():
            return _FilenameMetadata(None, None, None, None, None, False)
        parsed_year = int(parts[0])
        year: int | None = parsed_year if 1900 <= parsed_year <= 2100 else None
        raw_author = parts[1].strip() or None
        raw_title = parts[2].strip() or None
        author = normalize_metadata_text(raw_author) if raw_author is not None else None
        title = normalize_metadata_text(raw_title) if raw_title is not None else None
        truncated = title is not None and (
            len(source_path.name.encode("utf-8")) >= 240 or _ends_with_incomplete_hangul_jamo(title)
        )
        return _FilenameMetadata(year, author, raw_author, title, raw_title, truncated)

    def _collect_title_candidates(
        self,
        publication_type: PublicationType,
        views: Sequence[_PageView],
        filename: _FilenameMetadata,
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> None:
        cover = next(
            (view for view in views if view.source is MetadataEvidenceSource.COVER_PAGE),
            None,
        )
        if cover is not None:
            raw_title = self._title_block(publication_type, cover, filename.author)
            if raw_title is not None:
                self._append_title_parts(
                    raw_title,
                    cover.source,
                    cover.page_number,
                    0.96,
                    candidates,
                )

        if filename.title is not None and filename.raw_title is not None:
            confidence = 0.18 if filename.title_is_probably_truncated else 0.35
            self._append_title_parts(
                filename.raw_title,
                MetadataEvidenceSource.FILENAME,
                None,
                confidence,
                candidates,
            )

    @staticmethod
    def _append_title_parts(
        raw_title: str,
        source: MetadataEvidenceSource,
        page_number: int | None,
        confidence: float,
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> None:
        normalized_lines: list[str] = []
        for raw_line in raw_title.splitlines():
            normalized_line = normalize_metadata_text(raw_line)
            normalized_line = _TRAILING_TITLE_NOTE_RE.sub("", normalized_line).strip()
            if normalized_line:
                normalized_lines.append(normalized_line)
        if not normalized_lines:
            return
        title, subtitle = _split_title_and_subtitle(normalized_lines)
        candidates[MetadataField.TITLE].append(
            _Candidate(title, raw_title.strip(), source, confidence, page_number)
        )
        if subtitle is not None:
            candidates[MetadataField.SUBTITLE].append(
                _Candidate(subtitle, raw_title.strip(), source, confidence, page_number)
            )

    def _title_block(
        self,
        publication_type: PublicationType,
        view: _PageView,
        filename_author: str | None,
    ) -> str | None:
        if not view.lines:
            return None
        if publication_type is PublicationType.DEFENSE_POLICY_RESEARCH:
            return self._journal_title_block(view.lines)
        if publication_type in {PublicationType.DEFENSE_FORUM, PublicationType.KIDA_BRIEF}:
            return self._periodical_title_block(view.lines)
        if publication_type is PublicationType.RESEARCH_REPORT:
            return self._report_title_block(view.lines, filename_author)
        return self._generic_title_block(view.lines)

    @staticmethod
    def _journal_title_block(lines: Sequence[_Line]) -> str | None:
        start = 0
        for index, line in enumerate(lines[:8]):
            if (
                line.normalized.startswith("국방정책연구")
                or "doi.org/" in line.normalized.casefold()
                or line.normalized.upper().startswith("ISSN ")
            ):
                start = index + 1
        title_lines: list[str] = []
        for line in lines[start:]:
            if _is_journal_author_line(line.normalized):
                break
            if _is_section_or_abstract_heading(line.normalized):
                break
            title_lines.append(line.raw)
        return "\n".join(title_lines) if title_lines else None

    @staticmethod
    def _periodical_title_block(lines: Sequence[_Line]) -> str | None:
        start = 0
        for index, line in enumerate(lines[:10]):
            if _is_periodical_header(line.normalized):
                start = index + 1
        author_index = _find_periodical_author_index(lines, start)
        if author_index is None or author_index <= start:
            return None
        title_lines: list[_Line] = []
        for line in lines[start:author_index]:
            if _is_periodical_header(line.normalized):
                continue
            if title_lines and line.normalized.casefold() == title_lines[-1].normalized.casefold():
                continue
            title_lines.append(line)
        return "\n".join(line.raw for line in title_lines) if title_lines else None

    @staticmethod
    def _report_title_block(
        lines: Sequence[_Line],
        filename_author: str | None,
    ) -> str | None:
        title_lines: list[str] = []
        author_index = _find_report_author_index(lines, filename_author)
        for index, line in enumerate(lines[:12]):
            if _is_report_metadata_line(line.normalized):
                if title_lines:
                    break
                continue
            if author_index == index:
                if title_lines:
                    break
                continue
            title_lines.append(line.raw)
        return "\n".join(title_lines) if title_lines else None

    @staticmethod
    def _generic_title_block(lines: Sequence[_Line]) -> str | None:
        for line in lines[:8]:
            if (
                not _is_periodical_header(line.normalized)
                and not _line_has_publication_date(line.normalized)
                and not _looks_like_affiliation(line.normalized)
                and not _EMAIL_RE.search(line.normalized)
            ):
                return line.raw
        return None

    @staticmethod
    def _collect_organization_candidates(
        views: Sequence[_PageView],
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> None:
        for view in views:
            for line in view.lines[:15]:
                explicit = re.match(
                    r"^(?:발행처|발행기관)\s*[:\uFF1A]?\s*(.+)$",
                    line.normalized,
                )
                organization: str | None = None
                if explicit is not None:
                    organization = explicit.group(1)
                elif line.normalized in {
                    "한국국방연구원",
                    "Korea Institute for Defense Analyses",
                }:
                    organization = line.normalized
                if organization:
                    candidates[MetadataField.ORGANIZATION].append(
                        _page_candidate(organization, line.raw, view, 0.96)
                    )

    @staticmethod
    def _collect_issue_candidates(
        publication_type: PublicationType,
        views: Sequence[_PageView],
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> None:
        for view in views:
            for line in view.lines[:10]:
                if publication_type is PublicationType.DEFENSE_POLICY_RESEARCH:
                    match = _JOURNAL_ISSUE_RE.search(line.normalized)
                    if match is not None:
                        candidates[MetadataField.VOLUME].append(
                            _page_candidate(match.group("volume"), line.raw, view, 0.98)
                        )
                        candidates[MetadataField.ISSUE_NUMBER].append(
                            _page_candidate(match.group("issue"), line.raw, view, 0.98)
                        )
                        break
                elif publication_type is PublicationType.DEFENSE_FORUM:
                    match = _SERIAL_ISSUE_RE.search(line.normalized)
                    if match is not None:
                        candidates[MetadataField.ISSUE_NUMBER].append(
                            _page_candidate(match.group("issue"), line.raw, view, 0.96)
                        )
                        break

    @staticmethod
    def _collect_doi_candidates(
        views: Sequence[_PageView],
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> None:
        for view in views:
            for line in view.lines:
                for match in _DOI_RE.finditer(line.normalized):
                    doi = match.group("doi").rstrip(".,;")
                    candidates[MetadataField.DOI].append(_page_candidate(doi, line.raw, view, 0.99))

    @staticmethod
    def _collect_abstract_candidates(
        views: Sequence[_PageView],
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> None:
        for view in views:
            abstract_block = _labeled_abstract(view.lines)
            if abstract_block is not None:
                raw_abstract, normalized_abstract = abstract_block
                candidates[MetadataField.ABSTRACT].append(
                    _page_candidate(normalized_abstract, raw_abstract, view, 0.9)
                )

    @staticmethod
    def _collect_keyword_candidates(
        views: Sequence[_PageView],
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> None:
        for view in views:
            keyword_block = _keyword_block(view.lines)
            if keyword_block is None:
                continue
            raw_text, keywords = keyword_block
            for keyword in keywords:
                candidates[MetadataField.KEYWORDS].append(
                    _page_candidate(keyword, raw_text, view, 0.94)
                )

    @staticmethod
    def _resolve_values(
        candidates: dict[MetadataField, list[_Candidate]],
    ) -> list[ExtractedMetadataValue]:
        values: list[ExtractedMetadataValue] = []
        single_fields = (
            MetadataField.TITLE,
            MetadataField.SUBTITLE,
            MetadataField.ORGANIZATION,
            MetadataField.ISSUE_NUMBER,
            MetadataField.VOLUME,
            MetadataField.DOI,
            MetadataField.ABSTRACT,
        )
        for field in single_fields:
            selected, failure_reason = _select_candidate(candidates[field])
            if selected is None:
                values.append(
                    ExtractedMetadataValue(
                        field=field,
                        failure_reason=failure_reason or _MISSING_REASONS[field],
                    )
                )
            else:
                values.append(
                    ExtractedMetadataValue(
                        field=field,
                        normalized=selected.normalized,
                        evidence=selected.evidence(),
                        confidence=selected.confidence,
                    )
                )

        keyword_candidates = _strongest_candidates(candidates[MetadataField.KEYWORDS])
        if not keyword_candidates:
            values.append(
                ExtractedMetadataValue(
                    field=MetadataField.KEYWORDS,
                    failure_reason=_MISSING_REASONS[MetadataField.KEYWORDS],
                )
            )
        else:
            unique: dict[str, _Candidate] = {}
            for candidate in keyword_candidates:
                unique.setdefault(candidate.normalized.casefold(), candidate)
            for ordinal, candidate in enumerate(unique.values()):
                values.append(
                    ExtractedMetadataValue(
                        field=MetadataField.KEYWORDS,
                        ordinal=ordinal,
                        normalized=candidate.normalized,
                        evidence=candidate.evidence(),
                        confidence=candidate.confidence,
                    )
                )
        return values

    def _extract_authors(
        self,
        publication_type: PublicationType,
        views: Sequence[_PageView],
        filename: _FilenameMetadata,
    ) -> list[ExtractedAuthor]:
        cover_groups: list[list[ExtractedAuthor]] = []
        body_groups: list[list[ExtractedAuthor]] = []
        for view in views:
            if publication_type is PublicationType.DEFENSE_POLICY_RESEARCH:
                authors = self._journal_authors(view)
            elif publication_type is PublicationType.RESEARCH_REPORT:
                authors = self._report_authors(view, filename.author)
                if not authors:
                    authors = self._general_authors(view)
            else:
                authors = self._general_authors(view)
            if not authors:
                continue
            if view.source is MetadataEvidenceSource.COVER_PAGE:
                cover_groups.append(authors)
            else:
                body_groups.append(authors)
        if cover_groups:
            return cover_groups[0]
        if body_groups:
            return body_groups[0]
        if filename.author is not None:
            return [
                ExtractedAuthor(
                    ordinal=0,
                    name=filename.author,
                    is_primary=True,
                    evidence=MetadataEvidence(
                        source=MetadataEvidenceSource.FILENAME,
                        raw_text=filename.raw_author or filename.author,
                    ),
                    confidence=0.3,
                )
            ]
        return [ExtractedAuthor(ordinal=0, failure_reason="저자 근거를 찾을 수 없음")]

    @staticmethod
    def _journal_authors(view: _PageView) -> list[ExtractedAuthor]:
        author_line: _Line | None = None
        author_matches: list[re.Match[str]] = []
        for line in view.lines:
            matches = list(_JOURNAL_AUTHOR_RE.finditer(line.normalized))
            if matches and _journal_author_matches_fill_line(line.normalized, matches):
                author_line = line
                author_matches = matches
                break
        if author_line is None:
            return []

        footnotes: dict[str, _Line] = {}
        for line in view.lines:
            match = _FOOTNOTE_RE.match(line.normalized)
            if match is not None:
                footnotes[match.group("marker")] = line

        authors: list[ExtractedAuthor] = []
        for ordinal, match in enumerate(author_matches):
            marker = match.group("marker")
            footnote = footnotes.get(marker)
            role: str | None = None
            affiliation: str | None = None
            email: str | None = None
            raw_evidence = author_line.raw
            if footnote is not None:
                role, affiliation, email = _parse_author_details(footnote.normalized)
                raw_evidence = f"{author_line.raw}\n{footnote.raw}"
            authors.append(
                ExtractedAuthor(
                    ordinal=ordinal,
                    name=match.group("name"),
                    role=role,
                    affiliation=affiliation,
                    email=email,
                    is_primary=ordinal == 0,
                    evidence=MetadataEvidence(
                        source=view.source,
                        raw_text=raw_evidence,
                        page_number=view.page_number,
                    ),
                    confidence=0.98 if footnote is not None else 0.88,
                )
            )
        return authors

    @staticmethod
    def _report_authors(
        view: _PageView,
        filename_author: str | None,
    ) -> list[ExtractedAuthor]:
        if view.source is MetadataEvidenceSource.BODY:
            return []
        author_index = _find_report_author_index(view.lines, filename_author)
        if author_index is None:
            return []
        line = view.lines[author_index]
        raw_tokens = [token for token in re.split(r"[\s,·]+", line.normalized) if token]
        if not raw_tokens or not all(
            _REPORT_AUTHOR_TOKEN_RE.fullmatch(token) for token in raw_tokens
        ):
            return []
        names = [token.removesuffix("(자문)") for token in raw_tokens]
        return [
            ExtractedAuthor(
                ordinal=ordinal,
                name=name,
                is_primary=ordinal == 0,
                evidence=MetadataEvidence(
                    source=view.source,
                    raw_text=line.raw,
                    page_number=view.page_number,
                ),
                confidence=0.9,
            )
            for ordinal, name in enumerate(names)
        ]

    @staticmethod
    def _general_authors(view: _PageView) -> list[ExtractedAuthor]:
        for index, line in enumerate(view.lines[:20]):
            role_matches = list(_ROLE_RE.finditer(line.normalized))
            if role_matches:
                affiliation, emails, supporting_raw = _nearby_author_details(view.lines, index)
                return [
                    ExtractedAuthor(
                        ordinal=ordinal,
                        name=match.group("name"),
                        role=match.group("role"),
                        affiliation=affiliation,
                        email=emails[ordinal] if ordinal < len(emails) else None,
                        is_primary=ordinal == 0,
                        evidence=MetadataEvidence(
                            source=view.source,
                            raw_text="\n".join([line.raw, *supporting_raw]),
                            page_number=view.page_number,
                        ),
                        confidence=0.94,
                    )
                    for ordinal, match in enumerate(role_matches)
                ]

        if view.source is MetadataEvidenceSource.BODY:
            return []
        author_index = _find_periodical_author_index(view.lines, 0)
        if author_index is None:
            return []
        line = view.lines[author_index]
        names = [normalize_metadata_text(name) for name in re.split(r"\s*[,·]\s*", line.normalized)]
        affiliation, emails, supporting_raw = _nearby_author_details(view.lines, author_index)
        return [
            ExtractedAuthor(
                ordinal=ordinal,
                name=name,
                affiliation=affiliation,
                email=emails[ordinal] if ordinal < len(emails) else None,
                is_primary=ordinal == 0,
                evidence=MetadataEvidence(
                    source=view.source,
                    raw_text="\n".join([line.raw, *supporting_raw]),
                    page_number=view.page_number,
                ),
                confidence=0.9,
            )
            for ordinal, name in enumerate(names)
        ]

    @staticmethod
    def _extract_dates(
        publication: ResearchPublication,
        views: Sequence[_PageView],
        filename: _FilenameMetadata,
    ) -> PublicationDates:
        date_candidates = [
            candidate
            for view in views
            for candidate in _date_candidates(publication.publication_type, view)
        ]
        cover_candidates = [
            candidate
            for candidate in date_candidates
            if candidate.source is MetadataEvidenceSource.COVER_PAGE
        ]
        selected_date, failure_reason = _select_date_candidate(cover_candidates)
        processed_at = _processed_at(publication)
        if selected_date is None:
            if failure_reason is None:
                has_body_date = any(
                    candidate.source is MetadataEvidenceSource.BODY for candidate in date_candidates
                )
                failure_reason = (
                    _BODY_DATE_REJECTED_REASON if has_body_date else _MISSING_DATE_REASON
                )
            return PublicationDates(
                filename_year=filename.year,
                processed_at=processed_at,
                failure_reason=failure_reason,
            )
        issue_label = selected_date.issue_label
        if issue_label is None and publication.publication_type in {
            PublicationType.DEFENSE_POLICY_RESEARCH,
            PublicationType.DEFENSE_FORUM,
        }:
            issue_label = _issue_label(publication.publication_type, views)
        return PublicationDates(
            filename_year=filename.year,
            published_at=selected_date.published_at,
            published_precision=selected_date.precision,
            issue_label=issue_label,
            processed_at=processed_at,
            date_evidence=MetadataEvidence(
                source=selected_date.source,
                raw_text=selected_date.raw_text,
                page_number=selected_date.page_number,
            ),
        )

    @staticmethod
    def _source_checksum(
        publication: ResearchPublication,
        pages: Sequence[PublicationPage],
        source_path: Path | None,
    ) -> str:
        if publication.checksum is not None:
            return publication.checksum
        page_checksums = {page.provenance.source_checksum for page in pages}
        if len(page_checksums) == 1:
            return next(iter(page_checksums))

        digest = hashlib.sha256()
        _hash_part(digest, publication.publication_id)
        _hash_part(digest, publication.publication_type.value)
        _hash_part(digest, source_path.name if source_path is not None else "")
        for page in pages:
            _hash_part(digest, str(page.page_number))
            _hash_part(digest, page.text)
            _hash_part(digest, page.provenance.source_checksum)
        return digest.hexdigest()


def _ends_with_incomplete_hangul_jamo(value: str) -> bool:
    final = value[-1]
    codepoint = ord(final)
    return (
        0x1100 <= codepoint <= 0x11FF
        or 0x3130 <= codepoint <= 0x318F
        or 0xA960 <= codepoint <= 0xA97F
        or 0xD7B0 <= codepoint <= 0xD7FF
    )


def _page_candidate(
    normalized: str,
    raw_text: str,
    view: _PageView,
    confidence: float,
) -> _Candidate:
    return _Candidate(
        normalized=normalize_metadata_text(normalized),
        raw_text=raw_text.strip(),
        source=view.source,
        confidence=confidence,
        page_number=view.page_number,
    )


def _strongest_candidates(candidates: Sequence[_Candidate]) -> list[_Candidate]:
    if not candidates:
        return []
    strength = max(EVIDENCE_SOURCE_STRENGTH[candidate.source] for candidate in candidates)
    strongest = [
        candidate
        for candidate in candidates
        if EVIDENCE_SOURCE_STRENGTH[candidate.source] == strength
    ]
    return strongest


def _select_candidate(candidates: Sequence[_Candidate]) -> tuple[_Candidate | None, str | None]:
    strongest = _strongest_candidates(candidates)
    if not strongest:
        return None, None
    normalized_values = {candidate.normalized.casefold() for candidate in strongest}
    if len(normalized_values) > 1:
        return None, _CONFLICT_REASON
    return strongest[0], None


def _is_journal_author_line(value: str) -> bool:
    matches = list(_JOURNAL_AUTHOR_RE.finditer(value))
    return bool(matches) and _journal_author_matches_fill_line(value, matches)


def _journal_author_matches_fill_line(value: str, matches: Sequence[re.Match[str]]) -> bool:
    residue = value
    for match in reversed(matches):
        residue = residue[: match.start()] + residue[match.end() :]
    residue = _LEADING_TITLE_NOTE_RE.sub("", residue)
    return not residue.strip(" ,·")


def _is_section_or_abstract_heading(value: str) -> bool:
    return bool(
        _ABSTRACT_HEADING_RE.match(value)
        or _SECTION_HEADING_RE.match(value)
        or value.startswith("목 차")
        or value == "목차"
    )


def _is_periodical_header(value: str) -> bool:
    lowered = value.casefold()
    return bool(
        _SERIAL_ISSUE_RE.search(value)
        or _DAY_RE.search(value)
        or _BRIEF_COVER_HEADER_RE.fullmatch(value)
        or value.startswith(("발행처", "발행인", "편집인"))
        or lowered.startswith(("pissn", "eissn", "issn"))
        or "doi.org/" in lowered
    )


def _is_report_metadata_line(value: str) -> bool:
    return bool(
        _REPORT_IDENTIFIER_RE.fullmatch(value)
        or _ISBN_RE.match(value)
        or _line_has_publication_date(value)
        or _is_publisher_line(value)
    )


def _is_report_author_line(value: str, filename_author: str | None) -> bool:
    tokens = [token for token in re.split(r"[\s,·]+", value) if token]
    if filename_author is not None and tokens and tokens[0] == filename_author:
        return True
    if ("," in value or "·" in value) and len(tokens) >= 2:
        return all(_REPORT_AUTHOR_TOKEN_RE.fullmatch(token) for token in tokens)
    return len(tokens) >= 3 and all(
        _THREE_SYLLABLE_AUTHOR_TOKEN_RE.fullmatch(token) for token in tokens
    )


def _find_report_author_index(
    lines: Sequence[_Line],
    filename_author: str | None,
) -> int | None:
    title_seen = False
    report_identifier_seen = False
    limit = min(len(lines), 12)
    for index, line in enumerate(lines[:limit]):
        value = line.normalized
        if _REPORT_IDENTIFIER_RE.fullmatch(value):
            report_identifier_seen = True
        if _is_report_metadata_line(value):
            continue
        if title_seen and _is_report_author_line(value, filename_author):
            return index
        trailing_lines = lines[index + 1 : limit]
        if (
            title_seen
            and report_identifier_seen
            and _THREE_SYLLABLE_AUTHOR_TOKEN_RE.fullmatch(value)
            and all(_is_report_metadata_line(trailing.normalized) for trailing in trailing_lines)
        ):
            return index
        title_seen = True
    return None


def _find_periodical_author_index(lines: Sequence[_Line], start: int) -> int | None:
    for index in range(start, min(len(lines), 20)):
        value = lines[index].normalized
        if _ROLE_RE.search(value):
            return index
        if not _STANDALONE_AUTHOR_RE.fullmatch(value):
            continue
        nearby = lines[index + 1 : index + 4]
        if any(
            _looks_like_affiliation(line.normalized) or _EMAIL_RE.search(line.normalized)
            for line in nearby
        ):
            return index
    return None


def _line_has_publication_date(value: str) -> bool:
    return bool(
        _DAY_RE.search(value)
        or _DOTTED_MONTH_RE.search(value)
        or _KOREAN_MONTH_RE.search(value)
        or _SEASON_RE.search(value)
    )


def _is_publisher_line(value: str) -> bool:
    return value.startswith(("발행처", "발행기관")) or value in {
        "한국국방연구원",
        "Korea Institute for Defense Analyses",
    }


def _looks_like_affiliation(value: str) -> bool:
    return any(term in value for term in _AFFILIATION_TERMS)


def _labeled_abstract(lines: Sequence[_Line]) -> tuple[str, str] | None:
    for heading_index, line in enumerate(lines):
        if _ABSTRACT_HEADING_RE.fullmatch(line.normalized) is None:
            continue
        content_start = heading_index + 1
        if line.normalized.casefold().startswith("abstract"):
            content_start = _english_abstract_content_start(lines, content_start)
        content: list[str] = []
        for candidate in lines[content_start:]:
            if _KEYWORD_RE.match(candidate.normalized):
                break
            if content and _SECTION_HEADING_RE.match(candidate.normalized):
                break
            content.append(candidate.raw)
        raw_text = "\n".join(content)
        normalized = normalize_metadata_text(raw_text)
        if normalized:
            return raw_text, normalized
    return None


def _english_abstract_content_start(lines: Sequence[_Line], start: int) -> int:
    for index in range(start, min(len(lines), start + 5)):
        raw = lines[index].raw
        normalized = lines[index].normalized
        if raw[:1].isspace() or len(normalized.split()) >= 12:
            return index
    return min(start + 1, len(lines))


def _keyword_block(lines: Sequence[_Line]) -> tuple[str, list[str]] | None:
    for index, line in enumerate(lines):
        match = _KEYWORD_RE.match(line.normalized)
        if match is None:
            continue
        raw_lines = [line.raw]
        value_lines = [match.group("value")]
        for continuation in lines[index + 1 :]:
            if (
                continuation.normalized.startswith(("*", "†", "‡"))
                or _SECTION_HEADING_RE.match(continuation.normalized)
                or _ABSTRACT_HEADING_RE.match(continuation.normalized)
                or _is_periodical_header(continuation.normalized)
            ):
                break
            value_lines.append(continuation.normalized)
            raw_lines.append(continuation.raw)
        value = normalize_metadata_text(" ".join(value_lines))
        keywords = [
            normalize_metadata_text(keyword)
            for keyword in re.split(r"\s*[,;\uFF1B]\s*", value)
            if normalize_metadata_text(keyword)
        ]
        if keywords:
            return "\n".join(raw_lines), keywords
    return None


def _parse_author_details(value: str) -> tuple[str | None, str | None, str | None]:
    details_match = _FOOTNOTE_RE.match(value)
    details = details_match.group("details") if details_match is not None else value
    email_match = _EMAIL_RE.search(details)
    email = email_match.group(0) if email_match is not None else None
    if email_match is not None:
        details = details.replace(email_match.group(0), "")
    roles: list[str] = []
    affiliations: list[str] = []
    for segment in re.split(r"\s*,\s*", details):
        normalized = normalize_metadata_text(segment).strip(" ,")
        if not normalized:
            continue
        if _looks_like_affiliation(normalized):
            affiliations.append(normalized)
        else:
            roles.append(normalized)
    role = ", ".join(roles) or None
    affiliation = ", ".join(affiliations) or None
    return role, affiliation, email


def _nearby_author_details(
    lines: Sequence[_Line],
    author_index: int,
) -> tuple[str | None, list[str], list[str]]:
    affiliation: str | None = None
    emails: list[str] = []
    supporting_raw: list[str] = []
    for line in lines[author_index + 1 : author_index + 5]:
        line_emails = _EMAIL_RE.findall(line.normalized)
        if line_emails:
            emails.extend(line_emails)
            supporting_raw.append(line.raw)
            continue
        if affiliation is None and _looks_like_affiliation(line.normalized):
            affiliation = line.normalized
            supporting_raw.append(line.raw)
    return affiliation, emails, supporting_raw


def _date_candidates(
    publication_type: PublicationType,
    view: _PageView,
) -> list[_DateCandidate]:
    match_order: tuple[re.Pattern[str], ...]
    if publication_type is PublicationType.DEFENSE_POLICY_RESEARCH:
        match_order = (_SEASON_RE, _DAY_RE, _DOTTED_MONTH_RE, _KOREAN_MONTH_RE)
    elif publication_type is PublicationType.DEFENSE_FORUM:
        match_order = (_DAY_RE, _KOREAN_MONTH_RE, _DOTTED_MONTH_RE, _SEASON_RE)
    else:
        match_order = (_DAY_RE, _DOTTED_MONTH_RE, _KOREAN_MONTH_RE, _SEASON_RE)

    for pattern in match_order:
        candidates: list[_DateCandidate] = []
        for line in view.lines[:30]:
            candidates.extend(
                _date_from_match(pattern, match, line.raw, view)
                for match in pattern.finditer(line.normalized)
            )
        if candidates:
            return candidates

    candidates = []
    for line in view.lines[:12]:
        if "사업" in line.normalized or "연구" in line.normalized:
            continue
        match = _YEAR_RE.search(line.normalized)
        if match is not None and (line.normalized == match.group(0) or "발행" in line.normalized):
            candidates.append(
                _DateCandidate(
                    published_at=date(int(match.group("year")), 1, 1),
                    precision=DatePrecision.YEAR,
                    issue_label=None,
                    raw_text=line.raw,
                    source=view.source,
                    page_number=view.page_number,
                )
            )
    return candidates


def _date_from_match(
    pattern: re.Pattern[str],
    match: re.Match[str],
    raw_text: str,
    view: _PageView,
) -> _DateCandidate:
    year = int(match.group("year"))
    issue_label: str | None = None
    if pattern is _DAY_RE:
        published_at = date(year, int(match.group("month")), int(match.group("day")))
        precision = DatePrecision.DAY
    elif pattern in {_DOTTED_MONTH_RE, _KOREAN_MONTH_RE}:
        published_at = date(year, int(match.group("month")), 1)
        precision = DatePrecision.MONTH
    else:
        season = match.group("season")
        published_at = date(year, _SEASON_MONTH[season], 1)
        precision = DatePrecision.SEASON
        issue_label = normalize_metadata_text(match.group(0))
    return _DateCandidate(
        published_at=published_at,
        precision=precision,
        issue_label=issue_label,
        raw_text=raw_text,
        source=view.source,
        page_number=view.page_number,
    )


def _select_date_candidate(
    candidates: Sequence[_DateCandidate],
) -> tuple[_DateCandidate | None, str | None]:
    if not candidates:
        return None, None
    strength = max(EVIDENCE_SOURCE_STRENGTH[candidate.source] for candidate in candidates)
    strongest = [
        candidate
        for candidate in candidates
        if EVIDENCE_SOURCE_STRENGTH[candidate.source] == strength
    ]
    values = {(candidate.published_at, candidate.precision) for candidate in strongest}
    if len(values) > 1:
        return None, _DATE_CONFLICT_REASON
    return min(strongest, key=lambda candidate: candidate.page_number), None


def _issue_label(
    publication_type: PublicationType,
    views: Sequence[_PageView],
) -> str | None:
    for view in views:
        for line in view.lines[:10]:
            pattern = (
                _JOURNAL_ISSUE_RE
                if publication_type is PublicationType.DEFENSE_POLICY_RESEARCH
                else _SERIAL_ISSUE_RE
            )
            match = pattern.search(line.normalized)
            if match is not None:
                return match.group(0)
    return None


def _processed_at(publication: ResearchPublication) -> datetime | None:
    if publication.created_at is not None:
        return publication.created_at
    raw_value = publication.raw_metadata.get("processed_date")
    if not isinstance(raw_value, str):
        return None
    try:
        return datetime.fromisoformat(raw_value)
    except ValueError:
        return None


def _hash_part(digest: _HashWriter, value: str) -> None:
    encoded = value.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
