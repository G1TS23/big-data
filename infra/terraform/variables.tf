# Aucune valeur secrète ici. Les identifiants du fournisseur viennent de
# l'environnement (SCW_ACCESS_KEY, SCW_SECRET_KEY), et les secrets applicatifs
# sont créés vides puis remplis hors de Terraform — voir secrets.tf.

variable "projet" {
  description = "Préfixe des ressources, pour les distinguer d'un autre déploiement."
  type        = string
  default     = "eds-chu"
}

variable "environnement" {
  description = "Environnement déployé : production, recette, bac à sable."
  type        = string
  default     = "recette"

  validation {
    condition     = contains(["production", "recette", "bac-a-sable"], var.environnement)
    error_message = "Environnement attendu : production, recette ou bac-a-sable."
  }
}

variable "region" {
  description = "Région d'hébergement. Une donnée de santé ne quitte pas le territoire."
  type        = string
  default     = "fr-par"

  validation {
    condition     = startswith(var.region, "fr-")
    error_message = "Les données de santé restent en France : la région doit commencer par « fr- »."
  }
}

variable "zone" {
  description = "Zone de disponibilité, à l'intérieur de la région."
  type        = string
  default     = "fr-par-1"
}

variable "version_kubernetes" {
  description = "Version du plan de contrôle Kubernetes."
  type        = string
  default     = "1.31"
}

variable "type_noeud" {
  description = <<-TEXTE
    Gabarit des nœuds. ClickHouse est le service exigeant : il tient l'entrepôt
    en mémoire pour ses agrégats. DEV1-L (4 vCPU, 8 Gio) suffit largement à la
    volumétrie observée — 155 Mio d'entrepôt — mais un CHU réel demanderait un
    gabarit à mémoire dominante.
  TEXTE
  type        = string
  default     = "DEV1-L"
}

variable "taille_pool" {
  description = "Nombre de nœuds. Trois pour tolérer la perte d'un nœud pendant une montée de version."
  type        = number
  default     = 3

  validation {
    condition     = var.taille_pool >= 2
    error_message = "Un nœud unique interdit toute montée de version sans interruption."
  }
}

variable "adresses_administration" {
  description = <<-TEXTE
    Adresses autorisées à joindre l'API Kubernetes. Vide signifie « aucune » et
    non « toutes » : voir kubernetes.tf, où l'absence de règle ferme l'accès.
  TEXTE
  type        = list(string)
  default     = []
}

variable "retention_jours" {
  description = "Durée de conservation des dépôts dans le lake, en jours."
  type        = number
  default     = 3650
}

locals {
  prefixe = "${var.projet}-${var.environnement}"

  etiquettes = [
    "projet:${var.projet}",
    "environnement:${var.environnement}",
    "donnees:sante",
    "gere-par:terraform",
  ]
}
