terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }

  # Local backend by default so `terraform init` works with zero setup.
  # For a real deployment, switch to a GCS backend so state is shared
  # and survives your laptop dying:
  #
  # backend "gcs" {
  #   bucket = "your-tf-state-bucket"
  #   prefix = "public-data-observatory"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
