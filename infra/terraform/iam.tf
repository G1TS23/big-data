# Une identité par usage, au moindre privilège.
#
# Le pipeline n'a pas besoin de créer des clusters : il lit et écrit dans un
# bucket, et lit trois secrets. Lui donner davantage reviendrait à défaire, au
# niveau du cloud, le cloisonnement que l'entrepôt applique au niveau du moteur.

resource "scaleway_iam_application" "pipeline" {
  name        = "${local.prefixe}-pipeline"
  description = "Exécution planifiée du pipeline : lake, bronze, silver, gold."
  tags        = local.etiquettes
}

# Les noms de jeux de permissions sont des chaînes libres : « terraform
# validate » en vérifie la syntaxe, pas l'existence. Seul un « plan » contre un
# compte réel les confronterait à l'API. C'est la limite de la vérification hors
# ligne, et elle est dite plutôt que tue — voir docs/CLOUD.md.
resource "scaleway_iam_policy" "pipeline" {
  name           = "${local.prefixe}-pipeline"
  description    = "Lecture des secrets, lecture-écriture du lake. Rien d'autre."
  application_id = scaleway_iam_application.pipeline.id
  tags           = local.etiquettes

  rule {
    project_ids          = [data.scaleway_account_project.courant.id]
    permission_set_names = ["ObjectStorageObjectsRead", "ObjectStorageObjectsWrite"]
  }

  rule {
    project_ids          = [data.scaleway_account_project.courant.id]
    permission_set_names = ["SecretManagerSecretAccess"]
  }
}

resource "scaleway_iam_api_key" "pipeline" {
  application_id     = scaleway_iam_application.pipeline.id
  description        = "Clé utilisée par le CronJob du pipeline."
  default_project_id = data.scaleway_account_project.courant.id
}

data "scaleway_account_project" "courant" {}
