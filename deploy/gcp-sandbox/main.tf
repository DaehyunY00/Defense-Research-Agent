locals {
  required_services = toset([
    "compute.googleapis.com",
    "dns.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
  ])
  worker_network_tag = "defense-research-sandbox"
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_storage_bucket" "sandbox" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 1
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_service_account" "worker" {
  account_id   = "defense-sandbox-worker"
  display_name = "Defense research isolated sandbox worker"
}

resource "google_project_iam_custom_role" "sandbox_object_io" {
  role_id     = "defenseSandboxObjectIO"
  title       = "Defense Sandbox Object IO"
  description = "Get and create exact sandbox objects without list, overwrite, or delete."
  permissions = [
    "storage.objects.create",
    "storage.objects.get",
  ]
}

resource "google_storage_bucket_iam_member" "worker_object_io" {
  bucket = google_storage_bucket.sandbox.name
  role   = google_project_iam_custom_role.sandbox_object_io.name
  member = "serviceAccount:${google_service_account.worker.email}"
}

resource "google_storage_bucket_iam_member" "controller_object_io" {
  bucket = google_storage_bucket.sandbox.name
  role   = google_project_iam_custom_role.sandbox_object_io.name
  member = var.controller_principal
}

resource "google_compute_network" "sandbox" {
  name                    = "defense-research-sandbox"
  auto_create_subnetworks = false

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "sandbox" {
  name                     = "defense-research-sandbox-${var.region}"
  region                   = var.region
  network                  = google_compute_network.sandbox.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true
}

resource "google_dns_managed_zone" "googleapis" {
  name        = "defense-sandbox-googleapis"
  dns_name    = "googleapis.com."
  description = "Resolve Google APIs to Private Google Access VIPs."
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.sandbox.id
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_dns_record_set" "private_googleapis" {
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "private.googleapis.com."
  type         = "A"
  ttl          = 300
  rrdatas = [
    "199.36.153.8",
    "199.36.153.9",
    "199.36.153.10",
    "199.36.153.11",
  ]
}

resource "google_dns_record_set" "wildcard_googleapis" {
  managed_zone = google_dns_managed_zone.googleapis.name
  name         = "*.googleapis.com."
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["private.googleapis.com."]
}

resource "google_compute_firewall" "allow_private_googleapis" {
  name      = "defense-sandbox-allow-googleapis"
  network   = google_compute_network.sandbox.name
  direction = "EGRESS"
  priority  = 900

  destination_ranges = ["199.36.153.8/30"]
  target_tags        = [local.worker_network_tag]

  allow {
    protocol = "tcp"
    ports    = ["443"]
  }
}

resource "google_compute_firewall" "deny_other_egress" {
  name      = "defense-sandbox-deny-other-egress"
  network   = google_compute_network.sandbox.name
  direction = "EGRESS"
  priority  = 1000

  destination_ranges = ["0.0.0.0/0"]
  target_tags        = [local.worker_network_tag]

  deny {
    protocol = "all"
  }
}

resource "google_cloud_run_v2_job" "sandbox" {
  name                = var.job_name
  location            = var.region
  deletion_protection = var.deletion_protection

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.worker.email
      timeout         = "180s"
      max_retries     = 0

      containers {
        name  = "sandbox-worker"
        image = var.worker_image

        env {
          name  = "SANDBOX_BUCKET"
          value = google_storage_bucket.sandbox.name
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        }
      }

      vpc_access {
        egress = "ALL_TRAFFIC"

        network_interfaces {
          network    = google_compute_network.sandbox.name
          subnetwork = google_compute_subnetwork.sandbox.name
          tags       = [local.worker_network_tag]
        }
      }
    }
  }

  depends_on = [
    google_compute_firewall.allow_private_googleapis,
    google_compute_firewall.deny_other_egress,
    google_dns_record_set.private_googleapis,
    google_dns_record_set.wildcard_googleapis,
    google_project_service.required,
    google_storage_bucket_iam_member.worker_object_io,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "controller_executor" {
  project  = var.project_id
  location = google_cloud_run_v2_job.sandbox.location
  name     = google_cloud_run_v2_job.sandbox.name
  role     = "roles/run.jobsExecutorWithOverrides"
  member   = var.controller_principal
}
