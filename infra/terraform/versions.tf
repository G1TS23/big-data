# Socle Terraform de l'entrepôt de données de santé, sur Azure.
#
# Le fournisseur est ÉPINGLÉ, comme les dépendances Python le sont par leurs
# empreintes : une infrastructure qui se redéploie différemment six mois plus
# tard n'est pas reproductible.

terraform {
  required_version = ">= 1.6"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "azurerm" {
  features {
    key_vault {
      # Un coffre supprimé par erreur reste récupérable : il contient le sel de
      # pseudonymisation, dont la perte rendrait tout l'historique illisible.
      purge_soft_delete_on_destroy = false
    }
    resource_group {
      # Refuse de détruire un groupe qui contiendrait encore des ressources non
      # gérées par Terraform — un disque oublié porterait des données de santé.
      prevent_deletion_if_contains_resources = true
    }
  }
}

data "azurerm_client_config" "courant" {}
