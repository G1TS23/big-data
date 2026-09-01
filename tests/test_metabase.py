"""Contrôles de la couche de restitution.

Les réglages d'affichage se testent sans Metabase : ce sont des dictionnaires.
Le cloisonnement, lui, exige l'instance, et est ignoré si elle est absente.
"""
import pytest

from eds.config import load_settings
from eds.metabase import SQL_DASHBOARDS, affichage, charger_specification, colonnes

SPEC = charger_specification()
CARTES = [(t["nom"], c) for t in SPEC["tableaux"] for c in t["cartes"] if "sql" in c]


class TestSpecification:
    def test_chaque_carte_a_sa_requete(self):
        for _, carte in CARTES:
            assert (SQL_DASHBOARDS / carte["sql"]).is_file(), f"{carte['sql']} introuvable"

    def test_chaque_requete_nomme_ses_colonnes(self):
        """L'axe et les mesures sont déduits des alias `AS \"…\"`."""
        for _, carte in CARTES:
            sql = (SQL_DASHBOARDS / carte["sql"]).read_text(encoding="utf-8")
            assert colonnes(sql), f"{carte['sql']} n'a aucun alias nommé"

    def test_les_series_declarees_existent_dans_la_requete(self):
        for _, carte in CARTES:
            sql = (SQL_DASHBOARDS / carte["sql"]).read_text(encoding="utf-8")
            dispo = set(colonnes(sql))
            attendues = set(carte.get("series") or ([carte["serie"]] if carte.get("serie") else []))
            assert attendues <= dispo, f"{carte['sql']} : séries absentes {attendues - dispo}"

    def test_aucune_carte_n_interroge_une_couche_interne(self):
        """Un tableau de bord ne lit que gold ou ops, jamais silver ni bronze."""
        for _, carte in CARTES:
            sql = (SQL_DASHBOARDS / carte["sql"]).read_text(encoding="utf-8").lower()
            assert "silver." not in sql and "bronze." not in sql, carte["sql"]

    def test_les_cartes_ne_se_chevauchent_pas(self):
        """La grille Metabase fait 24 colonnes ; deux cartes ne peuvent pas
        occuper la même case."""
        for tableau in SPEC["tableaux"]:
            occupees = set()
            for carte in tableau["cartes"]:
                for r in range(carte["row"], carte["row"] + carte["size_y"]):
                    for c in range(carte["col"], carte["col"] + carte["size_x"]):
                        assert c < 24, f"{tableau['nom']} : carte hors grille"
                        assert (r, c) not in occupees, \
                            f"{tableau['nom']} : chevauchement en ({r}, {c})"
                        occupees.add((r, c))


class TestAffichage:
    def test_axe_et_mesures_deduits(self):
        sql = (SQL_DASHBOARDS / "pilot_alertes_motif.sql").read_text(encoding="utf-8")
        carte = next(c for _, c in CARTES if c["sql"] == "pilot_alertes_motif.sql")
        r = affichage(carte, sql, SPEC["couleurs"])
        assert r["graph.dimensions"] == ["Jour"]
        assert r["graph.metrics"] == ["Bradycardie", "Tachycardie", "Hypoxémie", "Fièvre"]
        assert r["stackable.stack_type"] == "stacked"

    def test_teintes_assignees_dans_l_ordre_fixe(self):
        """Une entité garde sa couleur : les teintes suivent l'ordre de la
        palette et ne sont jamais recyclées."""
        sql = (SQL_DASHBOARDS / "pilot_alertes_motif.sql").read_text(encoding="utf-8")
        carte = next(c for _, c in CARTES if c["sql"] == "pilot_alertes_motif.sql")
        couleurs = affichage(carte, sql, SPEC["couleurs"])["series_settings"]
        attendu = [SPEC["couleurs"][f"serie_{i}"] for i in range(1, 5)]
        assert [v["color"] for v in couleurs.values()] == attendu

    def test_pas_de_doublon_de_teinte_dans_une_carte(self):
        for _, carte in CARTES:
            sql = (SQL_DASHBOARDS / carte["sql"]).read_text(encoding="utf-8")
            reglages = affichage(carte, sql, SPEC["couleurs"]).get("series_settings", {})
            teintes = [v["color"] for v in reglages.values()]
            assert len(teintes) == len(set(teintes)), f"{carte['sql']} réutilise une teinte"

    def test_valeurs_affichees_sur_les_teintes_peu_contrastees(self):
        """Aqua, jaune et magenta passent sous 3:1 sur fond clair : les séries
        qui les portent doivent afficher leurs valeurs, faute de quoi elles ne
        sont plus identifiables autrement que par la couleur."""
        faibles = {"serie_3", "serie_4"}
        for _, carte in CARTES:
            if carte.get("couleur") in faibles:
                assert carte.get("valeurs"), f"{carte['sql']} : teinte peu contrastée sans étiquettes"


# ─── Cloisonnement — exige l'instance Metabase ─────────────────────────────

@pytest.fixture(scope="module")
def constats():
    """Metabase peut répondre alors que ClickHouse, derrière, est arrêté.

    Le contrôle n'a alors aucun sens : une carte échoue pour une raison
    technique, pas parce que les droits l'interdisent. On distingue les deux et
    on s'abstient plutôt que de conclure.
    """
    from eds.metabase import verifier_cloisonnement
    try:
        resultats = verifier_cloisonnement(load_settings())
    except Exception as exc:
        pytest.skip(f"Metabase indisponible : {exc}")
    if any(c.get("indisponible") for c in resultats):
        pytest.skip("source de données injoignable derrière Metabase")
    return resultats


class TestCloisonnementRestitution:
    def test_chaque_usage_ouvre_ses_cartes(self, constats):
        attendus = [c for c in constats if c["attendu"] == "autorisé"]
        assert attendus, "aucun accès autorisé testé"
        assert all(c["conforme"] for c in attendus)

    def test_aucun_usage_n_ouvre_les_cartes_d_un_autre(self, constats):
        refus = [c for c in constats if c["attendu"] == "refusé" and "ouvrir" in c["action"]]
        assert refus
        assert all(c["conforme"] for c in refus), \
            [c for c in refus if not c["conforme"]]

    def test_aucune_requete_libre_hors_de_sa_base(self, constats):
        """Sans cela, un utilisateur contournerait les cartes en écrivant son
        propre SQL sur la base d'un autre usage."""
        refus = [c for c in constats if c["attendu"] == "refusé" and "requête libre" in c["action"]]
        assert refus
        assert all(c["conforme"] for c in refus)
