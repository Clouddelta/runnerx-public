output "github_environment_variables" {
  description = "Copy these non-secret values into the GitHub production Environment variables."
  value = {
    GCP_PROJECT_ID             = var.project_id
    GCP_REGION                 = var.region
    ARTIFACT_REPOSITORY        = google_artifact_registry_repository.images.repository_id
    CLOUD_RUN_SERVICE          = "${var.name_prefix}-api"
    CLOUD_SQL_CONNECTION_NAME  = google_sql_database_instance.database.connection_name
    RUNTIME_SERVICE_ACCOUNT    = google_service_account.runtime.email
    DEPLOY_SERVICE_ACCOUNT     = google_service_account.deployer.email
    DB_NAME                    = var.database_name
    DB_USER                    = var.database_user
    DB_PASSWORD_SECRET         = google_secret_manager_secret.database_password.secret_id
    WORKLOAD_IDENTITY_PROVIDER = google_iam_workload_identity_pool_provider.github.name
  }
}

output "cloud_sql_instance" {
  value = google_sql_database_instance.database.name
}

output "database_password_secret" {
  description = "Add a secret version out of band, then set DB_PASSWORD_SECRET_VERSION to its numeric version."
  value       = google_secret_manager_secret.database_password.id
}
