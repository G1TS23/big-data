# Le lake, en stockage objet.
#
# C'est le seul composant du pipeline qui reste lié au système de fichiers :
# eds/lake.py écrit avec shutil.copy2. Le passage au stockage objet est du code
# Python à écrire, pas de l'infrastructure — le conteneur est prêt, le code ne
# l'est pas encore. Voir docs/CLOUD.md.

resource "azurerm_storage_account" "lake" {
  name                = "st${local.prefixe_compact}"
  resource_group_name = azurerm_resource_group.eds.name
  location            = azurerm_resource_group.eds.location
  tags                = local.etiquettes

  account_tier             = "Standard"
  account_replication_type = "LRS"

  # Le chiffrement au repos est actif par défaut chez Azure ; ces deux réglages
  # ferment ce qui ne l'est pas : aucun accès en clair, aucun accès anonyme.
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false

  blob_properties {
    # Un dépôt du CHU écrasé par erreur reste récupérable : c'est ce qui donne
    # au lake sa valeur de preuve.
    versioning_enabled = true

    delete_retention_policy {
      days = 30
    }
  }
}

resource "azurerm_storage_container" "lake" {
  name                  = "lake"
  storage_account_id    = azurerm_storage_account.lake.id
  container_access_type = "private"
}

# Une durée de conservation doit être DÉFINIE : le RGPD interdit de garder
# indéfiniment. Dix ans par défaut, à arbitrer avec le DPO du CHU.
resource "azurerm_storage_management_policy" "lake" {
  storage_account_id = azurerm_storage_account.lake.id

  rule {
    name    = "expiration-des-depots"
    enabled = true

    filters {
      blob_types = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_creation_greater_than = var.retention_jours
      }
      version {
        delete_after_days_since_creation = 90
      }
    }
  }
}
