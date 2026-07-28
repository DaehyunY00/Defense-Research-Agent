"""Read-only collection and normalization pipeline for the local KIDA dataset."""

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import cast
from unicodedata import normalize

from pydantic import JsonValue

from defense_research_agent.data.readers import (
    JsonPublicationReader,
    PdfPublicationReader,
    PublicationReader,
    PublicationSource,
    SkipSourceFile,
    SourceFileKind,
)
from defense_research_agent.domain import (
    IngestionFailure,
    IngestionReport,
    JsonObject,
    PublicationType,
    ResearchPublication,
)
from defense_research_agent.services.publication_type import classify_publication_type

_SYSTEM_FILENAMES = frozenset({".DS_Store"})
_MISSING_FIELDS = (
    "title",
    "subtitle",
    "authors",
    "organization",
    "publication_date",
    "issue_number",
    "volume",
    "abstract",
    "keywords",
    "language",
    "source_url",
    "local_path",
    "content",
    "created_at",
    "checksum",
)
_FILENAME_PATTERN = re.compile(r"^(?P<year>\d{4})_(?P<author>[^_]+)_(?P<title>.+)$")

type SourceLinkKey = tuple[PublicationType, str]


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    """Normalized publications and the persisted run report."""

    publications: tuple[ResearchPublication, ...]
    report: IngestionReport
    report_path: Path


@dataclass(frozen=True, slots=True)
class _FilenameFields:
    year: int | None
    author: str | None
    title: str | None


class IngestionService:
    """Coordinate readers, deterministic merging, and artifact writes."""

    def __init__(self, readers: Sequence[PublicationReader] | None = None) -> None:
        self._readers = tuple(readers or (JsonPublicationReader(), PdfPublicationReader()))

    def ingest(
        self,
        input_dir: Path,
        output_dir: Path,
        report_path: Path | None = None,
    ) -> IngestionOutcome:
        """Ingest a directory recursively while recording individual file failures."""
        input_root = input_dir.resolve()
        output_root = output_dir.resolve()
        resolved_report_path = (
            report_path.resolve()
            if report_path is not None
            else output_root.parent / "reports" / "ingestion_report.json"
        )
        self._validate_paths(input_root, output_root, resolved_report_path)

        all_files = sorted(
            (path for path in input_root.rglob("*") if path.is_file()),
            key=lambda path: _path_sort_key(path.relative_to(input_root)),
        )
        sources: list[PublicationSource] = []
        failures: list[IngestionFailure] = []
        skipped_count = 0

        for path in all_files:
            relative_path = path.relative_to(input_root)
            if path.name in _SYSTEM_FILENAMES:
                skipped_count += 1
                continue

            reader = self._reader_for(path)
            if reader is None:
                failures.append(
                    IngestionFailure(
                        path=relative_path.as_posix(),
                        error_type="UnsupportedFileFormat",
                        reason=f"no reader for extension {path.suffix or '<none>'}",
                    )
                )
                continue

            try:
                sources.append(reader.read(path, input_root))
            except SkipSourceFile:
                skipped_count += 1
            except Exception as error:
                failures.append(
                    IngestionFailure(
                        path=relative_path.as_posix(),
                        reader=reader.name,
                        error_type=type(error).__name__,
                        reason=str(error),
                    )
                )

        publications, duplicate_count, duplicate_group_count = self._normalize_sources(
            input_root,
            sources,
        )
        publications_path = output_root / "publications.jsonl"
        report = IngestionReport(
            input_path=input_dir.as_posix(),
            publications_path=output_dir.joinpath("publications.jsonl").as_posix(),
            total_file_count=len(all_files),
            success_count=len(sources),
            failure_count=len(failures),
            skipped_count=skipped_count,
            publication_count=len(publications),
            publication_type_counts=dict(
                sorted(
                    Counter(
                        publication.publication_type.value for publication in publications
                    ).items()
                )
            ),
            suspected_duplicate_count=duplicate_count,
            suspected_duplicate_group_count=duplicate_group_count,
            missing_field_counts=self._missing_field_counts(publications),
            failures=failures,
        )

        self._write_publications(publications_path, publications)
        self._write_report(resolved_report_path, report)
        return IngestionOutcome(
            publications=tuple(publications),
            report=report,
            report_path=resolved_report_path,
        )

    def _reader_for(self, path: Path) -> PublicationReader | None:
        return next((reader for reader in self._readers if reader.supports(path)), None)

    @staticmethod
    def _validate_paths(input_root: Path, output_root: Path, report_path: Path) -> None:
        if not input_root.is_dir():
            raise ValueError(f"input path is not a directory: {input_root}")
        if output_root == input_root or output_root.is_relative_to(input_root):
            raise ValueError("output directory must be outside the read-only input directory")
        if report_path == input_root or report_path.is_relative_to(input_root):
            raise ValueError("report path must be outside the read-only input directory")

    def _normalize_sources(
        self,
        input_root: Path,
        sources: Sequence[PublicationSource],
    ) -> tuple[list[ResearchPublication], int, int]:
        pdf_sources = sorted(
            (source for source in sources if source.kind is SourceFileKind.PDF),
            key=lambda source: _path_sort_key(source.relative_path),
        )
        json_sources = sorted(
            (source for source in sources if source.kind is SourceFileKind.DOCUMENT_JSON),
            key=lambda source: _path_sort_key(source.relative_path),
        )

        json_by_key: dict[SourceLinkKey, list[PublicationSource]] = defaultdict(list)
        for source in json_sources:
            json_by_key[_source_link_key(source)].append(source)

        json_duplicate_groups = [group for group in json_by_key.values() if len(group) > 1]
        duplicate_count = sum(len(group) - 1 for group in json_duplicate_groups)
        duplicate_group_count = len(json_duplicate_groups)

        pdf_identity_groups: dict[tuple[PublicationType, str], list[PublicationSource]] = (
            defaultdict(list)
        )
        for source in pdf_sources:
            source_type = classify_publication_type(
                source.source_path,
                source.raw_metadata,
                source.content,
            )
            pdf_identity_groups[(source_type, source.checksum)].append(source)
        pdf_duplicate_groups = [group for group in pdf_identity_groups.values() if len(group) > 1]
        duplicate_count += sum(len(group) - 1 for group in pdf_duplicate_groups)
        duplicate_group_count += len(pdf_duplicate_groups)

        publications_by_id: dict[str, ResearchPublication] = {}
        consumed_json_keys: set[SourceLinkKey] = set()
        for pdf_source in pdf_sources:
            key = _source_link_key(pdf_source)
            linked_json = json_by_key.get(key, [])
            consumed_json_keys.add(key)
            publication = self._publication_from_pdf(
                input_root,
                pdf_source,
                linked_json,
            )
            publications_by_id.setdefault(publication.publication_id, publication)

        for key, source_group in sorted(
            json_by_key.items(),
            key=lambda item: (item[0][0].value, item[0][1]),
        ):
            if key in consumed_json_keys:
                continue
            publication = self._publication_from_json_only(input_root, source_group)
            publications_by_id.setdefault(publication.publication_id, publication)

        publications = sorted(
            publications_by_id.values(),
            key=lambda publication: (
                publication.publication_type.value,
                publication.local_path or "",
                publication.publication_id,
            ),
        )
        return publications, duplicate_count, duplicate_group_count

    def _publication_from_pdf(
        self,
        input_root: Path,
        pdf_source: PublicationSource,
        linked_json: Sequence[PublicationSource],
    ) -> ResearchPublication:
        selected_json = linked_json[0] if linked_json else None
        metadata = selected_json.raw_metadata if selected_json is not None else None
        content = selected_json.content if selected_json is not None else None
        publication_type = classify_publication_type(
            pdf_source.source_path,
            metadata,
            content,
        )
        filename_fields = _parse_filename(pdf_source.target_filename)
        raw_metadata = self._build_raw_metadata(
            selected_json or pdf_source,
            pdf_source,
            linked_json,
            filename_fields,
        )
        return ResearchPublication(
            publication_id=_publication_id(publication_type, pdf_source.checksum),
            publication_type=publication_type,
            title=filename_fields.title,
            authors=[filename_fields.author] if filename_fields.author is not None else [],
            local_path=(Path(input_root.name) / pdf_source.relative_path).as_posix(),
            raw_metadata=raw_metadata,
            content=content,
            created_at=selected_json.created_at if selected_json is not None else None,
            checksum=pdf_source.checksum,
        )

    def _publication_from_json_only(
        self,
        input_root: Path,
        source_group: Sequence[PublicationSource],
    ) -> ResearchPublication:
        selected_json = source_group[0]
        publication_type = classify_publication_type(
            selected_json.source_path,
            selected_json.raw_metadata,
            selected_json.content,
        )
        filename_fields = _parse_filename(selected_json.target_filename)
        raw_metadata = self._build_raw_metadata(
            selected_json,
            None,
            source_group,
            filename_fields,
        )
        return ResearchPublication(
            publication_id=_publication_id(publication_type, selected_json.checksum),
            publication_type=publication_type,
            title=filename_fields.title,
            authors=[filename_fields.author] if filename_fields.author is not None else [],
            local_path=(Path(input_root.name) / selected_json.relative_path).as_posix(),
            raw_metadata=raw_metadata,
            content=selected_json.content,
            created_at=selected_json.created_at,
            checksum=selected_json.checksum,
        )

    @staticmethod
    def _build_raw_metadata(
        selected_source: PublicationSource,
        pdf_source: PublicationSource | None,
        json_sources: Sequence[PublicationSource],
        filename_fields: _FilenameFields,
    ) -> JsonObject:
        raw_metadata = dict(selected_source.raw_metadata)
        lineage: JsonObject = {
            "selected_source_path": selected_source.relative_path.as_posix(),
            "json_source_paths": [source.relative_path.as_posix() for source in json_sources],
            "json_source_checksums": [source.checksum for source in json_sources],
            "pdf_source_path": (
                pdf_source.relative_path.as_posix() if pdf_source is not None else None
            ),
            "pdf_checksum": pdf_source.checksum if pdf_source is not None else None,
            "pdf_linked": pdf_source is not None,
            "filename_year": filename_fields.year,
            "filename_author": filename_fields.author,
            "title_source": "filename" if filename_fields.title is not None else None,
        }
        raw_metadata["_ingestion"] = cast(JsonValue, lineage)
        return raw_metadata

    @staticmethod
    def _missing_field_counts(
        publications: Iterable[ResearchPublication],
    ) -> dict[str, int]:
        counts = dict.fromkeys(_MISSING_FIELDS, 0)
        for publication in publications:
            values = publication.model_dump(mode="python")
            for field in _MISSING_FIELDS:
                value = values[field]
                if value is None or value == "" or value == []:
                    counts[field] += 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _write_publications(
        publications_path: Path,
        publications: Sequence[ResearchPublication],
    ) -> None:
        content = "".join(f"{publication.model_dump_json()}\n" for publication in publications)
        _atomic_write_text(publications_path, content)

    @staticmethod
    def _write_report(report_path: Path, report: IngestionReport) -> None:
        content = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        _atomic_write_text(report_path, f"{content}\n")


def _source_link_key(source: PublicationSource) -> SourceLinkKey:
    publication_type = classify_publication_type(
        source.source_path,
        source.raw_metadata,
        source.content,
    )
    normalized_filename = normalize("NFC", source.target_filename).casefold()
    return publication_type, normalized_filename


def _parse_filename(filename: str) -> _FilenameFields:
    stem = Path(filename).stem
    match = _FILENAME_PATTERN.fullmatch(stem)
    if match is None:
        return _FilenameFields(year=None, author=None, title=None)
    return _FilenameFields(
        year=int(match.group("year")),
        author=normalize("NFC", match.group("author")).strip() or None,
        title=normalize("NFC", match.group("title")).strip() or None,
    )


def _publication_id(publication_type: PublicationType, checksum: str) -> str:
    identity = f"publication:v1\0{publication_type.value}\0{checksum}".encode()
    digest = sha256(identity).hexdigest()
    return f"pub:kida:{digest[:32]}"


def _path_sort_key(path: Path) -> tuple[str, str]:
    raw_path = path.as_posix()
    return normalize("NFC", raw_path).casefold(), raw_path


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(content, encoding="utf-8", newline="\n")
    temporary_path.replace(path)
