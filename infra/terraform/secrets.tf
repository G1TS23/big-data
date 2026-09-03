# Les secrets sont DÉCLARÉS ici, jamais renseignés.
#
# Terraform crée le conteneur ; la valeur y est déposée hors de son état, par
# la console ou la CLI. La raison est simple : le fichier d'état de Terraform
# contient en clair tout ce qu'on lui confie. Écrire le sel dans une variable
# reviendrait à le publier dans terraform.tfstate.
#
# C'est la réponse à ce que le dossier annonce comme une limite : « en
# production, ce sel appartient à un coffre, pas à un fichier .env ».

resource "scaleway_secret" "sel_pseudonymisation" {
  name        = "${local.prefixe}-sel"
  description = <<-TEXTE
    Sel HMAC de pseudonymisation des identifiants patients.

    LE CHANGER ROMPT LA CONTINUITÉ DES PSEUDONYMES : les patients déjà chargés
    recevraient de nouvelles clés et les jointures avec l'historique
    deviendraient invalides. Une rotation impose de retraiter l'intégralité de
    la source, lake compris.
  TEXTE
  tags        = local.etiquettes

  # Empêche une suppression accidentelle : sans ce sel, l'historique des
  # pseudonymes est définitivement perdu.
  protected = true
}

resource "scaleway_secret" "mots_de_passe_clickhouse" {
  name        = "${local.prefixe}-clickhouse"
  description = "Comptes ClickHouse : administration et les trois comptes cloisonnés par usage."
  tags        = local.etiquettes
}

resource "scaleway_secret" "administration_metabase" {
  name        = "${local.prefixe}-metabase"
  description = "Compte d'administration Metabase, utilisé par « eds metabase »."
  tags        = local.etiquettes
}
