"""Provisionnement de la couche de restitution.

Metabase en édition communautaire n'a pas d'export de sérialisation : ses
tableaux de bord vivent dans un volume Docker, invisible du dépôt. Les
construire par l'API plutôt qu'à la souris les rend VERSIONNÉS et REJOUABLES —
un correcteur qui clone le projet et lance `eds metabase` retrouve exactement
les mêmes écrans.

C'est aussi de l'automatisation au sens propre : provisionner la restitution
fait partie du pipeline, au même titre que l'ingestion.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import urllib3
import yaml

from eds.config import ROOT, Settings

log = logging.getLogger("eds.metabase")

DASHBOARDS = ROOT / "config" / "dashboards.yml"
SQL_DASHBOARDS = ROOT / "sql" / "dashboards"


class MetabaseError(RuntimeError):
    """Erreur d'API. `status` porte le code HTTP, qui distingue un refus de
    droits (403) d'une panne (5xx) — deux situations qu'un contrôle de
    cloisonnement ne doit surtout pas confondre."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


class Metabase:
    """Client minimal de l'API Metabase, limité à ce que le projet provisionne."""

    def __init__(self, settings: Settings):
        self.base = settings.metabase_url
        self.settings = settings
        self.http = urllib3.PoolManager()
        self.token: str | None = None

    # ── transport ───────────────────────────────────────────────────────────
    def _call(self, method: str, route: str, body: Any = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Metabase-Session"] = self.token
        response = self.http.request(
            method, f"{self.base}{route}", headers=headers,
            body=json.dumps(body).encode("utf-8") if body is not None else None)
        payload = response.data.decode("utf-8", "replace")
        if response.status >= 400:
            raise MetabaseError(f"{method} {route} → {response.status} : {payload[:400]}",
                                status=response.status)
        return json.loads(payload) if payload.strip() else None

    def get(self, route):            return self._call("GET", route)
    def post(self, route, body):     return self._call("POST", route, body)
    def put(self, route, body):      return self._call("PUT", route, body)

    # ── session ─────────────────────────────────────────────────────────────
    def connect(self) -> None:
        """Ouvre une session : première configuration si l'instance est vierge."""
        props = self.get("/api/session/properties")
        if not props.get("has-user-setup"):
            self._premiere_configuration(props["setup-token"])
        else:
            self.token = self.post("/api/session", {
                "username": self.settings.metabase_email,
                "password": self.settings.metabase_password})["id"]
            log.info("session ouverte", extra={"compte": self.settings.metabase_email})

    def _premiere_configuration(self, token: str) -> None:
        self.token = self.post("/api/setup", {
            "token": token,
            "user": {"first_name": "EDS", "last_name": "Admin",
                     "email": self.settings.metabase_email,
                     "password": self.settings.metabase_password,
                     "site_name": "EDS — CHU"},
            "prefs": {"site_name": "EDS — CHU", "site_locale": "fr",
                      "allow_tracking": False},
        })["id"]
        log.info("instance configurée", extra={"compte": self.settings.metabase_email})

    # ── objets, créés de façon idempotente ──────────────────────────────────
    def _trouver(self, route: str, nom: str, cle: str = "name") -> dict | None:
        objets = self.get(route)
        objets = objets.get("data", objets) if isinstance(objets, dict) else objets
        return next((o for o in objets if o.get(cle) == nom), None)

    def ensure_database(self, nom: str, base: str, utilisateur: str, mot_de_passe: str) -> int:
        details = {
            # Vu depuis le conteneur Metabase, ClickHouse porte son nom de service.
            "host": self.settings.ch_docker_host,
            "port": self.settings.ch_port,
            "user": utilisateur,
            "password": mot_de_passe,
            "dbname": base,
            "ssl": False,
        }
        existant = self._trouver("/api/database", nom)
        if existant:
            self.put(f"/api/database/{existant['id']}", {"name": nom, "engine": "clickhouse",
                                                         "details": details})
            return existant["id"]
        cree = self.post("/api/database", {"name": nom, "engine": "clickhouse",
                                           "details": details, "is_full_sync": True})
        return cree["id"]

    def ensure_group(self, nom: str) -> int:
        existant = self._trouver("/api/permissions/group", nom)
        return existant["id"] if existant else self.post("/api/permissions/group",
                                                         {"name": nom})["id"]

    def ensure_collection(self, nom: str, description: str) -> int:
        existant = self._trouver("/api/collection", nom)
        if existant:
            return existant["id"]
        return self.post("/api/collection", {"name": nom, "description": description,
                                             "parent_id": None})["id"]

    def cloisonner(self, autorisations: dict[int, int]) -> None:
        """N'accorde à chaque groupe que SA base, et referme le reste.

        Le vrai mur est côté ClickHouse : chaque connexion emprunte un compte
        distinct, aux droits restreints, ce que `eds acces` démontre. Ce réglage
        aligne l'interface sur ce mur.

        Deux limites de l'édition communautaire, à connaître :
          — le niveau « blocked » de view-data est réservé aux licences payantes,
            on ne peut donc que refuser la CRÉATION de requêtes ;
          — le groupe « All Users » contient tout le monde et reçoit par défaut
            un accès natif à toutes les bases. Le refermer est l'opération la
            plus importante ici : sans cela, tous les réglages par groupe qui
            suivent seraient sans effet, Metabase retenant la permission la plus
            large parmi les groupes d'un utilisateur.
        """
        graphe = self.get("/api/permissions/graph")
        bases = [d["id"] for d in self.get("/api/database")["data"]]
        tous = next(g["id"] for g in self.get("/api/permissions/group")
                    if g["name"] == "All Users")

        ferme = {"view-data": "unrestricted", "create-queries": "no",
                 "download": {"schemas": "none"}}
        ouvert = {"view-data": "unrestricted", "create-queries": "query-builder-and-native",
                  "download": {"schemas": "full"}}

        graphe["groups"].setdefault(str(tous), {})
        for base_id in bases:
            graphe["groups"][str(tous)][str(base_id)] = dict(ferme)

        for groupe_id, base_autorisee in autorisations.items():
            entree = graphe["groups"].setdefault(str(groupe_id), {})
            for base_id in bases:
                entree[str(base_id)] = dict(ouvert if base_id == base_autorisee else ferme)

        self.put("/api/permissions/graph", graphe)

    def cloisonner_collections(self, autorisations: dict[int, int]) -> None:
        """Chaque groupe ne voit que sa collection ; personne ne voit la racine."""
        graphe = self.get("/api/collection/graph")
        collections = [str(c["id"]) for c in self.get("/api/collection")
                       if not c.get("is_personal") and c["id"] != "root"]
        administrateurs = {g["id"] for g in self.get("/api/permissions/group")
                           if g["name"] == "Administrators"}

        for groupe_id_str, entree in graphe["groups"].items():
            if int(groupe_id_str) in administrateurs:
                continue
            autorisee = autorisations.get(int(groupe_id_str))
            entree["root"] = "none"
            for collection_id in collections:
                entree[collection_id] = "read" if collection_id == str(autorisee) else "none"
        self.put("/api/collection/graph", graphe)

    def ensure_utilisateur(self, email: str, prenom: str, nom: str,
                           mot_de_passe: str, groupe_id: int) -> int:
        """Un compte de démonstration par usage, membre de son seul groupe."""
        existant = next((u for u in self.get("/api/user")["data"] if u["email"] == email), None)
        if existant:
            self.put(f"/api/user/{existant['id']}",
                     {"first_name": prenom, "last_name": nom, "email": email,
                      "user_group_memberships": [{"id": 1}, {"id": groupe_id}]})
            return existant["id"]
        cree = self.post("/api/user", {
            "first_name": prenom, "last_name": nom, "email": email,
            "password": mot_de_passe,
            "user_group_memberships": [{"id": 1}, {"id": groupe_id}]})
        return cree["id"]

    def ensure_card(self, spec: dict, database_id: int, collection_id: int) -> int:
        requete = (SQL_DASHBOARDS / spec["sql"]).read_text(encoding="utf-8")
        corps = {
            "name": spec["titre"],
            # Metabase refuse une description vide : il faut l'omettre, pas
            # envoyer une chaîne vide.
            "description": spec.get("description") or None,
            "display": spec["forme"],
            "visualization_settings": spec.get("affichage", {}),
            "dataset_query": {"type": "native", "database": database_id,
                              "native": {"query": requete}},
            "collection_id": collection_id,
        }
        existant = self._trouver(f"/api/collection/{collection_id}/items?models=card",
                                 spec["titre"])
        if existant:
            self.put(f"/api/card/{existant['id']}", corps)
            return existant["id"]
        return self.post("/api/card", corps)["id"]

    def ensure_dashboard(self, nom: str, description: str, collection_id: int) -> int:
        existant = self._trouver(f"/api/collection/{collection_id}/items?models=dashboard", nom)
        if existant:
            return existant["id"]
        return self.post("/api/dashboard", {"name": nom, "description": description,
                                            "collection_id": collection_id})["id"]

    def poser_cartes(self, dashboard_id: int, cartes: list[dict]) -> None:
        """Place les cartes sur la grille de 24 colonnes du tableau de bord."""
        dashcards = []
        for index, carte in enumerate(cartes):
            place = dict(carte)
            place["id"] = -(index + 1)            # identifiant négatif = création
            dashcards.append(place)
        self.put(f"/api/dashboard/{dashboard_id}", {"dashcards": dashcards})


def charger_specification(path: Path | None = None) -> dict:
    with open(path or DASHBOARDS, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ── Traduction de la spécification en réglages Metabase ─────────────────────

ALIAS = re.compile(r'\bAS\s+"([^"]+)"', re.IGNORECASE)


def colonnes(sql: str) -> list[str]:
    """Colonnes d'une requête de carte, dans l'ordre.

    Toutes les requêtes de `sql/dashboards/` nomment leurs colonnes avec
    `AS "…"` : la première est l'axe, les suivantes sont les mesures.
    """
    return ALIAS.findall(sql)


def affichage(spec: dict, sql: str, palette: dict) -> dict:
    """Construit les visualization_settings d'une carte."""
    cols = colonnes(sql)
    reglages: dict[str, Any] = dict(spec.get("affichage") or {})
    forme = spec["forme"]

    if spec.get("description"):
        reglages["card.description"] = spec["description"]

    if forme == "scalar":
        if cols:
            reglages.setdefault("scalar.field", cols[0])
        return reglages

    if forme == "table":
        return reglages

    if not cols:
        return reglages

    mesures = spec.get("series") or ([spec["serie"]] if spec.get("serie") else cols[1:])
    reglages["graph.dimensions"] = [cols[0]]
    reglages["graph.metrics"] = mesures

    # Étiquettes de valeur : elles tiennent lieu de second encodage pour les
    # teintes dont le contraste passe sous 3:1 sur fond clair.
    if spec.get("valeurs"):
        reglages["graph.show_values"] = True
    if spec.get("empile"):
        reglages["stackable.stack_type"] = "stacked"
    if spec.get("axe_x"):
        reglages["graph.x_axis.title_text"] = spec["axe_x"]
    if spec.get("axe_y"):
        reglages["graph.y_axis.title_text"] = spec["axe_y"]

    # Les teintes sont assignées dans l'ordre fixe de la palette, jamais cyclées :
    # une même entité garde sa couleur d'une carte à l'autre.
    if spec.get("couleur"):
        reglages["series_settings"] = {mesures[0]: {"color": palette[spec["couleur"]]}}
    elif len(mesures) > 1:
        slots = [palette[f"serie_{i}"] for i in range(1, len(palette) + 1)]
        reglages["series_settings"] = {
            nom: {"color": slots[index]} for index, nom in enumerate(mesures[:len(slots)])}
    return reglages


def carte_texte(spec: dict) -> dict:
    """Encart de texte : ce n'est pas une carte, mais un élément de tableau."""
    return {
        "id": None, "card_id": None,
        "row": spec["row"], "col": spec["col"],
        "size_x": spec["size_x"], "size_y": spec["size_y"],
        "series": [], "parameter_mappings": [],
        "visualization_settings": {
            "virtual_card": {"name": None, "display": "text",
                             "visualization_settings": {}, "dataset_query": {},
                             "archived": False},
            "text": spec["texte"],
            "dashcard.background": False,
        },
    }


# ── Démonstration du cloisonnement, côté restitution ────────────────────────

def verifier_cloisonnement(settings: Settings) -> list[dict]:
    """Éprouve, compte par compte, ce que chaque usage peut réellement lire.

    Le mur décisif est côté ClickHouse — trois comptes aux droits distincts,
    ce que `eds acces` démontre séparément. Ce contrôle-ci vérifie que
    l'interface ne le contourne pas : un utilisateur ne doit pouvoir ouvrir ni
    les cartes d'un autre usage, ni une requête libre sur sa base.
    """
    spec = charger_specification()
    admin = Metabase(settings)
    admin.connect()

    cartes = {c["name"]: c["id"] for c in admin.get("/api/card")}
    bases = {d["name"]: d["id"] for d in admin.get("/api/database")["data"]}

    # Une carte témoin par usage, pour éprouver les accès croisés.
    temoins = {t["connexion"]: next(c["titre"] for c in t["cartes"] if "sql" in c)
               for t in spec["tableaux"]}

    def resultat(session, route: str, corps: dict) -> str:
        """Trois issues, à ne pas confondre.

        Un refus de droits arrive en 403. Une base injoignable arrive en 202
        avec un statut d'échec : le cloisonnement n'y est pour rien, et
        conclure « refusé » ferait passer une panne pour une protection.
        """
        try:
            reponse = session.post(route, corps)
        except MetabaseError as exc:
            return "refusé" if exc.status == 403 else "indisponible"
        return "autorisé" if reponse.get("status") == "completed" else "indisponible"

    constats = []
    for connexion in spec["connexions"]:
        demo = connexion.get("compte_demo")
        if not demo:
            continue
        session = Metabase(settings)
        session.token = session.post("/api/session", {
            "username": demo["email"], "password": settings.metabase_demo_password})["id"]

        essais = [(f"ouvrir « {titre} »", nom_connexion == connexion["nom"],
                   f"/api/card/{cartes[titre]}/query", {})
                  for nom_connexion, titre in temoins.items()]
        essais += [(f"requête libre sur {nom_base}", nom_base == connexion["nom"],
                    "/api/dataset", {"type": "native", "database": base_id,
                                     "native": {"query": "SELECT 1"}})
                   for nom_base, base_id in bases.items()]

        for action, attendu, route, corps in essais:
            obtenu = resultat(session, route, corps)
            constats.append({
                "compte": demo["email"], "action": action,
                "attendu": "autorisé" if attendu else "refusé",
                "obtenu": obtenu,
                "conforme": obtenu == ("autorisé" if attendu else "refusé"),
                "indisponible": obtenu == "indisponible"})
    return constats
