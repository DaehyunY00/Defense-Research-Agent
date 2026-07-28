"""Entrypoint for the isolated Cloud Run sandbox worker image."""

from defense_research_agent.services.sandbox_worker import run_worker_from_environment


def main() -> int:
    """Run one Cloud Run Job task."""
    return run_worker_from_environment()


if __name__ == "__main__":
    raise SystemExit(main())
