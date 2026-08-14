# ---------------------------------------------------------------------------
# Public Data Observatory — core GCP infrastructure
#
# Creates:
#   - required API enablement
#   - one BigQuery dataset per pipeline layer (raw / staging / intermediate / marts)
#   - a service account for GitHub Actions, with NO downloaded JSON key —
#     auth happens via Workload Identity Federation, scoped to one repo
#   - IAM bindings letting that service account run dbt + the ingestion
#     and quality scripts, and nothing more
# ---------------------------------------------------------------------------

locals {
  sa_account_id = "pdo-github-actions-${var.environment}"
  wif_pool_id   = "pdo-github-pool-${var.environment}"
  wif_provider_id = "pdo-github-provider"
}

# --- APIs --------------------------------------------------------------

resource "google_project_service" "required" {
  for_each = toset([
    "bigquery.googleapis.com",
    "iamcredentials.googleapis.com",
    "iam.googleapis.com",
    "sts.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# --- BigQuery datasets ---------------------------------------------------

resource "google_bigquery_dataset" "layer" {
  for_each = toset(var.dataset_ids)

  project     = var.project_id
  dataset_id  = each.value
  location    = var.bq_location
  description = "Public Data Observatory — ${each.value} layer"
  labels      = merge(var.labels, { layer = each.value })

  depends_on = [google_project_service.required]
}

# --- Service account for GitHub Actions ----------------------------------

resource "google_service_account" "github_actions" {
  project      = var.project_id
  account_id   = local.sa_account_id
  display_name = "Public Data Observatory — GitHub Actions (${var.environment})"

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "github_actions_bq_data_editor" {
  project = var.project_id
  role    = "roles/bigquery.dataEditor"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_project_iam_member" "github_actions_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.github_actions.email}"
}

# --- Workload Identity Federation: keyless auth from GitHub Actions ------
#
# This avoids ever generating/storing a downloadable service account key.
# GitHub's OIDC token is exchanged for short-lived GCP credentials at
# workflow runtime instead. The `attribute.repository == var.github_repo`
# condition below is what scopes trust to only this repo.

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = local.wif_pool_id
  display_name              = "GitHub Actions pool (${var.environment})"

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = local.wif_provider_id
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Only allow tokens whose `repository` claim matches this repo — a
  # workflow in a different repo cannot mint credentials for this SA
  # even if it somehow knew the pool/provider IDs.
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_actions_wif_binding" {
  service_account_id = google_service_account.github_actions.name
  role                = "roles/iam.workloadIdentityUser"
  member              = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
