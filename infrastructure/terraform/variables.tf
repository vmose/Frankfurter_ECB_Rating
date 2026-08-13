variable "project_id" {
  description = "GCP project ID that hosts the observatory's BigQuery datasets."
  type        = string
}

variable "region" {
  description = "Default region for regional resources (the BigQuery datasets themselves use bq_location below)."
  type        = string
  default     = "us-central1"
}

variable "bq_location" {
  description = "BigQuery dataset location. 'US' is a multi-region and the simplest default."
  type        = string
  default     = "US"
}

variable "environment" {
  description = "Environment label applied to resources (dev/staging/prod), used in naming and labels."
  type        = string
  default     = "dev"
}

variable "github_repo" {
  description = "GitHub repo in 'owner/name' form, used to scope the Workload Identity Federation trust so only this repo's Actions runs can assume the deploy service account."
  type        = string
}

variable "dataset_ids" {
  description = "BigQuery datasets the pipeline writes to. Kept as a variable so quality/marts naming stays in one place."
  type        = list(string)
  default     = ["raw", "staging", "intermediate", "marts"]
}

variable "raw_table_expiration_days" {
  description = "Optional partition expiration for the raw table, in days. Set to 0 to disable (keep forever)."
  type        = number
  default     = 0
}

variable "labels" {
  description = "Common labels applied to all resources this module manages."
  type        = map(string)
  default = {
    project = "public-data-observatory"
  }
}
