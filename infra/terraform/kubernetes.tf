# Le cluster porte les trois mêmes services qu'en local : ClickHouse, Metabase,
# et le pipeline. La traduction est directe — docker-compose décrivait déjà des
# conteneurs, des volumes et une dépendance de démarrage.

resource "azurerm_kubernetes_cluster" "eds" {
  name                = "aks-${local.prefixe}"
  resource_group_name = azurerm_resource_group.eds.name
  location            = azurerm_resource_group.eds.location
  dns_prefix          = local.prefixe
  kubernetes_version  = var.version_kubernetes
  tags                = local.etiquettes

  # Une identité gérée plutôt qu'un couple identifiant/secret : il n'y a alors
  # aucun secret de plateforme à faire tourner ni à stocker.
  identity {
    type = "SystemAssigned"
  }

  default_node_pool {
    name            = "noeuds"
    vm_size         = var.gabarit_noeud
    node_count      = var.nombre_noeuds
    vnet_subnet_id  = azurerm_subnet.noeuds.id
    os_disk_size_gb = 32

    upgrade_settings {
      # Un nœud de plus pendant la montée de version, jamais un de moins : le
      # service reste rendu. C'est ce qui impose de garder de la marge sous le
      # quota de 6 vCPU.
      max_surge = "1"
    }
  }

  network_profile {
    network_plugin = "azure"
    network_policy = "calico"
    service_cidr   = "10.43.0.0/16"
    dns_service_ip = "10.43.0.10"
  }

  # L'API du cluster n'est joignable que depuis les adresses déclarées.
  #
  # ATTENTION : une liste VIDE laisse l'API ouverte à tout Internet — c'est le
  # défaut d'Azure, et c'est l'inverse de ce qu'on veut. Renseigner
  # adresses_administration avant tout usage réel. Le contrôle ci-dessous
  # refuse ce cas en production.
  api_server_access_profile {
    authorized_ip_ranges = var.adresses_administration
  }

  lifecycle {
    precondition {
      condition     = var.environnement != "production" || length(var.adresses_administration) > 0
      error_message = "En production, l'API du cluster ne peut pas rester ouverte : renseigner adresses_administration."
    }
  }
}
