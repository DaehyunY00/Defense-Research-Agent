"""Integration test for the standalone local search CLI."""

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

from defense_research_agent.domain import PublicationType, ResearchPublication


def test_search_cli_supports_korean_and_type_filter(tmp_path: Path) -> None:
    index_path = tmp_path / "publications.jsonl"
    publications = [
        ResearchPublication(
            publication_id="pub:forum",
            publication_type=PublicationType.DEFENSE_FORUM,
            title="국방 인공지능 인력 정책",
            authors=["김정책"],
            content="인공지능을 활용한 인력 선발 정책을 연구한다.",
        ),
        ResearchPublication(
            publication_id="pub:brief",
            publication_type=PublicationType.KIDA_BRIEF,
            title="국방 인공지능 브리프",
            content="인공지능 기술 동향",
        ),
    ]
    index_path.write_text(
        "".join(f"{publication.model_dump_json()}\n" for publication in publications),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent.cli.search",
            "--index",
            str(index_path),
            "--query",
            "국방 인공지능 인력 정책",
            "--type",
            "defense_forum",
            "--limit",
            "10",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = cast(dict[str, object], json.loads(completed.stdout))
    results = cast(list[dict[str, object]], payload["results"])

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert payload["result_count"] == 1
    assert results[0]["publication_id"] == "pub:forum"
    assert "title" in cast(list[str], results[0]["matched_fields"])
    assert "인공지능" in completed.stdout
