# Un groupe de ressources unique : tout l'entrepôt y vit, et « terraform
# destroy » ne peut rien laisser derrière lui ailleurs.

resource "azurerm_resource_group" "eds" {
  name     = "rg-${local.prefixe}"
  location = var.region
  tags     = local.etiquettes
}

# Le réseau privé porte tout le trafic interne. ClickHouse n'a pas d'adresse
# publique : c'est la traduction, à l'échelle du cloud, du choix déjà fait en
# local — le moteur n'est joignable que par les services qui en ont besoin.
resource "azurerm_virtual_network" "eds" {
  name                = "vnet-${local.prefixe}"
  resource_group_name = azurerm_resource_group.eds.name
  location            = azurerm_resource_group.eds.location
  address_space       = ["10.42.0.0/16"]
  tags                = local.etiquettes
}

resource "azurerm_subnet" "noeuds" {
  name                 = "snet-noeuds"
  resource_group_name  = azurerm_resource_group.eds.name
  virtual_network_name = azurerm_virtual_network.eds.name
  address_prefixes     = ["10.42.0.0/22"]
}
