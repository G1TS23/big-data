"""Contrôles de la génération SQL du chargement lake → bronze.

Ces tests ne touchent pas ClickHouse : ils vérifient que l'orchestrateur
construit la bonne requête. Le moteur, lui, est vérifié par l'exécution réelle.
"""
import pytest

from eds.config import load_sources
from eds.loader import build_insert, split_columns


class TestDecoupageDuSchema:
    def test_colonnes_simples(self):
        assert split_columns("a String, b UInt8") == ["a", "b"]

    def test_types_parametres(self):
        assert split_columns("a Nullable(UInt16), b DateTime64(6)") == ["a", "b"]

    def test_type_imbrique_avec_virgules(self):
        """`Array(Tuple(x String, y String))` contient des virgules internes."""
        schema = "stay_id String, diagnostics Array(Tuple(code_cim10 String, type String))"
        assert split_columns(schema) == ["stay_id", "diagnostics"]

    def test_decimal_a_deux_parametres(self):
        assert split_columns("a Decimal(10, 2), b String") == ["a", "b"]


class TestRequeteDInsertion:
    BRONZE = {
        "table": "bronze.patients",
        "format": "CSVWithNames",
        "schema": "patient_key String, birth_year Nullable(UInt16)",
    }

    def test_colonnes_techniques_ajoutees(self):
        sql = build_insert(self.BRONZE)
        assert "(patient_key, birth_year, _source_file, _ingestion_date, _batch_id)" in sql

    def test_les_donnees_passent_par_input(self):
        """La lecture du fichier appartient au moteur, pas à Python."""
        sql = build_insert(self.BRONZE)
        assert "FROM input('patient_key String, birth_year Nullable(UInt16)')" in sql
        assert sql.endswith("FORMAT CSVWithNames")

    def test_valeurs_techniques_parametrees(self):
        """Les chemins et identifiants passent en paramètres, jamais concaténés."""
        sql = build_insert(self.BRONZE)
        assert "{f:String}, {d:Date}, {b:String}" in sql

    def test_transtypage_applique(self):
        sql = build_insert({**self.BRONZE, "schema": "ts DateTime64(6), v Int64",
                            "cast": {"ts": "toDateTime(ts)"}})
        assert "SELECT toDateTime(ts), v," in sql
        # la colonne cible garde son nom, seule l'expression source change
        assert "(ts, v, _source_file" in sql


class TestDeclarationDesFlux:
    """Chaque flux déclaré doit pouvoir produire une requête valide."""

    @pytest.mark.parametrize("nom", ["patients", "sejours", "diagnostics", "monitoring"])
    def test_flux_declare_une_cible_bronze(self, nom):
        spec = load_sources()[nom]
        assert "bronze" in spec, f"{nom} n'a pas de cible bronze"
        sql = build_insert(spec["bronze"])
        assert sql.startswith(f"INSERT INTO {spec['bronze']['table']} ")

    def test_les_referentiels_ont_chacun_leur_cible(self):
        fichiers = load_sources()["referentiels"]["files"]
        cibles = {f["bronze"]["table"] for f in fichiers}
        assert cibles == {"bronze.services", "bronze.cim10"}

    def test_schema_bronze_couvre_les_colonnes_du_lake(self):
        """Le schéma d'insertion doit décrire exactement les colonnes écrites
        dans le lake, sinon le fichier serait mal aligné à la lecture."""
        for nom in ("patients", "sejours"):
            spec = load_sources()[nom]
            assert split_columns(spec["bronze"]["schema"]) == spec["lake_columns"]
