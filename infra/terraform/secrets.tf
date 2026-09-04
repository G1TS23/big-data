# Le coffre. Les secrets y sont DÉCLARÉS, jamais renseignés par Terraform.
#
# La raison tient en une phrase : le fichier d'état de Terraform contient en
# clair tout ce qu'on lui confie. Y écrire le sel de pseudonymisation
# reviendrait à le publier.
#
# C'est la réponse à ce que le dossier annonce comme une limite : « en
# production, ce sel appartient à un coffre, pas à un fichier .env ».

resource "azurerm_key_vault" "eds" {
  name                = "kv-${local.prefixe}"
  resource_group_name = azurerm_resource_group.eds.name
  location            = azurerm_resource_group.eds.location
  tenant_id           = data.azurerm_client_config.courant.tenant_id
  sku_name            = "standard"
  tags                = local.etiquettes

  # Sans le sel, l'historique des pseudonymes est définitivement perdu : une
  # suppression accidentelle doit rester réversible.
  soft_delete_retention_days = 90
  purge_protection_enabled   = var.environnement == "production"

  # Les droits passent par RBAC et non par des politiques d'accès : c'est le
  # même principe que le cloisonnement du moteur — une identité, des droits
  # explicites, rien d'implicite.
  rbac_authorization_enabled = true
}

# Qui déploie peut écrire les secrets. Sans cette attribution, « az keyvault
# secret set » échouerait juste après la création du coffre.
resource "azurerm_role_assignment" "coffre_administrateur" {
  scope                = azurerm_key_vault.eds.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.courant.object_id
}

# Le cluster lit les secrets, et rien d'autre. C'est le moindre privilège
# appliqué au cloud : le pipeline n'a pas besoin de créer des ressources.
resource "azurerm_role_assignment" "coffre_cluster" {
  scope                = azurerm_key_vault.eds.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_kubernetes_cluster.eds.kubelet_identity[0].object_id
}
