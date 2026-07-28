"""Integration test for the offline seven-role lab demonstration."""

import json
import subprocess
import sys
from pathlib import Path
from typing import cast


def test_research_lab_demo_reaches_human_review_boundary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "defense_research_agent.cli.research_lab_demo",
            "--project-id",
            "integration:lab",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    payload = cast(dict[str, object], json.loads(completed.stdout))

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert payload["status"] == "awaiting_human_review"
    assert len(cast(list[object], payload["specialist_results"])) == 4
    assert len(cast(list[object], payload["review_results"])) == 2
    tool_contexts = cast(list[dict[str, object]], payload["tool_contexts"])
    assert len(tool_contexts) == 6
    evidence_ids = {
        evidence["evidence_id"]
        for context in tool_contexts
        for evidence in cast(list[dict[str, object]], context["evidence"])
    }
    assert evidence_ids >= {
        "pub:ai-policy",
        "pub:metrics",
        "ext:gov:ai-workforce-policy",
    }
    assert any(str(evidence_id).startswith("sandbox:") for evidence_id in evidence_ids)
    assert {
        "analysis:completion-by-program",
        "analysis:planned-completed-correlation",
    } <= evidence_ids
    analysis_context = next(
        context for context in tool_contexts if context["role"] == "methodology_researcher"
    )
    for evidence in cast(list[dict[str, object]], analysis_context["evidence"]):
        metadata = cast(dict[str, object], evidence["metadata"])
        assert metadata["arbitrary_code_executed"] is False
        assert metadata["arbitrary_sql_executed"] is False
        assert metadata["source_mutated"] is False
    sandbox_results = cast(list[dict[str, object]], payload["sandbox_results"])
    assert len(sandbox_results) == 1
    assert sandbox_results[0]["status"] == "passed"
    assert sandbox_results[0]["applied_to_source"] is False
    assert sandbox_results[0]["deployed"] is False
    project_root = Path(__file__).parents[2]
    assert not (
        project_root / "src" / "defense_research_agent" / "poc" / "policy_metrics.py"
    ).exists()
    report = cast(dict[str, object], payload["final_report"])
    assert report["human_approval_required"] is True
