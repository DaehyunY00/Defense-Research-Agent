output "api_url" {
  description = "Private Cloud Run API URL."
  value       = google_cloud_run_v2_service.api.uri
}

output "research_job_name" {
  description = "Cloud Run Job used for asynchronous research."
  value       = google_cloud_run_v2_job.worker.name
}

output "artifact_bucket" {
  description = "Private bucket containing complete research runs."
  value       = google_storage_bucket.artifacts.name
}

output "corpus_bucket" {
  description = "Private bucket containing reviewed corpus indexes and manifests."
  value       = google_storage_bucket.corpus.name
}

output "corpus_manifest_object" {
  description = "Exact approved corpus manifest configured for the worker; empty means external-only."
  value       = var.corpus_manifest_object
}

output "anthropic_secret" {
  description = "Secret Manager secret ID. The value is never managed by Terraform."
  value       = google_secret_manager_secret.anthropic.secret_id
}

output "api_invocation_example" {
  description = "Authenticated health request for the active gcloud identity."
  value = join(" ", [
    "curl",
    "-H",
    "\"Authorization: Bearer $(gcloud auth print-identity-token)\"",
    "\"${google_cloud_run_v2_service.api.uri}/healthz\"",
  ])
}
