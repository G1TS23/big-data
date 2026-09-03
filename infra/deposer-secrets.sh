#!/usr/bin/env bash
# Étape 3 du plan de migration : remplir le coffre.
#
# Terraform CRÉE le coffre mais n'y met aucune valeur : un secret écrit par
# Terraform finit en clair dans l'état. Les valeurs sont donc tirées ici, et
# n'existent qu'à deux endroits — le coffre, et la mémoire de ce script.
#
# Les secrets du cloud sont NEUFS : ils ne reprennent pas ceux du .env local.
# Deux environnements qui partagent un mot de passe n'en font plus qu'un, et
# un sel de pseudonymisation partagé rendrait les deux entrepôts corrélables.
#
#   ./infra/deposer-secrets.sh <nom-du-coffre>
#
# Idempotent : relancer ne réécrit pas un secret déjà présent, faute de quoi un
# second passage changerait le sel et casserait la continuité des pseudonymes.
set -euo pipefail

COFFRE="${1:-}"
if [[ -z "$COFFRE" ]]; then
  echo "usage : $0 <nom-du-coffre>   (terraform output -raw coffre)" >&2
  exit 2
fi

# Metabase refuse à l'initialisation un mot de passe sans majuscule, minuscule,
# chiffre et symbole : on tire une base alphanumérique puis on complète.
mot_de_passe() { python3 -c "
import secrets, string
base = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(28))
print(base + 'Aa1!')
"; }

sel() { python3 -c "import secrets; print(secrets.token_hex(32))"; }

deposer() {
  local nom="$1" valeur="$2"
  if az keyvault secret show --vault-name "$COFFRE" --name "$nom" >/dev/null 2>&1; then
    echo "  = $nom (déjà présent, inchangé)"
    return
  fi
  az keyvault secret set --vault-name "$COFFRE" --name "$nom" --value "$valeur" \
    --output none
  echo "  + $nom"
}

echo "Dépôt des secrets dans $COFFRE"
deposer eds-salt                          "$(sel)"
deposer clickhouse-admin-password         "$(mot_de_passe)"
deposer clickhouse-pilotage-password      "$(mot_de_passe)"
deposer clickhouse-exploitation-password  "$(mot_de_passe)"
deposer clickhouse-recherche-password     "$(mot_de_passe)"
deposer metabase-admin-password           "$(mot_de_passe)"
deposer metabase-demo-password            "$(mot_de_passe)"

echo
echo "Contrôle : $(az keyvault secret list --vault-name "$COFFRE" --query 'length(@)' -o tsv) secrets dans le coffre."
echo "Aucune valeur n'a été affichée, ni écrite sur disque."
