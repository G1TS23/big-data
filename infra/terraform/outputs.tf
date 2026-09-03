output "registre" {
  description = "Où pousser l'image : docker push <registre>/eds:<version>"
  value       = scaleway_registry_namespace.eds.endpoint
}

output "lake" {
  description = "Bucket du lake, à renseigner dans EDS_LAKE_PATH."
  value       = scaleway_object_bucket.lake.endpoint
}

output "cluster_id" {
  description = "Identifiant du cluster, pour récupérer le kubeconfig."
  value       = scaleway_k8s_cluster.eds.id
}

output "secrets" {
  description = "Secrets créés VIDES : y déposer les valeurs hors de Terraform."
  value = {
    sel        = scaleway_secret.sel_pseudonymisation.id
    clickhouse = scaleway_secret.mots_de_passe_clickhouse.id
    metabase   = scaleway_secret.administration_metabase.id
  }
}

output "cle_pipeline" {
  description = "Clé d'accès de l'application pipeline."
  value       = scaleway_iam_api_key.pipeline.access_key
}

output "cle_pipeline_secrete" {
  description = "Secret associé. Ne jamais journaliser."
  value       = scaleway_iam_api_key.pipeline.secret_key
  sensitive   = true
}
