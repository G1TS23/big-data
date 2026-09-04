#!/usr/bin/env bash
# Étape 5 du plan de migration : transporter les secrets du coffre au cluster.
#
# Le coffre reste la SOURCE : on ne tape jamais une valeur ici, on la lit. Ce
# script ne fait que traduire des noms — le coffre nomme en minuscules à
# tirets, Kubernetes exige des identifiants de variables d'environnement,
# parce que « envFrom » transforme chaque clé en variable et ignore
# silencieusement celles qui n'en sont pas.
#
#   ./infra/secrets-kubernetes.sh <coffre> <groupe-de-ressources>
#
# Trois secrets sont créés, et le découpage est délibéré :
#   eds-secrets   ce dont l'application a besoin, injecté en entier (envFrom)
#   metabase-db   la base applicative de Metabase, que l'application NE voit pas
#   eds-depot     la clé du partage où le CHU dépose, lue par le pilote CSI
set -euo pipefail

COFFRE="${1:-}" ; GROUPE="${2:-}"
if [[ -z "$COFFRE" || -z "$GROUPE" ]]; then
  echo "usage : $0 <coffre> <groupe-de-ressources>" >&2
  exit 2
fi

lire() {
  local nom="$1"
  az keyvault secret show --vault-name "$COFFRE" --name "$nom" --query value -o tsv
}

# --dry-run=client | apply : idempotent, et ne laisse aucune valeur en ligne de
# commande visible par un autre processus du cluster.
appliquer() {
  local nom="$1"
  kubectl apply -n eds -f - >/dev/null
  echo "  ✓ $nom"
}

echo "Transport des secrets depuis $COFFRE"

kubectl create secret generic eds-secrets -n eds \
  --from-literal=EDS_SALT="$(lire eds-salt)" \
  --from-literal=CLICKHOUSE_ADMIN_PASSWORD="$(lire clickhouse-admin-password)" \
  --from-literal=CLICKHOUSE_PILOTAGE_PASSWORD="$(lire clickhouse-pilotage-password)" \
  --from-literal=CLICKHOUSE_EXPLOITATION_PASSWORD="$(lire clickhouse-exploitation-password)" \
  --from-literal=CLICKHOUSE_RECHERCHE_PASSWORD="$(lire clickhouse-recherche-password)" \
  --from-literal=METABASE_ADMIN_PASSWORD="$(lire metabase-admin-password)" \
  --from-literal=METABASE_DEMO_PASSWORD="$(lire metabase-demo-password)" \
  --dry-run=client -o yaml | appliquer eds-secrets

HOTE=$(az postgres flexible-server list -g "$GROUPE" --query "[0].fullyQualifiedDomainName" -o tsv)
kubectl create secret generic metabase-db -n eds \
  --from-literal=METABASE_DB_HOST="$HOTE" \
  --from-literal=METABASE_DB_PASSWORD="$(lire metabase-db-password)" \
  --dry-run=client -o yaml | appliquer metabase-db

# Le pilote file.csi.azure.com impose ces deux noms de clés.
COMPTE=$(az storage account list -g "$GROUPE" --query "[0].name" -o tsv)
kubectl create secret generic eds-depot -n eds \
  --from-literal=azurestorageaccountname="$COMPTE" \
  --from-literal=azurestorageaccountkey="$(lire depot-storage-key)" \
  --dry-run=client -o yaml | appliquer eds-depot

echo
kubectl get secrets -n eds --no-headers | awk '{printf "  %-14s %s clés\n", $1, $3}'
