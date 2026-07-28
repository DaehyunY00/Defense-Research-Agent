"""Tests for the repository data-directory write guard."""

from pathlib import Path

import pytest

from defense_research_agent.path_safety import ensure_outside_read_only_data

PROJECT_ROOT = Path(__file__).parents[2]


def test_generated_outputs_are_blocked_inside_data_and_allowed_elsewhere(
    tmp_path: Path,
) -> None:
    protected_target = PROJECT_ROOT / "data" / "forbidden-output.json"

    with pytest.raises(ValueError, match="read-only data"):
        ensure_outside_read_only_data(protected_target)

    ensure_outside_read_only_data(tmp_path / "artifacts" / "allowed-output.json")
    assert not protected_target.exists()
