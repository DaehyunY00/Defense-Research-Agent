output "sandbox_bucket" {
  description = "Dedicated immutable-request bucket."
  value       = google_storage_bucket.sandbox.name
}

output "sandbox_job_resource" {
  description = "Full Cloud Run Job resource name used by the controller."
  value = join("/", [
    "projects",
    var.project_id,
    "locations",
    var.region,
    "jobs",
    google_cloud_run_v2_job.sandbox.name,
  ])
}

output "worker_service_account" {
  description = "Least-privilege identity attached to the worker."
  value       = google_service_account.worker.email
}
