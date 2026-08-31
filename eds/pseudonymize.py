"""Pseudonymisation appliquée à l'entrée du lake.

Choix et justification (RGPD art. 4-5 et 32) :

- **HMAC-SHA256 plutôt que sha256(sel + valeur)** : un simple hachage préfixé
  est exposé à l'extension de longueur, et surtout un sel concaténé mal isolé
  se retrouve facilement dans un log. HMAC est la construction prévue pour
  « hacher avec une clé ».
- **Déterministe** : le même IPP donne toujours le même pseudonyme, sinon les
  jointures patients ↔ séjours et le suivi des réadmissions deviennent
  impossibles.
- **Non réversible sans le sel** : le sel n'entre jamais dans l'entrepôt, n'est
  pas versionné, et vit uniquement dans .env (chmod 600).
- **Tronqué à 128 bits** : 32 caractères hexadécimaux suffisent largement pour
  quelques millions de patients, et divisent par deux le coût de stockage de la
  clé de jointure. Le risque de collision reste négligeable.

Attention : le pseudonyme reste une donnée à caractère personnel. Ce n'est pas
de l'anonymisation — la ré-identification redevient possible pour qui détient
le sel. C'est exactement l'effet recherché : réversible par le responsable de
traitement, opaque pour l'analyste.
"""
from __future__ import annotations

import hmac
from datetime import date
from hashlib import sha256

PSEUDO_HEX_LEN = 32  # 128 bits

_YEAR_MIN, _YEAR_MAX = 1900, date.today().year


def pseudonymize(value: str, salt: str) -> str:
    """Pseudonyme stable et non réversible d'un identifiant patient."""
    if value is None:
        return ""
    value = value.strip()
    if not value:
        return ""
    digest = hmac.new(salt.encode("utf-8"), value.encode("utf-8"), sha256).hexdigest()
    return digest[:PSEUDO_HEX_LEN]


def generalize_year(value: str) -> str:
    """Réduit une date de naissance à son année (généralisation).

    Retourne une chaîne vide si la date est absente ou illisible : la ligne est
    conservée et c'est la couche silver qui décidera de l'écarter, avec traçage.
    """
    if not value:
        return ""
    value = value.strip()
    if len(value) < 4:
        return ""
    year = value[:4]
    if not year.isdigit():
        return ""
    if not _YEAR_MIN <= int(year) <= _YEAR_MAX:
        return ""
    return year


RULES = {"year": generalize_year}


def apply_privacy(row: dict, privacy: dict, salt: str) -> dict:
    """Applique la politique déclarée dans config/sources.yml à une ligne.

    Produit un nouveau dictionnaire : la ligne d'origine, qui porte l'identité,
    n'est jamais modifiée en place ni réutilisée en aval.
    """
    out = dict(row)

    for rule in privacy.get("hash") or []:
        out[rule["to"]] = pseudonymize(row.get(rule["from"], ""), salt)

    for rule in privacy.get("generalize") or []:
        fn = RULES.get(rule["rule"])
        if fn is None:
            raise ValueError(f"règle de généralisation inconnue : {rule['rule']}")
        out[rule["to"]] = fn(row.get(rule["from"], ""))

    for column in privacy.get("drop") or []:
        out.pop(column, None)

    return out
