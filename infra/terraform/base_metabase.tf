# Base applicative de Metabase.
#
# En local, Metabase garde son état dans un H2 posé sur un volume : acceptable
# pour une démonstration, pas sur Kubernetes où un pod peut être déplacé à tout
# moment. H2 ne supporte ni le déplacement à chaud ni deux écrivains, et la
# panne se manifeste par une base corrompue plutôt que par une erreur claire.
#
# Cette base ne contient AUCUNE donnée de santé : elle porte les définitions de
# cartes, les comptes et les permissions. Le chiffrement au repos s'applique
# quand même — la liste des comptes qui accèdent à un entrepôt de santé mérite
# la même protection que l'entrepôt.

resource "azurerm_postgresql_flexible_server" "metabase" {
  name                = "psql-${local.prefixe}"
  resource_group_name = azurerm_resource_group.eds.name
  location            = azurerm_resource_group.eds.location
  version             = "16"
  tags                = local.etiquettes

  sku_name   = "B_Standard_B1ms"
  storage_mb = 32768

  administrator_login    = "metabase"
  administrator_password = random_password.metabase.result

  # Sept jours suffisent : perdre l'état de Metabase coûte la reconstruction
  # des tableaux de bord, que « eds metabase » sait refaire en une commande.
  backup_retention_days = 7

  # Un seul nœud hors production : la haute disponibilité doublerait le coût
  # pour protéger des données que l'on sait reconstruire.
  dynamic "high_availability" {
    for_each = var.environnement == "production" ? [1] : []
    content {
      mode = "ZoneRedundant"
    }
  }
}

resource "azurerm_postgresql_flexible_server_database" "metabase" {
  name      = "metabase"
  server_id = azurerm_postgresql_flexible_server.metabase.id
  charset   = "UTF8"
  collation = "en_US.utf8"
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

resource "azurerm_key_vault_secret" "metabase_mot_de_passe" {
  name         = "metabase-db-password"
  value        = random_password.metabase.result
  key_vault_id = azurerm_key_vault.eds.id

  depends_on = [azurerm_role_assignment.coffre_administrateur]
}
