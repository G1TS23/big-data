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
    """Le tableau publié dans docs/VALIDATION.md, figé.

    Les données du CHU sont versionnées avec le projet et ne changent pas : ces
    volumes sont donc des constantes, pas des ordres de grandeur. Un écart
    signale une règle de rejet modifiée, et un document à corriger.
    """

    def test_source(self, source):
        assert {t: source[t]["source"] for t in CORRESPONDANCES} == {
            "patients": 16_200, "sejours": 15_000, "diagnostics": 37_380,
            "monitoring": 66_677, "services": 8, "cim10": 10,
        }

    def test_silver(self, entrepot):
        assert {t: entrepot[t]["silver"] for t in CORRESPONDANCES} == {
            "patients": 6_000, "sejours": 14_864, "diagnostics": 37_040,
            "monitoring": 64_799, "services": 8, "cim10": 10,
        }

    def test_rejets(self, entrepot):
        assert {t: entrepot[t]["rejets"] for t in CORRESPONDANCES} == {
            "patients": 0, "sejours": 136, "diagnostics": 340,
            "monitoring": 1_878, "services": 0, "cim10": 0,
        }

    def test_les_doublons_patients_sont_des_instantanes_cumulatifs(self, source):
        """Les trois dépôts se contiennent : 16 200 lignes, 6 000 patients."""
        assert source["patients"]["doublons"] == 10_200
