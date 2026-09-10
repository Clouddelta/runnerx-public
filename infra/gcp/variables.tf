variable "project_id" {
  description = "An existing GCP project with billing enabled. No production project is assumed."
  type        = string
}

variable "region" {
  description = "Region shared by Cloud SQL, Artifact Registry and Cloud Run."
  type        = string
  default     = "us-central1"
}

variable "name_prefix" {
  description = "Resource prefix. Use a separate project and prefix for each environment."
  type        = string
  default     = "runnerx"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,17}$", var.name_prefix))
    error_message = "Use 3-18 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "database_name" {
  type    = string
  default = "runnerx"
}

variable "database_user" {
  description = "Provision this built-in MySQL user separately; passwords never enter Terraform state."
  type        = string
  default     = "runnerx"
}

variable "database_tier" {
  description = "Small single-zone instance for the reference deployment; adjust for measured workload."
  type        = string
  default     = "db-custom-1-3840"
}

variable "github_repository" {
  description = "Exact owner/repository allowed to deploy, for example your-org/runnerx."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Set the exact GitHub owner/repository."
  }
}

variable "github_repository_id" {
  description = "Immutable numeric GitHub repository ID; prevents name-reuse trust."
  type        = string
  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "GitHub repository ID must be numeric."
  }
}

variable "github_owner_id" {
  description = "Immutable numeric GitHub repository owner ID."
  type        = string
  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "GitHub owner ID must be numeric."
  }
}
