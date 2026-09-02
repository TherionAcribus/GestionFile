"""Couche service : la logique métier, séparée des vues et du temps réel.

Les blueprints y délèguent au lieu de faire eux-mêmes requêtes SQL et émissions
SocketIO. Objectif : un seul propriétaire par flux métier, testable sans client
HTTP.
"""
