from functools import wraps

from flask import request, jsonify, make_response, current_app as app
from sqlalchemy.exc import IntegrityError

from models import db, IdempotencyKey


def _reserve_or_get(key, counter_id):
    """ Réserve la clé pour cette exécution.

    Retourne None si la clé est nouvelle (réservée avec succès -> l'appelant doit
    exécuter l'action), ou la ligne existante si la clé a déjà été vue (rejeu).
    La réservation repose sur la clé primaire : deux requêtes concurrentes
    portant la même clé ne peuvent pas toutes deux l'insérer. """
    existing = IdempotencyKey.query.get(key)
    if existing is not None:
        return existing

    db.session.add(IdempotencyKey(key=key, counter_id=counter_id))
    try:
        db.session.commit()
        return None
    except IntegrityError:
        # Course entre deux requêtes portant la même clé : l'autre a réservé en
        # premier. On récupère sa ligne pour renvoyer sa réponse (ou signaler
        # qu'elle est encore en cours).
        db.session.rollback()
        return IdempotencyKey.query.get(key)


def idempotent(view):
    """ Rend une route idempotente via l'en-tête X-Idempotency-Key.

    Sans en-tête, comportement inchangé. Avec en-tête, la première requête
    exécute l'action et mémorise sa réponse ; toute requête ultérieure portant la
    même clé renvoie la réponse mémorisée sans ré-exécuter l'action. Utile pour
    les commandes non rejouables comme « appeler le suivant » : un renvoi réseau
    ou une relance automatique après un 401 ne doit pas faire avancer la file
    deux fois.

    Tolérant aux pannes : si le magasin d'idempotence est indisponible (table
    absente, base injoignable...), la vue s'exécute normalement plutôt que de
    bloquer une fonctionnalité centrale. """
    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get("X-Idempotency-Key")
        if not key:
            return view(*args, **kwargs)

        try:
            existing = _reserve_or_get(key, kwargs.get("counter_id"))
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Idempotence indisponible, exécution normale: {e}")
            return view(*args, **kwargs)

        if existing is not None:
            if existing.status_code is None:
                # La première exécution portant cette clé est encore en cours.
                return jsonify({"error": "in_progress"}), 409
            resp = make_response(existing.response_body or "", existing.status_code)
            if existing.content_type:
                resp.headers["Content-Type"] = existing.content_type
            return resp

        # Clé nouvelle : on exécute réellement l'action puis on mémorise le
        # résultat pour les rejeux éventuels.
        resp = make_response(view(*args, **kwargs))
        try:
            row = IdempotencyKey.query.get(key)
            if row is not None:
                row.response_body = resp.get_data(as_text=True)
                row.status_code = resp.status_code
                row.content_type = resp.headers.get("Content-Type")
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Enregistrement du résultat idempotent impossible: {e}")
        return resp

    return wrapper
