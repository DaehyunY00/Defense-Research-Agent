variable "project_id" {
  description = "Google Cloud project that hosts the isolated sandbox."
  type        = string
}

variable "region" {
  description = "Cloud Run and subnet region."
  type        = string
  default     = "asia-northeast3"
}

variable "job_name" {
  description = "Cloud Run Job name."
  type        = string
  default     = "defense-research-sandbox"
}

variable "worker_image" {
  description = "Immutable Artifact Registry image digest for the sandbox worker."
  type        = string

  validation {
    condition     = strcontains(var.worker_image, "@sha256:")
    error_message = "worker_image must be pinned by sha256 digest."
  }
}

variable "bucket_name" {
  description = "Globally unique GCS bucket used only for ephemeral sandbox requests."
  type        = string
}

variable "controller_principal" {
  description = "IAM principal that submits jobs, for example serviceAccount:api@project.iam.gserviceaccount.com."
  type        = string

  validation {
    condition = (
      startswith(var.controller_principal, "serviceAccount:") ||
      startswith(var.controller_principal, "user:")
    )
    error_message = "controller_principal must be a serviceAccount: or user: IAM member."
  }
}

variable "subnet_cidr" {
  description = "Dedicated Direct VPC subnet; Cloud Run Jobs require /26 or larger."
  type        = string
  default     = "10.42.0.0/26"
}

variable "deletion_protection" {
  description = "Protect the deployed job from accidental deletion."
  type        = bool
  default     = true
}
