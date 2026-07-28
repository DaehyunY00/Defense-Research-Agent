"""Static deployment contract checks for the main GCP application."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "gcp-app"


def test_main_terraform_declares_private_async_key_scoped_architecture() -> None:
    main = (DEPLOY_ROOT / "main.tf").read_text(encoding="utf-8")

    assert 'resource "google_cloud_run_v2_service" "api"' in main
    assert 'resource "google_cloud_run_v2_job" "worker"' in main
    assert 'resource "google_firestore_database" "app"' in main
    assert 'resource "google_secret_manager_secret" "anthropic"' in main
    assert 'resource "google_storage_bucket" "artifacts"' in main
    assert 'resource "google_storage_bucket" "corpus"' in main
    assert 'resource "google_storage_bucket_iam_member" "worker_corpus_reader"' in main
    assert 'permissions = ["storage.objects.get"]' in main
    assert 'role     = "roles/run.jobsExecutorWithOverrides"' in main
    assert "max_retries     = 0" in main
    assert "if_generation_match" not in main
    assert '"allUsers"' not in main

    secret_environment = main.index('name = "ANTHROPIC_API_KEY"')
    worker = main.index('resource "google_cloud_run_v2_job" "worker"')
    api = main.index('resource "google_cloud_run_v2_service" "api"')
    assert worker < secret_environment < api
    assert "secret_data" not in main
    assert "local.worker_environment" in main
    assert "DEFENSE_RESEARCH_CORPUS_MANIFEST_OBJECT" in main
    assert "DEFENSE_RESEARCH_OFFICIAL_SOURCE_DOMAINS" in main


def test_deploy_script_streams_secret_outside_terraform_and_pins_image() -> None:
    deploy_script = (DEPLOY_ROOT / "deploy.sh").read_text(encoding="utf-8")
    variables = (DEPLOY_ROOT / "variables.tf").read_text(encoding="utf-8")

    assert "gcloud secrets versions add" in deploy_script
    assert "--data-file=-" in deploy_script
    assert "terraform" in deploy_script
    assert "gcloud builds submit" in deploy_script
    assert "image_summary.digest" in deploy_script
    assert "--if-generation-match=0" in deploy_script
    assert "defense_research_agent.cli.corpus_index" in deploy_script
    assert "DEFENSE_RESEARCH_APPROVE_CORPUS" in deploy_script
    assert "p020-" in deploy_script
    assert "@sha256:" in variables
    assert "secret_data" not in deploy_script


def test_application_container_is_non_root_and_serves_fastapi() -> None:
    dockerfile = (DEPLOY_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12-slim" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "defense_research_agent.api.app:app" in dockerfile
    assert "EXPOSE 8080" in dockerfile
