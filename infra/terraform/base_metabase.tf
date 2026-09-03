# Base applicative de Metabase.
#
# En local, Metabase garde son état dans un H2 posé sur un volume : c'est
# acceptable pour une démonstration, pas sur Kubernetes, où un pod peut être
# déplacé à tout moment. H2 ne supporte ni le déplacement à chaud ni deux
# écrivains, et la panne se manifeste par une base corrompue, pas par une
# erreur claire.
#
# Cette base ne contient AUCUNE donnée de santé : elle porte les définitions de
# cartes, les comptes et les permissions. Les données restent dans ClickHouse.
# Le chiffrement au repos est activé quand même — la politique s'applique à
# tout ce qui touche au dossier, y compris à la liste des comptes.

resource "scaleway_rdb_instance" "metabase" {
  name      = "${local.prefixe}-metabase"
  node_type = "DB-DEV-S"
  engine    = "PostgreSQL-15"
  tags      = local.etiquettes

  # Un seul nœud en recette, deux en production : perdre l'état de Metabase
  # coûte la reconstruction des tableaux de bord, que « eds metabase » sait
  # refaire — mais l'interruption reste une gêne pour les utilisateurs.
  is_ha_cluster = var.environnement == "production"

  encryption_at_rest = true

  user_name = "metabase"
  password  = random_password.metabase.result

  disable_backup            = false
  backup_schedule_frequency = 24
  backup_schedule_retention = 7

  private_network {
    pn_id       = scaleway_vpc_private_network.eds.id
    enable_ipam = true
  }
}

# Celui-là ne peut PAS rester hors de l'état, contrairement au sel de
# pseudonymisation : Terraform doit fournir le mot de passe initial à la base,
# donc il le connaît, donc terraform.tfstate le contient.
#
# La conséquence est à assumer plutôt qu'à ignorer : **l'état lui-même est un
# secret**. Il ne va pas dans git, il vit dans un stockage distant chiffré, et
# l'accès à ce stockage se traite comme l'accès à la base.
resource "random_password" "metabase" {
  length  = 32
  special = false # certains pilotes PostgreSQL digèrent mal les caractères échappés
}

# La valeur est déposée dans le gestionnaire de secrets, d'où Kubernetes la lit.
resource "scaleway_secret_version" "metabase" {
  secret_id = scaleway_secret.administration_metabase.id
  data      = random_password.metabase.result
}
