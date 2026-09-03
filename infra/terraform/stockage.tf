# Le lake, en stockage objet.
#
# C'est le seul composant du pipeline qui reste lié au système de fichiers :
# eds/lake.py écrit avec shutil.copy2. Le passage au stockage objet est du code
# Python à écrire, pas de l'infrastructure — le bucket est prêt, le code ne
# l'est pas encore. Voir docs/CLOUD.md.

resource "scaleway_object_bucket" "lake" {
  name   = "${local.prefixe}-lake"
  region = var.region
  tags   = { for e in local.etiquettes : split(":", e)[0] => split(":", e)[1] }

  # Verrouillage objet : une fois écrit, un dépôt du CHU ne peut plus être
  # modifié ni supprimé avant l'échéance. C'est ce qui donne au lake sa valeur
  # de preuve — on peut toujours revenir à ce que l'hôpital a réellement déposé.
  object_lock_enabled = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    id      = "expiration-des-depots"
    enabled = true

    # Une durée de conservation doit être DÉFINIE : le RGPD interdit de garder
    # indéfiniment. Dix ans par défaut, à arbitrer avec le DPO du CHU.
    expiration {
      days = var.retention_jours
    }

    # Un import interrompu ne doit pas être facturé indéfiniment.
    abort_incomplete_multipart_upload_days = 7
  }
}

# Chiffrement au repos, imposé côté serveur : aucun écrivain ne peut l'oublier.
resource "scaleway_object_bucket_server_side_encryption_configuration" "lake" {
  bucket = scaleway_object_bucket.lake.id
  region = var.region

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
