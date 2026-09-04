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

  # Explicite, et non par défaut : l'analyse statique signale cet attribut comme
  # un point bloquant, et elle a raison sur le fond — ce registre porte l'image
  # qui manipule des données de santé.
  #
  # Le mettre à « false » ne le durcirait pourtant pas, il le rendrait
  # INJOIGNABLE. Le SKU Basic n'offre ni point de terminaison privé ni règle
  # d'adresse : Microsoft réserve les deux au SKU Premium. Sans chemin privé,
  # couper l'accès public coupe aussi le cluster.
  #
  # Ce qui protège réellement le registre ici, ce n'est donc pas le réseau :
  # c'est que le compte administrateur est désactivé et que le seul droit
  # accordé est « AcrPull », à la seule identité du cluster.
  #
  # En production : SKU Premium, point de terminaison privé, et construction de
  # l'image par un agent situé dans le réseau plutôt que depuis un poste.
  public_network_access_enabled = true

  # Pas de bloc « identity » ici, malgré ce que suggère l'analyse statique. Une
  # identité PORTÉE PAR le registre ne sert qu'à ce que le registre appelle
  # d'autres services — clés de chiffrement gérées par le client, tâches ACR —
  # et nous n'utilisons ni l'un ni l'autre. L'authentification VERS le registre,
  # elle, passe déjà par une identité managée : celle du kubelet, ci-dessous.
  # Ajouter un bloc que rien ne consomme donnerait l'apparence d'un durcissement
  # sans en produire un.
}

# Le cluster tire l'image sans mot de passe : son identité suffit. Un secret de
# registre stocké dans Kubernetes serait un secret de plus à faire tourner.
resource "azurerm_role_assignment" "registre_cluster" {
  scope                = azurerm_container_registry.eds.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_kubernetes_cluster.eds.kubelet_identity[0].object_id
}
