"""Profil de préférences applicatives par équipier (App Comptoir).

L'App Comptoir remonte les réglages PERSONNELS d'un équipier (raccourcis,
notifications, disposition, volume…) et les redescend à la demande : un
équipier qui change de PC retrouve son environnement. La politique de
récupération (« jamais / sur demande / automatique ») voyage DANS le profil :
elle suit la personne, pas le poste.

  GET  /api/pyside/staff_preferences/<staff_id>
       -> 200 {payload, schema_version, updated_at}
       -> 404 {"error": "staff_unknown"}   (équipier inexistant)
       -> 404 {"error": "no_preferences"}  (aucun profil enregistré)
  PUT  /api/pyside/staff_preferences/<staff_id>
       <- {"payload": {...}, "schema_version": int}
       -> 200 {profil enregistré} | 400 | 404 staff inconnu | 413 trop gros

Garde ``require_app_token`` (clients machine uniquement) : ces données
appartiennent au flux App↔serveur, pas à la console d'administration web.
L'identité du poste n'est jamais stockée ici (ni URL, ni comptoir, ni
secret) : le payload ne contient que des préférences personnelles.
"""

import json

from flask import Blueprint, jsonify, request

from auth_utils import require_app_token
from models import db, Pharmacist, StaffPreference

staff_prefs_bp = Blueprint("pyside_staff_prefs", __name__)

# Borne de prudence : un profil reste un petit dict de réglages — un corps
# volumineux est rejeté avant tout stockage.
_MAX_PAYLOAD_BYTES = 64 * 1024


def _staff_or_404(staff_id):
    """Retourne l'équipier ou une réponse 404 prête à renvoyer."""
    if db.session.get(Pharmacist, staff_id) is None:
        return jsonify({"error": "staff_unknown"}), 404
    return None


@staff_prefs_bp.route(
    "/api/pyside/staff_preferences/<int:staff_id>", methods=["GET"])
@require_app_token
def get_staff_preferences(staff_id):
    missing = _staff_or_404(staff_id)
    if missing is not None:
        return missing
    row = StaffPreference.query.filter_by(staff_id=staff_id).first()
    if row is None:
        return jsonify({"error": "no_preferences"}), 404
    return jsonify(row.to_dict())


@staff_prefs_bp.route(
    "/api/pyside/staff_preferences/<int:staff_id>", methods=["PUT"])
@require_app_token
def put_staff_preferences(staff_id):
    missing = _staff_or_404(staff_id)
    if missing is not None:
        return missing
    data = request.get_json(silent=True) or {}
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return jsonify({"error": "payload_must_be_object"}), 400
    if len(json.dumps(payload)) > _MAX_PAYLOAD_BYTES:
        return jsonify({"error": "payload_too_large"}), 413
    try:
        schema_version = max(1, int(data.get("schema_version", 1)))
    except (TypeError, ValueError):
        schema_version = 1
    row = StaffPreference.query.filter_by(staff_id=staff_id).first()
    if row is None:
        row = StaffPreference(staff_id=staff_id)
        db.session.add(row)
    row.payload = payload
    row.schema_version = schema_version
    db.session.commit()
    return jsonify(row.to_dict())
