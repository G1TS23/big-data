output "groupe_ressources" {
  description = "Tout l'entrepôt vit ici. « terraform destroy » ne laisse rien ailleurs."
  value       = azurerm_resource_group.eds.name
}

output "registre" {
  description = "Où pousser l'image : docker push <registre>/eds:<version>"
  value       = azurerm_container_registry.eds.login_server
}

output "cluster" {
  description = "Récupérer le kubeconfig : az aks get-credentials -g <rg> -n <cluster>"
  value       = azurerm_kubernetes_cluster.eds.name
}

output "coffre" {
  description = "Y déposer les secrets, hors de Terraform : az keyvault secret set --vault-name <coffre> ..."
  value       = azurerm_key_vault.eds.name
}

output "lake" {
  description = "Conteneur du lake, quand eds/lake.py saura y écrire."
  value       = "${azurerm_storage_account.lake.name}/${azurerm_storage_container.lake.name}"
}

output "base_metabase" {
  description = "Hôte de la base applicative de Metabase."
  value       = azurerm_postgresql_flexible_server.metabase.fqdn
}

output "secrets_a_deposer" {
  description = "Les secrets que Terraform ne renseigne PAS, et qu'il faut déposer."
  value = [
    "eds-salt",
    "clickhouse-admin-password",
    "clickhouse-pilotage-password",
    "clickhouse-exploitation-password",
    "clickhouse-recherche-password",
    "metabase-admin-password",
    "metabase-demo-password",
  ]
}
