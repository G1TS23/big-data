"""Contrôles de l'entrée du lake : ce qui est copié, ce qui est écarté."""
import csv

import pytest

from eds.config import ConfigError, _validate
from eds import lake
from eds.lake import discover, ingest
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
    "sejours": {
        "format": "csv", "path": "sejours/{date}/sejours.csv", "mode": "incremental",
        "source_columns": ["stay_id", "patient_id", "service_code", "admission_ts",
                           "discharge_ts", "admission_mode", "discharge_mode"],
        "privacy": {
            "hash": [{"from": "patient_id", "to": "patient_key"}],
            "drop": ["patient_id"],
        },
        "lake_columns": ["stay_id", "patient_key", "service_code", "admission_ts",
                         "discharge_ts", "admission_mode", "discharge_mode"],
    },
    "diagnostics": {"format": "json", "path": "diagnostics/{date}/diagnostics.json", "mode": "incremental"},
    "monitoring": {"format": "parquet", "path": "monitoring/{date}/monitoring.parquet",
                   "mode": "incremental"},
    "referentiels": {
        "format": "csv", "mode": "full",
        "files": [
            {"path": "referentiels/{date}/services.csv",
             "source_columns": ["service_code", "service_label"]},
            {"path": "referentiels/{date}/cim10.csv",
             "source_columns": ["code_cim10", "libelle"]},
        ],
    },
}


def _deposit(source, date):
    return next(d for d in discover(FIXTURES, SOURCES)
                if d.source == source and d.deposit_date == date)


class TestDecouverte:
    def test_trouve_les_depots(self):
        depots = discover(FIXTURES, SOURCES)
        assert {(d.source, d.deposit_date) for d in depots} == {
            ("patients", "2026-01-01"), ("patients", "2026-01-02"),
            ("sejours", "2026-01-01"), ("diagnostics", "2026-01-01"),
            ("monitoring", "2026-01-01"),
            ("referentiels/services", "2026-01-01"),
            ("referentiels/cim10", "2026-01-01"),
        }

    def test_source_multi_fichiers_donne_un_depot_par_fichier(self):
        """Les référentiels déclarent deux fichiers sous une seule source :
        chacun doit être découvert et nommé séparément."""
        depots = [d for d in discover(FIXTURES, SOURCES) if d.source.startswith("referentiels/")]
        assert len(depots) == 2
        assert {d.src_path.name for d in depots} == {"services.csv", "cim10.csv"}


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
    def test_copie_octet_pour_octet(self, tmp_path):
        """Un flux sans identité est copié à l'identique — vérifié sur les
        octets eux-mêmes, ce qu'une comparaison d'empreintes ne faisait
        qu'indirectement."""
        depot = _deposit("diagnostics", "2026-01-01")
        resultat = ingest(depot, tmp_path, SALT)
        assert resultat.status == "OK"
        assert resultat.lake_path.read_bytes() == depot.src_path.read_bytes()

    def test_fichier_binaire_copie_sans_alteration(self, tmp_path):
        """Le monitoring arrive en Parquet. Une copie qui passerait par un
        décodage texte le corromprait silencieusement."""
        depot = _deposit("monitoring", "2026-01-01")
        resultat = ingest(depot, tmp_path, SALT)
        assert resultat.status == "OK"
        assert resultat.lake_path.read_bytes() == depot.src_path.read_bytes()

    def test_le_parquet_reste_lisible_apres_copie(self, tmp_path):
        """La copie doit rester un Parquet valide, pas seulement des octets
        identiques : c'est ClickHouse qui le lira ensuite."""
        import pyarrow.parquet as pq
        resultat = ingest(_deposit("monitoring", "2026-01-01"), tmp_path, SALT)
        table = pq.read_table(resultat.lake_path)
        assert table.num_rows == 6
        assert table.schema.names == ["stay_id", "ts", "heart_rate", "spo2", "temp_c"]

    def test_les_valeurs_aberrantes_ne_sont_pas_filtrees(self, tmp_path):
        """Le lake ne corrige rien : la fréquence à 500 doit arriver intacte,
        c'est la couche silver qui l'écartera, en la traçant."""
        import pyarrow.parquet as pq
        resultat = ingest(_deposit("monitoring", "2026-01-01"), tmp_path, SALT)
        assert 500 in pq.read_table(resultat.lake_path).column("heart_rate").to_pylist()

    def test_referentiel_copie_tel_quel(self, tmp_path):
        """Une nomenclature ne porte aucune identité : pas de politique de
        confidentialité, donc copie brute."""
        depot = _deposit("referentiels/services", "2026-01-01")
        resultat = ingest(depot, tmp_path, SALT)
        assert resultat.status == "OK"
        assert resultat.lake_path.read_bytes() == depot.src_path.read_bytes()


class TestReprisApresInterruption:
    """Le CHU garantit que le contenu d'un dépôt ne change jamais. Le risque
    n'est donc pas la source qui bouge, c'est notre écriture qui s'arrête."""

    def test_une_interruption_ne_laisse_rien_a_l_emplacement_definitif(self, tmp_path, monkeypatch):
        depot = _deposit("monitoring", "2026-01-01")
        monkeypatch.setattr(lake, "_publier",
                            lambda *a: (_ for _ in ()).throw(KeyboardInterrupt("tué")))
        with pytest.raises(KeyboardInterrupt):
            ingest(depot, tmp_path, SALT)

        publies = list(tmp_path.glob("monitoring/**/monitoring.parquet"))
        assert publies == [], "un fichier incomplet occupe son emplacement définitif"
        assert list(tmp_path.rglob(f"*{lake.PARTIEL}")), "aucun résidu à reprendre"

    def test_la_reprise_efface_le_residu_puis_recommence(self, tmp_path, monkeypatch):
        depot = _deposit("monitoring", "2026-01-01")
        monkeypatch.setattr(lake, "_publier",
                            lambda *a: (_ for _ in ()).throw(KeyboardInterrupt("tué")))
        with pytest.raises(KeyboardInterrupt):
            ingest(depot, tmp_path, SALT)
        monkeypatch.undo()

        effaces = lake.nettoyer_residus(tmp_path)
        assert [r.name for r in effaces] == ["monitoring.parquet" + lake.PARTIEL]
        assert not list(tmp_path.rglob(f"*{lake.PARTIEL}"))

        resultat = ingest(depot, tmp_path, SALT)
        assert resultat.status == "OK"
        assert resultat.lake_path.read_bytes() == depot.src_path.read_bytes()

    def test_le_nettoyage_epargne_les_fichiers_publies(self, tmp_path):
        """Un fichier correctement publié ne doit jamais être effacé par la
        reprise, sans quoi chaque incident recopierait tout le lake."""
        resultat = ingest(_deposit("monitoring", "2026-01-01"), tmp_path, SALT)
        assert lake.nettoyer_residus(tmp_path) == []
        assert resultat.lake_path.is_file()

    def test_un_depot_journalise_mais_absent_n_est_pas_considere_publie(self, tmp_path):
        """Le journal dit ce qui a été ingéré, pas ce qui est encore là. Un lake
        purgé ou un volume démonté ferait échouer l'étape suivante sans rien
        expliquer si l'on se fiait au seul journal."""
        depot = _deposit("monitoring", "2026-01-01")
        assert not lake.est_publie(depot, tmp_path)

        resultat = ingest(depot, tmp_path, SALT)
        assert lake.est_publie(depot, tmp_path)

        resultat.lake_path.unlink()
        assert not lake.est_publie(depot, tmp_path)

    def test_un_residu_ne_compte_pas_comme_publie(self, tmp_path, monkeypatch):
        """Un « .partiel » n'est pas une publication."""
        depot = _deposit("monitoring", "2026-01-01")
        monkeypatch.setattr(lake, "_publier",
                            lambda *a: (_ for _ in ()).throw(KeyboardInterrupt("tué")))
        with pytest.raises(KeyboardInterrupt):
            ingest(depot, tmp_path, SALT)
        assert not lake.est_publie(depot, tmp_path)

    def test_le_nettoyage_supporte_un_lake_inexistant(self, tmp_path):
        assert lake.nettoyer_residus(tmp_path / "jamais_cree") == []

    def test_la_copie_brute_ne_relit_pas_la_source(self, tmp_path):
        """Décider et copier ne doivent coûter qu'une lecture. L'ancienne
        version en faisait trois : hacher la source, copier, hacher la copie."""
        resultat = ingest(_deposit("monitoring", "2026-01-01"), tmp_path, SALT)
        assert not hasattr(resultat, "src_sha256"), \
            "le résultat porte encore une empreinte, donc une relecture"


class TestJointureEntreFlux:
    """La propriété qui fait tenir tout le modèle."""

    def test_meme_patient_meme_cle_dans_les_deux_fichiers(self, tmp_path):
        """patients.csv et sejours.csv portent le même patient_id en clair.
        S'ils ne produisaient pas la même clé, la jointure serait rompue et
        aucun indicateur par patient ne serait calculable."""
        patients = ingest(_deposit("patients", "2026-01-01"), tmp_path, SALT)
        sejours = ingest(_deposit("sejours", "2026-01-01"), tmp_path, SALT)

        cles_patients = {r["patient_key"]
                         for r in csv.DictReader(patients.lake_path.open(encoding="utf-8"))}
        cles_sejours = {r["patient_key"]
                        for r in csv.DictReader(sejours.lake_path.open(encoding="utf-8"))}

        assert cles_sejours, "aucune clé côté séjours"
        assert cles_sejours <= cles_patients, \
            f"séjours orphelins : {cles_sejours - cles_patients}"

    def test_le_sejour_ne_porte_plus_d_identifiant_en_clair(self, tmp_path):
        resultat = ingest(_deposit("sejours", "2026-01-01"), tmp_path, SALT)
        contenu = resultat.lake_path.read_text(encoding="utf-8")
        assert "IPP9000001" not in contenu
        entete = next(csv.reader(resultat.lake_path.open(encoding="utf-8")))
        assert "patient_id" not in entete and "patient_key" in entete


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
