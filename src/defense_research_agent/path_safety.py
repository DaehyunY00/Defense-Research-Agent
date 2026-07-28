"""Filesystem guards shared by generated-artifact writers."""

from pathlib import Path


def ensure_outside_read_only_data(
    output_path: Path,
    data_root: Path = Path("data"),
) -> None:
    """Reject resolved output paths at or below the repository's read-only data root."""
    resolved_output = output_path.resolve()
    resolved_data = data_root.resolve()
    if resolved_output == resolved_data or resolved_output.is_relative_to(resolved_data):
        raise ValueError("generated output must be outside the read-only data directory")
