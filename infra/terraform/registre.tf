# L'image du pipeline, celle que le Dockerfile produit déjà.
#
# Privé, évidemment : elle embarque le code qui manipule des données de santé.
resource "scaleway_registry_namespace" "eds" {
  name        = local.prefixe
  description = "Image du pipeline EDS, construite depuis le Dockerfile du dépôt."
  region      = var.region
  is_public   = false
}
