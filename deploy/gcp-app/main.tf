locals {
  required_services = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
  ])
  artifact_bucket_name = (
    var.artifact_bucket_name != ""
    ? var.artifact_bucket_name
    : "${var.project_id}-defense-research-artifacts"
  )
  corpus_bucket_name = (
    var.corpus_bucket_name != ""
    ? var.corpus_bucket_name
    : "${var.project_id}-defense-research-corpus"
  )
  firestore_location = (
    var.firestore_location != ""
    ? var.firestore_location
    : var.region
  )
  common_environment = {
    GOOGLE_CLOUD_PROJECT                       = var.project_id
    DEFENSE_RESEARCH_GCP_REGION                = var.region
    DEFENSE_RESEARCH_JOB_NAME                  = var.research_job_name
    DEFENSE_RESEARCH_JOB_CONTAINER             = "research-worker"
    DEFENSE_RESEARCH_FIRESTORE_DATABASE        = var.firestore_database
    DEFENSE_RESEARCH_PROJECT_COLLECTION        = var.project_collection
    DEFENSE_RESEARCH_ARTIFACT_BUCKET           = local.artifact_bucket_name
    DEFENSE_RESEARCH_CLAUDE_TIMEOUT_SECONDS    = "90"
    DEFENSE_RESEARCH_CLAUDE_MAX_RETRIES        = "2"
  }
  worker_environment = merge(local.common_environment, {
    DEFENSE_RESEARCH_CORPUS_BUCKET                   = local.corpus_bucket_name
    DEFENSE_RESEARCH_CORPUS_MANIFEST_OBJECT          = var.corpus_manifest_object
    DEFENSE_RESEARCH_CORPUS_MAX_BYTES                = tostring(var.corpus_max_bytes)
    DEFENSE_RESEARCH_OFFICIAL_SOURCE_DOMAINS         = join(",", var.official_source_domains)
    DEFENSE_RESEARCH_OFFICIAL_SEARCH_MAX_USES        = tostring(var.official_search_max_uses)
  })
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository_name
  description   = "Defense research application images"
  format        = "DOCKER"

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret" "anthropic" {
  project   = var.project_id
  secret_id = var.anthropic_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

# The secret value is intentionally absent from Terraform and its state.
# deploy.sh adds the enabled version through stdin before the service is applied.

resource "google_firestore_database" "app" {
  project                     = var.project_id
  name                        = var.firestore_database
  location_id                 = local.firestore_location
  type                        = "FIRESTORE_NATIVE"
  delete_protection_state     = "DELETE_PROTECTION_DISABLED"
  deletion_policy             = "ABANDON"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = local.artifact_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket" "corpus" {
  project                     = var.project_id
  name                        = local.corpus_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  depends_on = [google_project_service.required]
}

resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "defense-research-api"
  display_name = "Defense research private API"
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "defense-research-worker"
  display_name = "Defense research Claude worker"
}

resource "google_project_iam_member" "api_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_member" "worker_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "api_result_reader" {
  bucket = google_storage_bucket.artifacts.name
  role   = google_project_iam_custom_role.result_reader.name
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_project_iam_custom_role" "result_reader" {
  project     = var.project_id
  role_id     = "defenseResearchResultReader"
  title       = "Defense Research Result Reader"
  description = "Read an exact research result object without list, update, or delete."
  permissions = ["storage.objects.get"]
}

resource "google_storage_bucket_iam_member" "worker_result_creator" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "worker_corpus_reader" {
  bucket = google_storage_bucket.corpus.name
  role   = google_project_iam_custom_role.result_reader.name
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_secret_manager_secret_iam_member" "worker_claude_key" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.anthropic.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_cloud_run_v2_job" "worker" {
  project             = var.project_id
  name                = var.research_job_name
  location            = var.region
  deletion_protection = var.deletion_protection

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.worker.email
      timeout         = "3600s"
      max_retries     = 0

      containers {
        name    = "research-worker"
        image   = var.app_image
        command = ["python"]
        args    = ["-m", "defense_research_agent.cli.research_worker"]

        dynamic "env" {
          for_each = local.worker_environment
          content {
            name  = env.key
            value = env.value
          }
        }

        env {
          name = "ANTHROPIC_API_KEY"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.anthropic.secret_id
              version = var.anthropic_secret_version
            }
          }
        }

        resources {
          limits = {
            cpu    = "2"
            memory = "2Gi"
          }
        }
      }
    }
  }

  depends_on = [
    google_firestore_database.app,
    google_project_iam_member.worker_firestore,
    google_secret_manager_secret_iam_member.worker_claude_key,
    google_storage_bucket_iam_member.worker_corpus_reader,
    google_storage_bucket_iam_member.worker_result_creator,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "api_executor" {
  project  = var.project_id
  location = google_cloud_run_v2_job.worker.location
  name     = google_cloud_run_v2_job.worker.name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_run_v2_service" "api" {
  project             = var.project_id
  name                = var.api_service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  template {
    service_account = google_service_account.api.email
    timeout         = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.max_api_instances
    }

    containers {
      name  = "api"
      image = var.app_image

      ports {
        name           = "http1"
        container_port = 8080
      }

      dynamic "env" {
        for_each = local.common_environment
        content {
          name  = env.key
          value = env.value
        }
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 1
        period_seconds        = 3
        failure_threshold     = 10

        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  depends_on = [
    google_cloud_run_v2_job_iam_member.api_executor,
    google_firestore_database.app,
    google_project_iam_member.api_firestore,
    google_storage_bucket_iam_member.api_result_reader,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "authorized_invoker" {
  for_each = var.api_invoker_members

  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = each.value
}
