# L'image du pipeline, celle que le Dockerfile produit déjà.
#
# Privé, évidemment : elle embarque le code qui manipule des données de santé.
resource "azurerm_container_registry" "eds" {
  name                = "cr${local.prefixe_compact}"
  resource_group_name = azurerm_resource_group.eds.name
  location            = azurerm_resource_group.eds.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.etiquettes
}

# Le cluster tire l'image sans mot de passe : son identité suffit. Un secret de
# registre stocké dans Kubernetes serait un secret de plus à faire tourner.
resource "azurerm_role_assignment" "registre_cluster" {
  scope                = azurerm_container_registry.eds.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.eds.kubelet_identity[0].object_id
}
