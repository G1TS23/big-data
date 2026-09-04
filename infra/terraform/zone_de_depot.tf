# ─── La zone de dépôt du CHU ─────────────────────────────────────────────────
#
# C'est l'endroit le PLUS SENSIBLE de tout le système, et le seul qui porte des
# identités en clair : nir, nom, prenom, birth_date. Tout ce qui est en aval a
# déjà traversé la pseudonymisation à l'entrée du lake.
#
# D'où un compte de stockage SÉPARÉ, et non un dossier de plus dans celui du
# lake. Qui peut lire l'entrepôt ne doit pas pouvoir lire les identités : la
# séparation des droits n'a de sens que si elle porte sur des objets distincts.

# Pas de bloc « identity » ici non plus. Une identité portée par le compte de
# stockage servirait à chiffrer avec une clé gérée par le client ; le chiffrement
# au repos d'Azure est déjà actif, et la clé du client relève d'un choix
# contractuel que ce projet n'a pas à trancher.
#
# Le vrai progrès serait ailleurs, et il reste à faire : le pilote CSI monte ce
# partage avec la CLÉ du compte, déposée dans le coffre. Une identité de charge
# de travail supprimerait ce secret partagé — c'est la piste, pas le bloc que
# l'analyse statique réclame.
resource "azurerm_storage_account" "depot" {
  name                = "st${local.prefixe_compact}src"
  resource_group_name = azurerm_resource_group.eds.name
  location            = azurerm_resource_group.eds.location
  tags                = merge(local.etiquettes, { sensibilite = "identifiante" })

  account_tier             = "Standard"
  account_replication_type = "LRS"

  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  # Le partage SMB exige la clé de compte pour être monté ; elle est déposée
  # dans le coffre et lue par le cluster, jamais écrite dans un manifeste.
  shared_access_key_enabled = true
}

# Le CHU y dépose ses exports quotidiens. Le pipeline le monte en LECTURE SEULE.
resource "azurerm_storage_share" "depot" {
  name               = "filestorage"
  storage_account_id = azurerm_storage_account.depot.id
  quota              = 50

  # 50 Gio : les 92 fichiers du jeu actuel pèsent 3,3 Mo, mais un CHU réel
  # déposerait des relevés de monitoring bien plus volumineux.
}

# La clé du compte, pour que Kubernetes puisse monter le partage.
resource "azurerm_key_vault_secret" "depot_cle" {
  name         = "depot-storage-key"
  value        = azurerm_storage_account.depot.primary_access_key
  key_vault_id = azurerm_key_vault.eds.id

  depends_on = [azurerm_role_assignment.coffre_administrateur]
}

# ─── Ce que cette zone impose, et que l'infrastructure ne peut pas garantir ──
#
# RÉTENTION COURTE. Une fois le fichier ingéré et pseudonymisé, le brut n'a plus
# de raison d'exister : le conserver revient à garder des identités dont on n'a
# plus l'usage, ce que la minimisation interdit. Le lake garde dix ans parce
# qu'il est anonyme ; cette zone devrait garder quelques jours.
#
# Azure Files n'offre PAS de règle de cycle de vie — contrairement au stockage
# objet, où le lake en a une. La purge doit donc être portée par une tâche
# planifiée, qu'il reste à écrire. C'est une dette assumée, pas un oubli : la
# nommer ici évite qu'elle se perde.
#
# En production, une variante mérite d'être étudiée : un conteneur objet avec
# SFTP activé plutôt qu'un partage SMB. Le CHU y pousserait par SFTP, et la
# rétention redeviendrait déclarative — au prix d'un montage plus complexe côté
# Kubernetes.
