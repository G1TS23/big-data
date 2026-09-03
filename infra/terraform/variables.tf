# Aucune valeur secrète ici. L'authentification vient de « az login », et les
# secrets applicatifs sont créés vides puis remplis hors de Terraform.

variable "projet" {
  description = "Préfixe des ressources, pour les distinguer d'un autre déploiement."
  type        = string
  default     = "edschu"

  validation {
    # Les noms de compte de stockage Azure n'acceptent ni tiret ni majuscule,
    # et sont limités à 24 caractères. Le préfixe doit donc rester sobre.
    condition     = can(regex("^[a-z0-9]{3,12}$", var.projet))
    error_message = "Le préfixe doit être en minuscules sans tiret, de 3 à 12 caractères."
  }
}

variable "environnement" {
  description = "Environnement déployé."
  type        = string
  default     = "recette"

  validation {
    condition     = contains(["production", "recette", "demo"], var.environnement)
    error_message = "Environnement attendu : production, recette ou demo."
  }
}

variable "region" {
  description = <<-TEXTE
    Région d'hébergement.

    En production, une donnée de santé française reste en France : la
    certification HDS de Microsoft (art. L1111-8 CSP) ne couvre que France
    Central et France South. La seconde validation ci-dessous l'impose.

    Hors production, la liste s'ouvre à l'Espace économique européen. Le RGPD
    reste respecté — aucun transfert hors EEE — mais la certification HDS,
    elle, est perdue. L'écart est acceptable sur des données fictives ; il ne
    le serait pas sur des données de patients.
  TEXTE
  type        = string
  default     = "francecentral"

  validation {
    # uaenorth, que la souscription étudiante autorise pourtant, est
    # délibérément absente de cette liste : elle est hors EEE.
    condition = contains([
      "francecentral", "francesouth",
      "northeurope", "westeurope",
      "germanywestcentral", "swedencentral", "polandcentral", "spaincentral",
      "italynorth", "norwayeast",
    ], var.region)
    error_message = "Région hors Espace économique européen : le RGPD interdit d'y transférer ces données."
  }

  validation {
    condition     = var.environnement != "production" || contains(["francecentral", "francesouth"], var.region)
    error_message = "En production, les données de santé restent en France : francecentral ou francesouth, seul périmètre de la certification HDS."
  }
}

variable "version_kubernetes" {
  description = "Version du plan de contrôle. Null laisse Azure choisir la version stable."
  type        = string
  default     = null
}

variable "gabarit_noeud" {
  description = <<-TEXTE
    Gabarit des nœuds. Standard_B2s_v2 offre 2 vCPU et 8 Gio.

    Le quota d'une souscription Azure for Students plafonne à 6 vCPU par région :
    deux nœuds en consomment quatre, et laissent la marge nécessaire pour qu'une
    montée de version puisse créer un nœud supplémentaire. Trois nœuds
    atteindraient le plafond et toute mise à jour échouerait.
  TEXTE
  type        = string
  default     = "Standard_B2s_v2"
}

variable "nombre_noeuds" {
  description = "Nombre de nœuds. Deux au minimum, pour survivre à la perte d'un nœud."
  type        = number
  default     = 2

  validation {
    condition     = var.nombre_noeuds >= 2
    error_message = "Un nœud unique interdit toute montée de version sans interruption."
  }
}

variable "adresses_administration" {
  description = <<-TEXTE
    Adresses autorisées à joindre l'API Kubernetes, en notation CIDR.

    Vide signifie « aucune restriction » côté Azure ; c'est l'inverse de ce que
    l'on veut, d'où l'avertissement dans kubernetes.tf. Renseigner l'adresse
    publique du poste d'administration.
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
  # Les comptes de stockage n'admettent ni tiret ni majuscule.
  prefixe_compact = "${var.projet}${var.environnement}"

  etiquettes = {
    projet        = var.projet
    environnement = var.environnement
    donnees       = "sante"
    gere_par      = "terraform"
  }
}
