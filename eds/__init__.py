"""Entrepôt de Données de Santé — orchestrateur.

Python pilote : il copie les fichiers, pseudonymise à l'entrée du lake, et
envoie le SQL. Il ne calcule aucun indicateur — la transformation appartient
au moteur.
"""
__version__ = "0.1.0"
