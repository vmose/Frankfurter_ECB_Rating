output "github_actions_service_account_email" {
  description = "Set this as the GCP_SERVICE_ACCOUNT value your workflows use with google-github-actions/auth."
  value       = google_service_account.github_actions.email
}

output "workload_identity_provider" {
  description = "Full resource name of the WIF provider — set as GCP_WORKLOAD_IDENTITY_PROVIDER for google-github-actions/auth's workload_identity_provider input."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "bigquery_dataset_ids" {
  description = "The BigQuery datasets created for each pipeline layer."
  value       = { for k, v in google_bigquery_dataset.layer : k => v.dataset_id }
}

output "project_id" {
  value = var.project_id
}
