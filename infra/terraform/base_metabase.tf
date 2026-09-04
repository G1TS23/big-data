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

  # Azure choisit une zone à la création et Terraform la voit ensuite comme
  # une dérive : sans cette valeur, toute modification ultérieure du serveur
  # échoue sur « zone can only be changed when exchanged with… ». La fixer
  # rend aussi le déploiement reproductible.
  zone = "1"

  administrator_login    = "metabase"
  administrator_password = random_password.metabase.result

  # PAS D'ADRESSE PUBLIQUE. Renseigner ces deux attributs bascule la base en
  # accès privé : elle n'est joignable que depuis le réseau virtuel, comme
  # ClickHouse. Sans eux, Azure crée un point d'accès public — et, faute de
  # règle de pare-feu, une base que personne ne peut joindre : la panne se
  # manifeste par un délai d'attente, jamais par un refus explicite.
  #
  # Ce n'est pas une précaution de forme. Cette base porte la liste des comptes
  # qui accèdent à un entrepôt de données de santé ; l'exposer publiquement
  # contredirait l'argument que défend tout le reste de cette infrastructure.
  # Azure refuse les deux à la fois — « ConflictingPublicNetworkAccessAnd
  # VirtualNetworkConfiguration » — et ne le déduit pas tout seul.
  public_network_access_enabled = false
  delegated_subnet_id           = azurerm_subnet.base.id
  private_dns_zone_id           = azurerm_private_dns_zone.base.id

  depends_on = [azurerm_private_dns_zone_virtual_network_link.base]

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

# La résolution du nom privé. Sans cette zone, le FQDN de la base ne résout
# depuis aucun pod : l'accès privé n'est pas qu'une affaire de routage.
resource "azurerm_private_dns_zone" "base" {
  name                = "${local.prefixe}.private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.eds.name
  tags                = local.etiquettes
}

resource "azurerm_private_dns_zone_virtual_network_link" "base" {
  name                  = "lien-vnet"
  resource_group_name   = azurerm_resource_group.eds.name
  private_dns_zone_name = azurerm_private_dns_zone.base.name
  virtual_network_id    = azurerm_virtual_network.eds.id
  registration_enabled  = false
  tags                  = local.etiquettes
}

# Metabase crée sa propre base au premier démarrage et y pose l'extension
# « citext ». Azure n'autorise aucune extension par défaut : sans ce paramètre,
# le serveur répond « extension citext is not allow-listed » et Metabase
# s'arrête. Rien ne le laisse deviner avant le premier démarrage — c'est une
# différence entre un PostgreSQL managé et un PostgreSQL en conteneur, que le
# développement en local ne peut pas révéler.
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.metabase.id
  value     = "CITEXT"
}
