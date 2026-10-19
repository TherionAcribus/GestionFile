"""Retour visuel des actions d'administration (« toasts ») et petits utilitaires.

Extrait d'``app.py`` (point 9.5d). Ces fonctions étaient greffées sur l'objet
application (``app.display_toast = display_toast``) pour être atteignables depuis
les blueprints via ``current_app`` — un contournement de la dépendance circulaire
que la fabrique d'application rend inutile. Elles s'importent désormais
normalement.

L'``after_request`` qui transforme le retour mémorisé en en-tête ``HX-Trigger``
reste enregistré dans ``app.py`` (il porte sur l'application entière, pas sur un
blueprint), mais la transformation elle-même vit ici
(``attach_admin_feedback``) afin d'être testable sur une application isolée —
``app.py`` n'est pas importable dans les tests.
"""

import json

from flask import current_app, g, has_request_context

from communication import communikation


def display_toast(success=True, message=None, broadcast=True):
    """Affiche le toast dans la page d'administration.

    Pour une validation réussie, on peut simplement appeler la fonction sans
    argument. Renvoie un ``("", 204)`` directement utilisable comme réponse de
    vue.

    ``broadcast=False`` désactive la diffusion WebSocket — qui informerait
    TOUS les administrateurs connectés — pour les actions strictement
    personnelles (parcours de configuration guidée). Le retour ne part alors
    que par l'en-tête ``HX-Trigger`` de la réponse, donc au seul auteur de la
    requête ; la clé ``toast`` ajoutée à l'acquittement demande au client
    d'afficher lui-même le toast visible, faute d'évènement WebSocket.
    """
    if message is None:
        message = "Enregistrement effectué"

    if broadcast:
        data = {"toast": True, 'success': success, 'message': message}
        communikation("admin", data)

    # Point 7.5 : en plus de la diffusion WebSocket (qui informe TOUS les
    # administrateurs), on mémorise le résultat pour l'auteur de la requête.
    # `attach_admin_feedback` (appelée par l'after_request de app.py) le
    # renvoie dans l'en-tête HX-Trigger, ce qui permet au client de confirmer
    # la sauvegarde à partir de la réponse HTTP — sans dépendre du WebSocket.
    # Sans diffusion (broadcast=False), 'toast' indique au client qu'il doit
    # afficher le toast visible lui-même.
    if has_request_context():
        g._admin_feedback = {'success': bool(success), 'message': message}
        if not broadcast:
            g._admin_feedback['toast'] = True

    return "", 204


def attach_admin_feedback(response):
    """Convertit le retour mémorisé (``g._admin_feedback``) en ``HX-Trigger``.

    Corps de ``app._attach_admin_feedback`` : l'enregistrement ``after_request``
    reste dans ``app.py`` (application entière), la transformation est ici pour
    être réutilisable par les applications de test isolées.
    """
    feedback = g.pop('_admin_feedback', None) if has_request_context() else None
    if feedback is None:
        return response

    trigger = {'adminFeedback': feedback}
    existing = response.headers.get('HX-Trigger')
    if existing:
        # Fusionne sans écraser un HX-Trigger déjà présent.
        try:
            merged = json.loads(existing)
            if not isinstance(merged, dict):
                raise ValueError
        except (ValueError, TypeError):
            merged = {existing: {}}
        merged.update(trigger)
        trigger = merged
    response.headers['HX-Trigger'] = json.dumps(trigger)
    return response


def allowed_image_file(filename):
    """Vérifie si le fichier a une extension autorisée.

    Conservée pour compatibilité ; les nouveaux uploads devraient utiliser
    ``image_storage.accept_image_upload`` (validation complète : extension +
    contenu + taille + nom unique).
    """
    from image_storage import ALLOWED_IMAGE_EXTENSIONS
    return ('.' in filename
            and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS)
