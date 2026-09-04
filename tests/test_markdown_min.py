"""Contrôles du convertisseur Markdown employé pour fabriquer le rapport.

Il n'avait aucun test : sa refactorisation a été vérifiée en comparant sa sortie
sur les quatre documents du projet, ce qui est convaincant une fois mais ne garde
rien ensuite. Ces contrôles fixent le comportement de chaque type de bloc.

Les deux derniers consignent des DÉFAUTS connus plutôt que des garanties : ils
échoueront le jour où on les corrigera, ce qui est le but — un défaut consigné
se voit, un défaut tu se reproduit.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docs" / "outils"))

from markdown_min import convertir  # noqa: E402


class TestBlocs:
    def test_titre_recoit_une_ancre_prefixee(self):
        assert convertir("# Le titre", "doc-") == '<h1 id="doc-le-titre">Le titre</h1>'

    def test_paragraphe_recolle_les_lignes(self):
        assert convertir("une phrase\ncoupée en deux") == "<p>une phrase coupée en deux</p>"

    def test_liste_a_puces_et_liste_ordonnee(self):
        assert convertir("- a\n- b") == "<ul>\n<li>a</li>\n<li>b</li>\n</ul>"
        assert convertir("1. a") == "<ol>\n<li>a</li>\n</ol>"

    def test_ligne_indentee_prolonge_la_puce(self):
        assert convertir("- début\n  suite") == "<ul>\n<li>début suite</li>\n</ul>"

    def test_bloc_de_code_est_echappe(self):
        assert convertir("```\n<b>\n```") == "<pre><code>&lt;b&gt;</code></pre>"

    def test_mermaid_a_sa_propre_classe(self):
        assert convertir("```mermaid\nA-->B\n```") == '<pre class="mermaid">A--&gt;B</pre>'

    def test_citation(self):
        assert convertir("> tenue") == "<blockquote>tenue</blockquote>"

    def test_filet_horizontal(self):
        assert convertir("---") == '<hr class="separateur">'

    def test_tableau_respecte_les_alignements(self):
        html = convertir("| a | b |\n|---|--:|\n| 1 | 2 |")
        assert 'style="text-align:left"' in html
        assert 'style="text-align:right"' in html

    def test_code_en_ligne_n_est_pas_echappe_deux_fois(self):
        assert convertir("voir `<a>`") == "<p>voir <code>&lt;a&gt;</code></p>"


class TestDefautsConnus:
    """Ce que le convertisseur perd en silence. À corriger, pas à conserver."""

    def test_un_filet_sans_ligne_vide_avant_est_rendu_en_toutes_lettres(self):
        # Markdown standard en ferait un titre setext ; nous en faisons du texte.
        # C'est par ce chemin qu'un « --- » s'est affiché dans le rapport.
        assert convertir("phrase\n---") == "<p>phrase ---</p>"

    def test_une_ligne_de_tableau_sans_alignement_disparait(self):
        # Aucune règle ne la reconnaît, le paragraphe l'exclut : elle est
        # consommée sans rien produire, et sans le moindre avertissement.
        assert convertir("| a | b |") == ""
