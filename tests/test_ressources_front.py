"""Dépendances front : versions figées et chemins statiques corrects.

Deux régressions verrouillées ici.

**1. Aucune ressource externe sans version.** `templates/counter/counter.html`
chargeait ``https://unpkg.com/htmx.org`` — sans numéro de version. La page
recevait donc la dernière version publiée : le passage d'htmx 1.x à 2.x (rupture
d'API) pouvait casser le comptoir sans qu'une seule ligne du dépôt ne change.
Pire, l'extension ``ws.js`` d'htmx **1.9.12** était chargée à côté, et
``hx-ext="ws"`` n'apparaissait nulle part.

**2. Aucun chemin `../static/...`.** Ces chemins se résolvent par rapport à
l'URL servie, pas au fichier de gabarit : ils cassent dès qu'une route change de
profondeur. ``url_for('static', ...)`` est la seule forme correcte.
"""

import io
import os
import re

import pytest

_RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_GABARITS = os.path.join(_RACINE, "templates")

_REF = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
#: Une version VRAIMENT figée porte au moins majeure.mineure : `@11.2.10` ou
#: `/2.29.1/`. `@11` seul ne suffit pas — le CDN sert alors la dernière 11.x,
#: donc un contenu qui change dans le dos du dépôt (cas rencontré avec Swiper).
_VERSION = re.compile(r"@\d+\.\d+|/\d+\.\d+")

#: Hôtes dont l'URL n'a légitimement pas de version : ce sont des services, pas
#: des fichiers versionnés que l'on pourrait épingler.
_SANS_VERSION_ADMIS = (
    "fonts.googleapis.com",   # API de police : l'URL décrit une famille
    "fonts.gstatic.com",
    "sdk.scdn.co",            # SDK Spotify : doit venir de chez eux
)


def _lire(rel):
    return io.open(os.path.join(_RACINE, rel), encoding="utf-8").read()


def _gabarits():
    for base, _dirs, fichiers in os.walk(_GABARITS):
        for nom in sorted(fichiers):
            if nom.endswith(".html"):
                chemin = os.path.join(base, nom)
                yield os.path.relpath(chemin, _RACINE).replace(os.sep, "/")


def _ressources(contenu):
    """URLs chargées comme ressource (script/feuille de style), pas les liens."""
    urls = []
    for balise in re.findall(r"<(?:script|link)\b[^>]*>", contenu, re.IGNORECASE):
        for url in _REF.findall(balise):
            urls.append(url)
    return urls


@pytest.mark.parametrize("gabarit", list(_gabarits()))
def test_aucune_ressource_externe_sans_version(gabarit):
    fautives = []
    for url in _ressources(_lire(gabarit)):
        if not url.startswith(("http://", "https://", "//")):
            continue
        if any(hote in url for hote in _SANS_VERSION_ADMIS):
            continue
        if not _VERSION.search(url):
            fautives.append(url)
    assert fautives == [], (
        f"{gabarit} charge une ressource externe sans version complète : {fautives}. "
        f"Une publication amont peut casser la page sans qu'on ait rien changé. "
        f"Épinglez la version, ou mieux, servez une copie locale de static/."
    )


@pytest.mark.parametrize("gabarit", list(_gabarits()))
def test_aucun_chemin_statique_relatif(gabarit):
    fautifs = [url for url in _ressources(_lire(gabarit))
               if "../static" in url or url.startswith("static/")]
    assert fautifs == [], (
        f"{gabarit} référence un fichier statique par un chemin relatif : {fautifs}. "
        f"Utilisez url_for('static', filename=...) — un chemin relatif se résout "
        f"depuis l'URL servie, pas depuis l'emplacement du gabarit."
    )


def test_extension_ws_htmx_non_chargee():
    """Elle n'était activée nulle part, et visait htmx 1.x alors que 2.x
    était chargé à côté."""
    for gabarit in _gabarits():
        assert "dist/ext/ws.js" not in _lire(gabarit), (
            f"{gabarit} recharge l'extension ws d'htmx ; aucun hx-ext=\"ws\" "
            f"n'existe dans le dépôt"
        )


@pytest.mark.parametrize("gabarit,bibliotheque", [
    ("templates/counter/counter.html", "js/libs/htmx.min.js"),
    ("templates/counter/counter.html", "js/libs/socket.io.min.js"),
    ("templates/counter/counter.html", "js/libs/materialize-2.1.0.min.js"),
    ("templates/counter/wrong_counter.html", "js/libs/htmx.min.js"),
    ("templates/announce/announce.html", "js/libs/htmx.min.js"),
    ("templates/announce/announce.html", "js/libs/socket.io.min.js"),
    ("templates/patient/phone.html", "js/libs/htmx.min.js"),
    ("templates/patient/phone.html", "js/libs/socket.io.min.js"),
])
def test_bibliotheque_servie_localement(gabarit, bibliotheque):
    """Les pages utilisées en officine doivent survivre à une coupure Internet
    tant que le serveur local répond (même principe que la borne)."""
    assert bibliotheque in _lire(gabarit)
    assert os.path.exists(os.path.join(_RACINE, "static", *bibliotheque.split("/"))), (
        f"{bibliotheque} référencé mais absent de static/"
    )


def test_htmx_local_est_bien_fige():
    """La copie locale doit porter un numéro de version identifiable."""
    contenu = _lire("static/js/libs/htmx.min.js")
    assert re.search(r'version:"\d+\.\d+\.\d+"', contenu), (
        "impossible de lire la version de la copie locale d'htmx"
    )
