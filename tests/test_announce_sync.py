"""Écran d'annonce — resynchronisation au connect et indicateur de fraîcheur.

Régressions verrouillées (audit « écran peut rester périmé 60 s ») :

1. La première connexion Socket.IO ne faisait que journaliser : un appel émis
   entre le rendu HTML et l'ouverture du socket restait invisible jusqu'à la
   prochaine reconnexion ou la passe périodique de 60 s. ``syncCallList()``
   est désormais lancée à CHAQUE connexion, initiale comprise.
2. ``fetch('/announce/state')`` ne contrôlait pas ``response.ok`` : un 401
   (SECURITY_LOGIN_SCREEN + session expirée) ou une redirection login était
   traité comme un état valide, ou plantait en ``response.json()`` sans trace
   exploitable. Le statut est vérifié explicitement.
3. Deux sources appellent ``syncCallList()`` (connect × 2 sockets, reconnect,
   trou de révision, passe 60 s) : une garde ``syncEnCours`` interdit les
   requêtes concurrentes.
4. Les échecs n'étaient visibles qu'en console sur un écran non surveillé : un
   badge ``#sync_status`` signale désormais « données non actualisées » avec
   l'heure de la dernière synchronisation réussie.
"""

import os
import re

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _handler(source, signature):
    """Corps du handler enregistré par ``signature`` (jusqu'à son ``});``)."""
    debut = source.index(signature)
    fin = source.index("});", debut)
    return source[debut:fin]


# --- 1. Resynchronisation à chaque connexion ---------------------------------

def test_sync_au_connect_socket_general():
    source = _read("static/js/announce.js")
    corps = _handler(source, "generalSocket.on('connect'")
    assert "syncCallList()" in corps, (
        "la connexion du socket général doit déclencher syncCallList()"
    )


def test_sync_au_connect_socket_ecran():
    source = _read("static/js/announce.js")
    corps = _handler(source, "screenSocket.on('connect'")
    assert "syncCallList()" in corps, (
        "la connexion du socket écran doit déclencher syncCallList()"
    )


# --- 2. Contrôle explicite de la réponse -------------------------------------

def test_sync_verifie_response_ok_et_401():
    source = _read("static/js/announce.js")
    debut = source.index("function syncCallList()")
    corps = source[debut:]
    assert "response.ok" in corps, "response.ok n'est pas contrôlé"
    assert "401" in corps, "le cas 401 n'est pas traité explicitement"


# --- 3. Pas de resynchronisations concurrentes --------------------------------

def test_sync_garde_anti_concurrence():
    source = _read("static/js/announce.js")
    assert "syncEnCours" in source
    corps = source[source.index("function syncCallList()"):]
    assert re.search(r"if \(syncEnCours\) \{\s*return;", corps), (
        "syncCallList doit sortir sans requête si une resync est en cours"
    )
    assert ".finally(" in corps and "syncEnCours = false" in corps, (
        "le drapeau doit être relâché dans un finally (succès ET échec)"
    )


# --- 4. Indicateur visible ----------------------------------------------------

def test_gabarit_porte_le_badge_de_fraicheur():
    contenu = _read("templates/announce/announce.html")
    assert 'id="sync_status"' in contenu
    assert 'role="alert"' in contenu


def test_js_alimente_le_badge():
    source = _read("static/js/announce.js")
    assert "getElementById('sync_status')" in source
    # L'heure de la dernière synchronisation réussie doit être affichée.
    assert "derniereSyncOk" in source


def test_css_du_badge_present():
    contenu = _read("static/css/display.css")
    assert "#sync_status" in contenu


def test_coupures_socket_remarquees():
    """Les handlers disconnect/connect_error mettent à jour l'indicateur."""
    source = _read("static/js/announce.js")
    for nom, handler in (("generalSocket", "noteSocketDisconnected('general')"),
                         ("screenSocket", "noteSocketDisconnected('screen')")):
        corps = _handler(source, f"{nom}.on('disconnect'")
        assert handler in corps, f"{nom} : disconnect ne met pas à jour le badge"
