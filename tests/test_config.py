"""Contrôles du chargement de la configuration.

C'est le premier code exécuté par toute commande. Une configuration invalide
doit être refusée AVANT qu'un octet de donnée de santé ne soit lu.
"""
import pytest

from eds.config import ConfigError, _validate, load_settings, load_sources


class TestReglagesDuProjet:
    def test_le_sel_est_present_et_suffisant(self):
        assert len(load_settings().salt) >= 32

    def test_les_chemins_sont_absolus(self):
        """Résolus au chargement : une commande lancée depuis un autre
        répertoire doit trouver les mêmes fichiers."""
        s = load_settings()
        assert s.source_path.is_absolute()
        assert s.lake_path.is_absolute()

    def test_le_depot_du_chu_existe(self):
        assert load_settings().source_path.is_dir()


class TestRefusDeDemarrage:
    """Mieux vaut ne pas démarrer que démarrer mal configuré."""

    def test_sans_sel(self, tmp_path, monkeypatch):
        monkeypatch.delenv("EDS_SALT", raising=False)
        vide = tmp_path / "vide.env"
        vide.write_text("EDS_SOURCE_PATH=/tmp\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="EDS_SALT"):
            load_settings(vide)

    def test_avec_le_sel_d_exemple_non_remplace(self, tmp_path, monkeypatch):
        """.env.example contient un marqueur : l'oublier doit bloquer."""
        monkeypatch.setenv("EDS_SALT", "remplacer_par_un_sel_de_64_caracteres")
        with pytest.raises(ConfigError, match="EDS_SALT"):
            load_settings(tmp_path / "inexistant.env")

    def test_avec_un_sel_trop_court(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EDS_SALT", "trop_court")
        with pytest.raises(ConfigError, match="trop court"):
            load_settings(tmp_path / "inexistant.env")

    def test_avec_un_depot_introuvable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EDS_SALT", "a" * 64)
        monkeypatch.setenv("EDS_SOURCE_PATH", str(tmp_path / "jamais_cree"))
        with pytest.raises(ConfigError, match="introuvable"):
            load_settings(tmp_path / "inexistant.env")


class TestDeclarationDesFlux:
    def test_les_six_flux_sont_declares(self):
        assert set(load_sources()) == {"patients", "sejours", "diagnostics",
                                       "monitoring", "actes", "referentiels"}

    def test_seuls_les_flux_porteurs_d_identite_ont_une_politique(self):
        """Une politique de confidentialité sur un flux qui n'en a pas besoin
        signalerait une erreur de déclaration."""
        avec = {n for n, s in load_sources().items() if s.get("privacy")}
        assert avec == {"patients", "sejours"}

    def test_une_colonne_supprimee_ne_peut_pas_etre_exposee(self):
        with pytest.raises(ConfigError, match="nir"):
            _validate({"patients": {"privacy": {"drop": ["nir"]},
                                    "lake_columns": ["patient_key", "nir"]}})

    def test_une_declaration_coherente_passe(self):
        _validate({"patients": {"privacy": {"drop": ["nir"]},
                                "lake_columns": ["patient_key"]}})
