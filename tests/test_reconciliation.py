"""Réconciliation source ↔ entrepôt : aucune ligne ne disparaît sans être comptée.

Ces contrôles recomptent les fichiers du CHU sans passer par le pipeline, puis
confrontent le résultat au contenu de l'entrepôt. Ils gardent le tableau de
docs/VALIDATION.md : si une règle de rejet change sans que le document suive,
c'est ici que ça se voit.
"""
import sys
from pathlib import Path

import pytest

from eds.config import load_settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "outils"))
from reconcilier import CORRESPONDANCES, compter_entrepot, compter_source  # noqa: E402


@pytest.fixture(scope="module")
def settings():
    return load_settings()


@pytest.fixture(scope="module")
def source(settings):
    racine = Path(settings.source_path)
    if not racine.is_dir():
        pytest.skip(f"dépôt source absent : {racine}")
    return compter_source(racine)


@pytest.fixture(scope="module")
def qualite(settings, entrepot):
    """Le bilan qualité du dernier run, indexé par règle."""
    from eds import sql
    client = sql.connect(settings)
    run = client.query("SELECT argMax(run_id, mesure_at) FROM ops.data_quality").result_rows[0][0]
    return {regle: (entree, concernees) for regle, entree, concernees
            in client.query("SELECT regle, lignes_entree, lignes_concernees "
                            "FROM ops.data_quality WHERE run_id = %(r)s",
                            parameters={"r": run}).result_rows}


@pytest.fixture(scope="module")
def entrepot(settings):
    from eds import sql
    try:
        client = sql.connect(settings, connect_timeout=2, send_receive_timeout=5)
        client.command("SELECT 1")
    except Exception as exc:
        pytest.skip(f"ClickHouse indisponible : {exc}")
    if not client.query("SELECT count() FROM system.tables "
                        "WHERE database = 'silver'").result_rows[0][0]:
        pytest.skip("silver absent — lancer `eds run`")
    return compter_entrepot(client)


@pytest.mark.parametrize("table", CORRESPONDANCES)
class TestReconciliation:
    def test_bronze_reproduit_la_source(self, table, source, entrepot):
        """Bronze recopie sans juger : le compte doit être celui de la source."""
        assert entrepot[table]["bronze"] == source[table]["source"]

    def test_aucune_ligne_ne_disparait(self, table, source, entrepot):
        """source = silver + rejets + doublons écartés."""
        e = entrepot[table]
        assert e["silver"] + e["rejets"] + source[table]["doublons"] == source[table]["source"]


class TestVolumesAttendus:
    """Les volumes du jeu de données, figés — le seul endroit du projet où ils
    le sont.

    Les fichiers du CHU sont versionnés avec le projet : ces nombres sont donc
    des constantes, pas des ordres de grandeur. Partout ailleurs, les tests
    éprouvent des mécanismes et restent indifférents au jeu de données ; si le
    CHU en livre un nouveau, c'est ce fichier seul qu'il faut reprendre, et les
    écarts qu'il signale disent exactement ce qui a changé.
    """

    def test_source(self, source):
        assert {t: source[t]["source"] for t in CORRESPONDANCES} == {
            "patients": 18_000, "sejours": 6_797, "diagnostics": 12_720,
            "monitoring": 41_778, "services": 8, "cim10": 13,
        }

    def test_silver(self, entrepot):
        assert {t: entrepot[t]["silver"] for t in CORRESPONDANCES} == {
            "patients": 6_000, "sejours": 6_729, "diagnostics": 12_593,
            "monitoring": 40_400, "services": 8, "cim10": 13,
        }

    def test_rejets(self, entrepot):
        assert {t: entrepot[t]["rejets"] for t in CORRESPONDANCES} == {
            "patients": 0, "sejours": 68, "diagnostics": 127,
            "monitoring": 1_378, "services": 0, "cim10": 0,
        }

    def test_les_doublons_patients_sont_des_instantanes(self, source):
        """Trois instantanés complets de 6 000 patients : 18 000 lignes reçues,
        12 000 répétitions, 6 000 patients réels."""
        assert source["patients"]["doublons"] == 12_000


class TestAnomaliesAttendues:
    """Ce que le jeu de données contient d'anormal, et en quelle quantité.

    Ces contrôles ne jugent pas le pipeline : ils décrivent la source. Un écart
    signifie que le CHU a livré autre chose — ce qui est arrivé, et que ces
    tests ont détecté immédiatement.
    """

    def test_signalements(self, qualite):
        assert {r: c for r, (_, c) in qualite.items()
                if r in SIGNALEMENTS} == SIGNALEMENTS

    def test_le_numerateur_de_readmission_se_reconstitue(self, qualite):
        fenetre, deces = qualite["retour_apres_deces_ecarte"]
        _, mutation = qualite["retour_apres_mutation_ecarte"]
        assert (fenetre, deces, mutation) == (780, 133, 255)
        assert fenetre - deces - mutation == 392


# Anomalies du jeu livré. Le jeu corrigé d'août 2026 a supprimé les
# chevauchements et les modes de sortie manquants : les contrôles restent, à
# zéro, pour qu'« aucune anomalie » se distingue de « plus personne ne mesure ».
SIGNALEMENTS = {
    "sejours_chevauchants": 0,
    "mode_sortie_manquant": 0,
    "admission_apres_deces": 133,
    "sejour_en_cours": 683,
    "releve_en_alerte": 3_270,
}
