"""Conservative reader for the observed PDF source format."""

from pathlib import Path

from defense_research_agent.data.readers.base import (
    PublicationReader,
    PublicationSource,
    SourceFileKind,
    calculate_file_checksum,
)


class PdfPublicationReader(PublicationReader):
    """Validate PDF identity and checksum without extracting PDF body text.

    Page text extraction is intentionally outside this reader. The current
    pipeline obtains existing page text from the paired metadata JSON source.
    """

    name = "pdf"
    suffixes = frozenset({".pdf"})

    def read(self, path: Path, input_root: Path) -> PublicationSource:
        """Read stable file metadata and checksum, leaving ``content`` unset."""
        with path.open("rb") as source_file:
            header = source_file.read(5)
        if header != b"%PDF-":
            raise ValueError("file does not have a valid PDF header")

        return PublicationSource(
            source_path=path,
            relative_path=path.relative_to(input_root),
            kind=SourceFileKind.PDF,
            checksum=calculate_file_checksum(path),
            target_filename=path.name,
            raw_metadata={
                "filename": path.name,
                "file_size_bytes": path.stat().st_size,
            },
        )
