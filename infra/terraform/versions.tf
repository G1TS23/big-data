# Socle Terraform de l'entrepôt de données de santé.
#
# Le fournisseur est ÉPINGLÉ, comme les dépendances Python le sont par leurs
# empreintes : une infrastructure qui se redéploie différemment six mois plus
# tard n'est pas reproductible.

terraform {
  required_version = ">= 1.6"

  required_providers {
    scaleway = {
      source  = "scaleway/scaleway"
      version = "~> 2.82"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "scaleway" {
  region = var.region
  zone   = var.zone
}
