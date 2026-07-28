variable "project_id" {
  description = "Google Cloud project that hosts the research application."
  type        = string
}

variable "region" {
  description = "Cloud Run, Artifact Registry, Firestore, and GCS region."
  type        = string
  default     = "asia-northeast3"
}

variable "app_image" {
  description = "Immutable application image URI pinned by sha256 digest."
  type        = string

  validation {
    condition     = strcontains(var.app_image, "@sha256:")
    error_message = "app_image must be pinned by sha256 digest."
  }
}

variable "api_service_name" {
  description = "Private Cloud Run API service name."
  type        = string
  default     = "defense-research-api"
}

variable "research_job_name" {
  description = "Asynchronous Cloud Run Job name."
  type        = string
  default     = "defense-research-worker"
}

variable "artifact_repository_name" {
  description = "Artifact Registry Docker repository name."
  type        = string
  default     = "defense-research"
}

variable "artifact_bucket_name" {
  description = "Globally unique GCS result bucket. Empty uses a project-derived name."
  type        = string
  default     = ""
}

variable "corpus_bucket_name" {
  description = "Globally unique private GCS bucket containing the approved internal corpus. Empty uses a project-derived name."
  type        = string
  default     = ""
}

variable "corpus_manifest_object" {
  description = "Exact approved corpus manifest object. Empty disables internal corpus search."
  type        = string
  default     = ""

  validation {
    condition = (
      var.corpus_manifest_object == ""
      || (
        startswith(var.corpus_manifest_object, "corpus/manifests/")
        && endswith(var.corpus_manifest_object, ".json")
        && !strcontains(var.corpus_manifest_object, "..")
      )
    )
    error_message = "corpus_manifest_object must be empty or a safe corpus/manifests/*.json path."
  }
}

variable "corpus_max_bytes" {
  description = "Maximum approved JSONL corpus size loaded by one worker."
  type        = number
  default     = 104857600

  validation {
    condition     = var.corpus_max_bytes >= 1 && var.corpus_max_bytes <= 524288000
    error_message = "corpus_max_bytes must be between 1 byte and 500 MiB."
  }
}

variable "official_source_domains" {
  description = "Bare DNS domains allowed for Claude server-side official-source web search."
  type        = list(string)
  default = [
    "assembly.go.kr",
    "congress.gov",
    "crsreports.congress.gov",
    "dapa.go.kr",
    "defense.gov",
    "gao.gov",
    "kida.re.kr",
    "law.go.kr",
    "mnd.go.kr",
    "nato.int",
    "state.gov",
    "un.org",
  ]

  validation {
    condition = (
      length(var.official_source_domains) >= 1
      && length(var.official_source_domains) <= 30
      && alltrue([
        for domain in var.official_source_domains :
        can(regex("^[a-z0-9][a-z0-9.-]*[a-z0-9]$", domain))
        && !startswith(domain, ".")
        && !endswith(domain, ".")
        && !strcontains(domain, "..")
      ])
    )
    error_message = "official_source_domains must contain 1-30 lowercase bare DNS domains."
  }
}

variable "official_search_max_uses" {
  description = "Maximum Claude server-side web searches per external issue query."
  type        = number
  default     = 4

  validation {
    condition     = var.official_search_max_uses >= 1 && var.official_search_max_uses <= 10
    error_message = "official_search_max_uses must be between 1 and 10."
  }
}

variable "firestore_database" {
  description = "Firestore Native mode database used for project state."
  type        = string
  default     = "(default)"
}

variable "firestore_location" {
  description = "Firestore location. Empty follows region; deploy.sh preserves an existing database."
  type        = string
  default     = ""
}

variable "project_collection" {
  description = "Firestore collection for compact project records."
  type        = string
  default     = "research_projects"
}

variable "anthropic_secret_id" {
  description = "Secret Manager secret containing the Claude API key."
  type        = string
  default     = "anthropic-api-key"
}

variable "anthropic_secret_version" {
  description = "Enabled numeric secret version injected into the worker."
  type        = string

  validation {
    condition     = can(regex("^[1-9][0-9]*$", var.anthropic_secret_version))
    error_message = "anthropic_secret_version must be a positive numeric version."
  }
}

variable "api_invoker_members" {
  description = "IAM users or service accounts allowed to invoke the private API."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for member in var.api_invoker_members :
      startswith(member, "user:") || startswith(member, "serviceAccount:")
    ])
    error_message = "API invokers must be user: or serviceAccount: IAM members."
  }
}

variable "deletion_protection" {
  description = "Protect the Cloud Run service and job from accidental deletion."
  type        = bool
  default     = false
}

variable "max_api_instances" {
  description = "Cost guardrail for private API instances."
  type        = number
  default     = 5

  validation {
    condition     = var.max_api_instances >= 1 && var.max_api_instances <= 20
    error_message = "max_api_instances must be between 1 and 20."
  }
}
