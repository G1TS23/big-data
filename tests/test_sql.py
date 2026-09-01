"""Contrôles du découpage des scripts SQL.

ClickHouse n'accepte qu'une instruction par requête. Le découpeur doit donc
reconnaître les points-virgules qui SÉPARENT et ignorer ceux qui appartiennent
à un commentaire ou à une chaîne.
"""
import re
from pathlib import Path

import pytest

from eds.sql import split_statements

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


class TestDecoupage:
    def test_instructions_simples(self):
        assert split_statements("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]

    def test_derniere_instruction_sans_point_virgule(self):
        assert split_statements("SELECT 1;\nSELECT 2") == ["SELECT 1", "SELECT 2"]

    def test_point_virgule_dans_un_commentaire(self):
        """Nos scripts en contiennent : « on reconstruirait par partition ; à
        15 000 séjours, la simplicité l'emporte »."""
        script = "-- un commentaire ; avec un point-virgule\nSELECT 1;"
        assert split_statements(script) == ["SELECT 1"]

    def test_point_virgule_dans_une_chaine(self):
        assert split_statements("SELECT 'a;b' AS x;") == ["SELECT 'a;b' AS x"]

    def test_commentaire_en_fin_de_ligne(self):
        script = "SELECT 1;  -- explication\nSELECT 2;"
        assert split_statements(script) == ["SELECT 1", "SELECT 2"]

    def test_apostrophe_doublee_dans_une_chaine(self):
        assert split_statements("SELECT 'l''hôpital;' AS x;") == ["SELECT 'l''hôpital;' AS x"]

    def test_commentaire_dans_une_chaine_reste_dans_la_chaine(self):
        assert split_statements("SELECT '-- pas un commentaire' AS x;") \
            == ["SELECT '-- pas un commentaire' AS x"]

    def test_script_vide(self):
        assert split_statements("") == []
        assert split_statements("-- rien que des commentaires\n\n") == []

    def test_les_commentaires_sont_retires(self):
        assert "explication" not in split_statements("SELECT 1 -- explication\n;")[0]


class TestScriptsDuProjet:
    """Le découpeur est volontairement restreint aux constructions que nos
    scripts emploient. Ces contrôles garantissent qu'ils y restent — sinon il
    faudrait l'élargir, et non le contourner."""

    FICHIERS = sorted(SQL_DIR.glob("*.sql"))

    # Les chaînes et les commentaires sont retirés avant l'examen : ce qui s'y
    # trouve n'a pas à respecter les restrictions.
    _NEUTRALISE = re.compile(r"--[^\n]*|'(?:[^']|'')*'")

    def _code_nu(self, fichier: Path) -> str:
        return self._NEUTRALISE.sub("", fichier.read_text(encoding="utf-8"))

    def test_il_y_a_bien_des_scripts(self):
        assert self.FICHIERS

    @pytest.mark.parametrize("fichier", FICHIERS, ids=lambda f: f.name)
    def test_aucun_commentaire_de_bloc(self, fichier):
        assert "/*" not in self._code_nu(fichier), \
            "commentaire de bloc : le découpeur ne le reconnaît pas"

    @pytest.mark.parametrize("fichier", FICHIERS, ids=lambda f: f.name)
    def test_aucun_identifiant_entre_guillemets(self, fichier):
        assert '"' not in self._code_nu(fichier), \
            "identifiant entre guillemets : le découpeur ne le reconnaît pas"

    @pytest.mark.parametrize("fichier", FICHIERS, ids=lambda f: f.name)
    def test_aucun_echappement_par_barre_oblique(self, fichier):
        assert "\\'" not in fichier.read_text(encoding="utf-8"), \
            "échappement par barre oblique : utiliser '' pour doubler l'apostrophe"

    @pytest.mark.parametrize("fichier", FICHIERS, ids=lambda f: f.name)
    def test_le_decoupage_produit_des_instructions_non_vides(self, fichier):
        instructions = split_statements(fichier.read_text(encoding="utf-8"))
        assert instructions
        assert all(i.strip() for i in instructions)
        assert not any(i.lstrip().startswith("--") for i in instructions)
