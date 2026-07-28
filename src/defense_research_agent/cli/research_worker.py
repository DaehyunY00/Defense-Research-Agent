"""Execute one queued research project inside a Cloud Run Job."""

import json
import os

from defense_research_agent.services.gcp_research_runtime import (
    build_gcp_research_runner,
)


def main() -> int:
    """Run exactly one server-assigned project and return a process exit code."""
    project_id = os.environ.get("DEFENSE_RESEARCH_PROJECT_ID", "").strip()
    if not project_id:
        print(
            json.dumps(
                {"status": "error", "reason": "missing_project_id"},
                sort_keys=True,
            )
        )
        return 2
    try:
        result = build_gcp_research_runner().run(project_id)
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "project_id": project_id,
                    "reason": "research_execution_failed",
                    "error_type": type(error).__name__,
                },
                sort_keys=True,
            )
        )
        return 1
    if result is None:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "project_id": project_id,
                    "reason": "project_not_queued",
                },
                sort_keys=True,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "status": result.status.value,
                "project_id": project_id,
                "result_object": result.result_object,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
