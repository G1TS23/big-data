"""Assemble les documents du dépôt en un rapport PDF autonome.

Le rendu attend DEUX livrables : le dépôt, et un rapport lisible sans lui.
Ce script produit le second à partir des trois documents versionnés, images
comprises, de sorte qu'il ne puisse pas diverger de ce que le dépôt contient.

    python docs/outils/rapport.py [destination.pdf]

Sans argument, écrit ../rendu/rapport-eds-chu.pdf — hors du dépôt, le rapport
étant un livrable distinct.

Le PDF est imprimé par Chrome en mode « headless » : c'est le seul moteur
présent sur la machine capable de rendre les schémas Mermaid, qui seraient
sinon perdus. Le HTML intermédiaire est conservé à côté du PDF.
"""
from __future__ import annotations

import base64
import mimetypes
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from markdown_min import convertir  # noqa: E402

RACINE = Path(__file__).resolve().parents[2]
DOCS = RACINE / "docs"

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# Ordre du rapport. Le dossier d'abord — c'est la partie 1 attendue — puis
# l'exploitation, puis la validation en annexe : elle justifie les chiffres des
# deux précédentes et n'a pas à être lue en premier.
#
# Un document ABSENT est simplement sauté. C'est ce qui permet à un chapitre
# optionnel — le passage au cloud, travaillé sur une branche — d'entrer dans le
# rapport sans que le générateur change, et d'en sortir sans laisser de trace si
# ce travail n'aboutit pas.
CHAPITRES_POSSIBLES = [
    ("DOSSIER.md", "dossier"),
    ("EXPLOITATION.md", "exploitation"),
    ("CLOUD.md", "cloud"),
    ("VALIDATION.md", "validation"),
]

CHAPITRES = [(f, p) for f, p in CHAPITRES_POSSIBLES if (Path(__file__).resolve()
              .parents[2] / "docs" / f).is_file()]

STYLE = """
@page { size: A4; margin: 18mm 16mm 20mm 16mm; }
body { font: 10.5pt/1.55 -apple-system, "Helvetica Neue", Arial, sans-serif;
       color: #1c2530; margin: 0; }
h1, h2, h3, h4 { color: #0d366b; line-height: 1.25; }
h1 { font-size: 20pt; margin: 0 0 .6em; break-before: page; }
h1.garde, h1.premier { break-before: avoid; }
h2 { font-size: 14.5pt; margin: 1.6em 0 .5em;
     border-bottom: 1px solid #d8e0ea; padding-bottom: .18em; }
h3 { font-size: 12pt; margin: 1.3em 0 .4em; }
h4 { font-size: 10.5pt; margin: 1.1em 0 .3em; }
h2, h3, h4 { break-after: avoid; }
/* Une capture ne doit pas se séparer du titre qui l'annonce. Le titre retient
   déjà le paragraphe qui le suit ; ces deux règles prolongent la chaîne
   jusqu'à l'image, faute de quoi la coupure tombe entre l'introduction et la
   capture qu'elle introduit. */
p:has(+ p > img) { break-after: avoid; }
p:has(> img) { break-before: avoid; break-inside: avoid; }
.figure { break-inside: avoid; }
p { margin: .55em 0; orphans: 3; widows: 3; }
a { color: #185f9c; text-decoration: none; }
code { font: 9pt/1.4 "SF Mono", Menlo, monospace; background: #f2f5f8;
       padding: .08em .3em; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #e2e8f0; border-radius: 5px;
      padding: .7em .9em; overflow: hidden; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.6pt; }
table { border-collapse: collapse; width: 100%; margin: .8em 0;
        font-size: 9.2pt; break-inside: avoid; }
th, td { border: 1px solid #dde4ec; padding: .34em .55em; vertical-align: top; }
th { background: #eef3f8; color: #0d366b; font-weight: 600; }
tbody tr:nth-child(even) { background: #fafcfe; }
blockquote { margin: .9em 0; padding: .55em .95em; background: #f4f7fb;
             border-left: 3px solid #2a78d6; break-inside: avoid; }
blockquote p { margin: 0; }
img { max-width: 100%; display: block; margin: 1em auto;
      border: 1px solid #dde4ec; border-radius: 4px; break-inside: avoid; }
hr.separateur { border: none; border-top: 1px solid #e6ecf3; margin: 1.6em 0; }
ul, ol { margin: .5em 0 .5em 1.1em; padding-left: .7em; }
li { margin: .22em 0; }
.mermaid { background: none; border: none; text-align: center; padding: 0;
           break-inside: avoid; margin: 1.1em 0; }
.mermaid svg { max-width: 100%; height: auto; }

.garde-page { break-after: page; padding-top: 22mm; }
.garde-page h1 { font-size: 30pt; border: none; margin-bottom: .15em; }
.garde-page .sous { font-size: 13pt; color: #4a5a6d; margin-bottom: 2.4em; }
.garde-page dl { display: grid; grid-template-columns: 34mm 1fr;
                 gap: .45em 0; font-size: 10.5pt; margin-top: 2em; }
.garde-page dt { color: #6b7a8d; }
.garde-page dd { margin: 0; }
.garde-page .note { margin-top: 3em; font-size: 9.5pt; color: #6b7a8d;
                    border-top: 1px solid #dde4ec; padding-top: .9em; }

.sommaire { break-after: page; }
.sommaire h1 { break-before: avoid; }
.sommaire ol { list-style: none; margin: 0; padding: 0; }
.sommaire > ol > li { margin: .55em 0 .12em; font-weight: 600; color: #0d366b; }
.sommaire ol ol { margin: .1em 0 0 0; padding-left: 1.1em; font-weight: 400; }
.sommaire ol ol li { margin: .05em 0; font-size: 9.3pt; }
.sommaire li.partie { margin: .32em 0 .1em; font-weight: 600;
                      font-size: 10pt; color: #245a94; }
.sommaire li.partie > ol { font-weight: 400; }
.sommaire a { color: inherit; }
"""


def incorporer_images(page: str) -> str:
    """Remplace chaque image par son contenu, encodé en data URI.

    Deux raisons. Le rapport devient autonome : un seul fichier, qui s'ouvre
    n'importe où. Et surtout, plus aucun chemin absolu n'y figure — la balise
    <base> portait le répertoire personnel de l'auteur, ce qui contredit un
    rendu anonyme.
    """
    def remplacer(m: re.Match) -> str:
        fichier = DOCS / m.group(1)
        if not fichier.is_file():
            return m.group(0)
        type_mime = mimetypes.guess_type(fichier.name)[0] or "application/octet-stream"
        donnees = base64.b64encode(fichier.read_bytes()).decode("ascii")
        return f'src="data:{type_mime};base64,{donnees}"'

    return re.sub(r'src="([^":]+)"', remplacer, page)


def relier(html_chapitre: str) -> str:
    """Change les liens entre documents en ancres internes au rapport.

    Le rapport doit se lire seul : un lien vers VALIDATION.md#… n'y mènerait
    nulle part, alors que la section s'y trouve, quelques pages plus loin.
    """
    import re

    fichiers = {fichier: prefixe for fichier, prefixe in CHAPITRES}

    def cible(m: re.Match) -> str:
        fichier, fragment = m.group(1), m.group(2)
        prefixe = fichiers.get(fichier)
        if prefixe is None:
            return m.group(0)
        return f'href="#{prefixe}-{fragment[1:]}"' if fragment else f'href="#{prefixe}-titre"'

    return re.sub(r'href="([A-Z][A-Za-z_]*\.md)(#[^"]*)?"', cible, html_chapitre)


def sommaire(chapitres: list[tuple[str, str, str]]) -> str:
    """Un sommaire à trois niveaux, construit depuis les titres rencontrés.

    Un document peut porter des titres de niveau 1 INTERNES — « Partie 1 »,
    « Partie 2 » — qui ne sont pas son titre. Les ignorer aplatirait le
    sommaire : les onze sections du dossier et les sept leçons s'y
    aligneraient sans qu'on voie où l'une finit et l'autre commence.
    """
    import re

    titres = re.compile(r'<h([12]) id="([^"]+)">(.*?)</h\1>', re.S)

    out = ['<section class="sommaire"><h1 class="premier">Sommaire</h1><ol>']
    for titre, prefixe, html_chapitre in chapitres:
        out.append(f'<li><a href="#{prefixe}-titre">{titre}</a><ol>')
        groupe_ouvert = False
        for m in titres.finditer(html_chapitre):
            niveau, ancre, texte = int(m.group(1)), m.group(2), re.sub(r"<[^>]+>", "", m.group(3))
            if ancre == f"{prefixe}-titre":
                continue                       # le titre du chapitre, déjà posé
            if niveau == 1:
                if groupe_ouvert:
                    out.append("</ol></li>")
                out.append(f'<li class="partie"><a href="#{ancre}">{texte}</a><ol>')
                groupe_ouvert = True
            else:
                out.append(f'<li><a href="#{ancre}">{texte}</a></li>')
        if groupe_ouvert:
            out.append("</ol></li>")
        out.append("</ol></li>")
    out.append("</ol></section>")
    return "".join(out)


_FIGURE = re.compile(
    r"(<p>(?:(?!</p>).)*?</p>)\s*(<p><img\b(?:(?!</p>).)*?</p>)", re.S)


def souder_figures(html: str) -> str:
    """Colle le paragraphe d'introduction à la capture qu'il introduit.

    « break-after: avoid » n'est qu'une indication : quand la place manque en
    bas de page, le moteur coupe quand même et la capture part seule sur la
    page suivante. Réunir les deux dans un bloc insécable déplace l'ensemble,
    ce qui est le comportement voulu — on lit l'annonce et l'image d'un seul
    tenant.
    """
    return _FIGURE.sub(r'<div class="figure">\1\2</div>', html)


def construire_html() -> str:
    chapitres = []
    for fichier, prefixe in CHAPITRES:
        source = (DOCS / fichier).read_text(encoding="utf-8")
        corps = convertir(source, prefixe_ancre=f"{prefixe}-")
        corps = relier(corps)
        corps = souder_figures(corps)
        # Le premier titre du document devient l'ancre du chapitre.
        corps = corps.replace("<h1 id=", f'<h1 id="{prefixe}-titre" data-ancienne=', 1)
        titre = source.splitlines()[0].lstrip("# ").strip()
        chapitres.append((titre, prefixe, corps))

    garde = f"""<section class="garde-page">
<h1 class="garde">Entrepôt de Données de Santé</h1>
<div class="sous">Centre Hospitalier Universitaire — rapport de projet</div>
<dl>
  <dt>Module</dt><dd>Big Data · M2</dd>
  <dt>Épreuve</dt><dd>E05 — BC05, compétences C27 à C31</dd>
  <dt>Livrables</dt><dd>Partie 1 — interface d'analyse<br>Partie 2 — automatisation</dd>
  <dt>Date</dt><dd>{date.today():%d/%m/%Y}</dd>
</dl>
<div class="note">Ce rapport reprend les documents versionnés avec le code.
Il se lit seul ; le dépôt, livré à part, permet de tout rejouer — les chiffres
cités y sont reproductibles par <code>eds run</code> puis
<code>python docs/outils/reconcilier.py</code>.</div>
</section>"""

    corps = "\n".join(html for _, _, html in chapitres)
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<title>EDS CHU — rapport de projet</title>
<style>{STYLE}</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
  mermaid.initialize({{ startOnLoad: true, theme: 'neutral',
                        themeVariables: {{ fontSize: '15px' }} }});
</script>
</head><body>
{garde}
{sommaire(chapitres)}
{corps}
</body></html>"""


def main() -> int:
    if not CHROME.exists():
        print(f"Chrome introuvable : {CHROME}", file=sys.stderr)
        print("C'est le seul moteur local capable de rendre les schémas Mermaid.",
              file=sys.stderr)
        return 1

    destination = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else RACINE.parent / "rendu" / "rapport-eds-chu.pdf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # resolve() APRÈS la création : Chrome reçoit un file:// et un chemin
    # relatif ne s'y exprime pas. La destination peut être donnée en relatif
    # depuis le Makefile, d'où la normalisation ici plutôt qu'à l'appel.
    destination = destination.resolve()

    page = destination.with_suffix(".html")
    page.write_text(incorporer_images(construire_html()), encoding="utf-8", newline="")

    # virtual-time-budget : Mermaid dessine ses schémas après le chargement.
    # Sans ce délai, Chrome imprimerait les blocs encore vides.
    subprocess.run([str(CHROME), "--headless=new", "--disable-gpu",
                    "--no-pdf-header-footer", "--virtual-time-budget=20000",
                    f"--print-to-pdf={destination}", page.as_uri()],
                   check=True, capture_output=True)

    if not destination.exists():
        print("Chrome n'a produit aucun fichier.", file=sys.stderr)
        return 1
    poids = destination.stat().st_size / 1_048_576
    print(f"{destination}  ({poids:.1f} Mo)")
    print(f"{page}  (source HTML, conservée)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
