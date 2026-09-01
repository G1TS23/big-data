"""Contrôles de la pseudonymisation — le cœur de la conformité du projet."""
import pytest

from eds.pseudonymize import PSEUDO_HEX_LEN, apply_privacy, generalize_year, pseudonymize
from tests.conftest import SALT


class TestPseudonymize:
    def test_deterministe(self):
        """Le pseudonyme d'un identifiant donné est figé.

        La valeur attendue est écrite en dur, et non recalculée par un second
        appel : comparer la fonction à elle-même passerait même si elle rendait
        une constante. Ancrer l'empreinte protège aussi l'ALGORITHME — remplacer
        HMAC par un simple hachage salé casserait ce test, alors qu'il resterait
        « déterministe ».

        Changer cette valeur revient à rompre la continuité des pseudonymes :
        toutes les jointures avec les données déjà chargées deviendraient
        invalides.
        """
        assert pseudonymize("IPP0000001", SALT) == "5cfacbf7a730e23a5fb695723e100d26"

    def test_sels_differents_pseudonymes_differents(self):
        assert pseudonymize("IPP0000001", SALT) != pseudonymize("IPP0000001", SALT + "x")

    def test_identifiants_differents_pseudonymes_differents(self):
        assert pseudonymize("IPP0000001", SALT) != pseudonymize("IPP0000002", SALT)

    def test_ne_contient_pas_la_valeur_source(self):
        assert "IPP0000001" not in pseudonymize("IPP0000001", SALT)

    def test_format(self):
        clef = pseudonymize("IPP0000001", SALT)
        assert len(clef) == PSEUDO_HEX_LEN
        assert all(c in "0123456789abcdef" for c in clef)

    @pytest.mark.parametrize("valeur", ["", "   ", None])
    def test_valeur_absente(self, valeur):
        assert pseudonymize(valeur, SALT) == ""

    def test_espaces_ignores(self):
        assert pseudonymize(" IPP0000001 ", SALT) == pseudonymize("IPP0000001", SALT)


class TestGeneralisation:
    @pytest.mark.parametrize("date_naissance,attendu", [
        ("1990-01-15", "1990"),
        ("2005-07-02", "2005"),
        ("", ""),            # absente
        ("15/01/1990", ""),  # format inattendu
        ("abcd-01-01", ""),  # non numérique
        ("1750-01-01", ""),  # hors bornes plausibles
        ("2999-01-01", ""),  # postérieure à aujourd'hui
    ])
    def test_annee(self, date_naissance, attendu):
        assert generalize_year(date_naissance) == attendu


class TestPolitiqueDeclaree:
    POLITIQUE = {
        "hash": [{"from": "patient_id", "to": "patient_key"}],
        "generalize": [{"from": "birth_date", "to": "birth_year", "rule": "year"}],
        "drop": ["patient_id", "nir", "nom", "prenom", "birth_date"],
    }
    LIGNE = {"patient_id": "IPP9000001", "nir": "199017512345678", "nom": "DUPONT",
             "prenom": "Jean", "birth_date": "1990-01-15", "sex": "M", "region_code": "75"}

    def test_identifiants_directs_supprimes(self):
        sortie = apply_privacy(self.LIGNE, self.POLITIQUE, SALT)
        for colonne in ("patient_id", "nir", "nom", "prenom", "birth_date"):
            assert colonne not in sortie

    def test_aucune_valeur_identifiante_ne_subsiste(self):
        sortie = apply_privacy(self.LIGNE, self.POLITIQUE, SALT)
        valeurs = "|".join(map(str, sortie.values()))
        for interdit in ("IPP9000001", "199017512345678", "DUPONT", "Jean", "1990-01-15"):
            assert interdit not in valeurs

    def test_donnees_utiles_conservees(self):
        sortie = apply_privacy(self.LIGNE, self.POLITIQUE, SALT)
        assert sortie["birth_year"] == "1990"
        assert sortie["sex"] == "M"
        assert sortie["region_code"] == "75"
        assert len(sortie["patient_key"]) == PSEUDO_HEX_LEN

    def test_ligne_source_non_modifiee(self):
        """La ligne portant l'identité ne doit jamais être altérée puis réutilisée."""
        avant = dict(self.LIGNE)
        apply_privacy(self.LIGNE, self.POLITIQUE, SALT)
        assert self.LIGNE == avant

    def test_regle_inconnue_rejetee(self):
        politique = {"generalize": [{"from": "birth_date", "to": "x", "rule": "inexistante"}]}
        with pytest.raises(ValueError):
            apply_privacy(self.LIGNE, politique, SALT)
