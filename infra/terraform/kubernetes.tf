# Le cluster porte les trois mêmes services qu'en local : ClickHouse, Metabase,
# et le pipeline. La traduction est directe — docker-compose décrivait déjà des
# conteneurs, des volumes et une dépendance de démarrage.

resource "scaleway_k8s_cluster" "eds" {
  name        = local.prefixe
  description = "Entrepôt de données de santé — ${var.environnement}"
  version     = var.version_kubernetes
  cni         = "cilium"
  tags        = local.etiquettes

  private_network_id = scaleway_vpc_private_network.eds.id

  # Sans cette option, détruire le cluster laisserait derrière lui des volumes
  # et des adresses facturés — et, s'agissant de données de santé, des disques
  # qu'on croirait supprimés.
  delete_additional_resources = true

  auto_upgrade {
    enable                        = true
    maintenance_window_day        = "sunday"
    maintenance_window_start_hour = 3
  }

  autoscaler_config {
    disable_scale_down = false
    # Une exécution du pipeline dure une seconde et demie : un nœud libéré ne
    # doit pas être repris trop vite, sans quoi le cluster oscille.
    scale_down_delay_after_add = "10m"
  }
}

resource "scaleway_k8s_pool" "eds" {
  cluster_id  = scaleway_k8s_cluster.eds.id
  name        = "${local.prefixe}-noeuds"
  node_type   = var.type_noeud
  size        = var.taille_pool
  min_size    = var.taille_pool
  max_size    = var.taille_pool + 2
  autoscaling = true
  autohealing = true
  tags        = local.etiquettes

  upgrade_policy {
    max_surge       = 1
    max_unavailable = 0
  }
}

# L'API du cluster n'est joignable que depuis les adresses déclarées.
#
# Une liste vide ferme l'accès au lieu de l'ouvrir : c'est l'inverse du défaut
# habituel, et c'est délibéré. Sur un entrepôt de santé, l'oubli doit fermer.
resource "scaleway_k8s_acl" "eds" {
  cluster_id    = scaleway_k8s_cluster.eds.id
  no_ip_allowed = length(var.adresses_administration) == 0

  dynamic "acl_rules" {
    for_each = var.adresses_administration
    content {
      ip          = acl_rules.value
      description = "Poste d'administration déclaré"
    }
  }
}
