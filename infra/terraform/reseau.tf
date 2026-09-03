# Le réseau privé porte tout le trafic interne : ClickHouse n'a pas d'adresse
# publique. C'est la traduction, à l'échelle du cloud, du choix déjà fait en
# local — le moteur n'est joignable que par les services qui en ont besoin.

resource "scaleway_vpc" "eds" {
  name = local.prefixe
  tags = local.etiquettes
}

resource "scaleway_vpc_private_network" "eds" {
  name   = "${local.prefixe}-prive"
  vpc_id = scaleway_vpc.eds.id
  tags   = local.etiquettes

  ipv4_subnet {
    subnet = "172.16.32.0/22"
  }
}
