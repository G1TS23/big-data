"""Contrôles de l'entrée du lake : ce qui est copié, ce qui est écarté."""
import csv

from eds.config import ConfigError, _validate
from eds.lake import discover, ingest, sha256_file
from tests.conftest import FIXTURES, SALT

SOURCES = {
    "patients": {
        "format": "csv", "path": "patients/{date}/patients.csv", "mode": "incremental",
        "source_columns": ["patient_id", "nir", "nom", "prenom", "birth_date", "sex", "region_code"],
        "privacy": {
            "hash": [{"from": "patient_id", "to": "patient_key"}],
            "generalize": [{"from": "birth_date", "to": "birth_year", "rule": "year"}],
            "drop": ["patient_id", "nir", "nom", "prenom", "birth_date"],
        },
        "lake_columns": ["patient_key", "birth_year", "sex", "region_code"],
    },
    "diagnostics": {"format": "json", "path": "diagnostics/{date}/diagnostics.json", "mode": "incremental"},
}


def _deposit(source, date):
    return next(d for d in discover(FIXTURES, SOURCES)
                if d.source == source and d.deposit_date == date)


class TestDecouverte:
    def test_trouve_les_depots(self):
        depots = discover(FIXTURES, SOURCES)
        assert {(d.source, d.deposit_date) for d in depots} == {
            ("patients", "2026-01-01"), ("patients", "2026-01-02"),
            ("diagnostics", "2026-01-01"),
        }


class TestCopiePseudonymisee:
    def test_aucune_identite_dans_le_lake(self, tmp_path):
        resultat = ingest(_deposit("patients", "2026-01-01"), tmp_path, SALT)
        assert resultat.status == "OK"

        contenu = resultat.lake_path.read_text(encoding="utf-8")
        for interdit in ("IPP9000001", "199017512345678", "DUPONT", "Jean", "1990-01-15"):
            assert interdit not in contenu

        entete = next(csv.reader(resultat.lake_path.open(encoding="utf-8")))
        assert entete == ["patient_key", "birth_year", "sex", "region_code"]

    def test_toutes_les_lignes_conservees(self, tmp_path):
        """L'entrée du lake ne filtre rien : écarter est le rôle de la couche silver."""
        resultat = ingest(_deposit("patients", "2026-01-01"), tmp_path, SALT)
        assert resultat.rows_in == resultat.rows_out == 3

    def test_date_naissance_illisible_ne_bloque_pas(self, tmp_path):
        """La 3e ligne n'a pas de date : elle passe, avec birth_year vide."""
        resultat = ingest(_deposit("patients", "2026-01-01"), tmp_path, SALT)
        lignes = list(csv.DictReader(resultat.lake_path.open(encoding="utf-8")))
        assert lignes[2]["birth_year"] == ""
        assert lignes[2]["patient_key"] != ""


class TestCopieBrute:
    def test_empreinte_identique(self, tmp_path):
        """Un flux sans identité est copié octet pour octet, et c'est vérifié."""
        depot = _deposit("diagnostics", "2026-01-01")
        resultat = ingest(depot, tmp_path, SALT)
        assert resultat.status == "OK"
        assert resultat.src_sha256 == resultat.lake_sha256 == sha256_file(depot.src_path)


class TestQuarantaine:
    def test_entete_non_conforme(self, tmp_path):
        resultat = ingest(_deposit("patients", "2026-01-02"), tmp_path, SALT)
        assert resultat.status == "QUARANTINE"
        assert "en-tête inattendu" in resultat.reason

    def test_fichier_conserve_avec_son_motif(self, tmp_path):
        ingest(_deposit("patients", "2026-01-02"), tmp_path, SALT)
        quarantaine = tmp_path / "_quarantaine" / "patients" / "2026-01-02"
        assert (quarantaine / "patients.csv").is_file()
        assert "en-tête inattendu" in (quarantaine / "patients.csv.motif.txt").read_text(encoding="utf-8")

    def test_rien_ecrit_dans_le_lake(self, tmp_path):
        ingest(_deposit("patients", "2026-01-02"), tmp_path, SALT)
        assert not (tmp_path / "patients" / "ingestion_date=2026-01-02").exists()


class TestInvariantDeConfiguration:
    def test_colonne_supprimee_puis_exposee_est_refusee(self):
        """Filet de sécurité : une politique incohérente doit échouer au démarrage."""
        incoherent = {"patients": {
            "privacy": {"drop": ["nir"]},
            "lake_columns": ["patient_key", "nir"],
        }}
        try:
            _validate(incoherent)
        except ConfigError as exc:
            assert "nir" in str(exc)
        else:
            raise AssertionError("la configuration incohérente aurait dû être refusée")
