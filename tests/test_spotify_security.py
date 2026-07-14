"""Sécurisation Spotify (secrets, authz, jetons côté serveur, ducking serveur).

Exécutable **sans MySQL ni serveur**. Couvre :

1. Logique pure ``spotify_support`` : résolution des identifiants sans valeur
   codée en dur (priorité au gestionnaire de secrets), URL de redirection
   tolérante hors requête, contrôleur de sourdine (DuckController) et son cycle.
2. Régression statique sur ``routes/admin_music.py`` : plus de secret client en
   dur, plus de journalisation de la clé, plus de stockage du jeton en session,
   cache adossé à la base, et **toutes** les routes ``/spotify/*`` protégées par
   une permission admin.
3. Régression statique : ducking piloté côté serveur (communication.py) et
   écran d'annonce public qui n'appelle plus aucune route Spotify.
"""

import os
import re

import pytest

import spotify_support as ss


_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Résolution des identifiants (aucune valeur codée en dur)
# ---------------------------------------------------------------------------

def test_env_credentials_take_precedence():
    creds = ss.resolve_spotify_credentials("env-id", "env-secret", "cfg-id", "cfg-secret")
    assert creds == ("env-id", "env-secret")


def test_config_credentials_used_when_no_env():
    creds = ss.resolve_spotify_credentials(None, None, "cfg-id", "cfg-secret")
    assert creds == ("cfg-id", "cfg-secret")


def test_missing_credentials_return_none():
    assert ss.resolve_spotify_credentials(None, None, None, None) is None
    # Un identifiant sans secret (ou l'inverse) ne suffit pas.
    assert ss.resolve_spotify_credentials("id", None, None, None) is None
    assert ss.resolve_spotify_credentials(None, "secret", None, None) is None


def test_env_and_config_can_be_mixed():
    # L'identifiant peut venir de la config et le secret de l'environnement.
    creds = ss.resolve_spotify_credentials(None, "env-secret", "cfg-id", None)
    assert creds == ("cfg-id", "env-secret")


def test_blank_values_are_ignored():
    creds = ss.resolve_spotify_credentials("   ", "  ", "cfg-id", "cfg-secret")
    assert creds == ("cfg-id", "cfg-secret")


# ---------------------------------------------------------------------------
# 2. URL de redirection
# ---------------------------------------------------------------------------

def test_redirect_uri_prefers_external_url():
    assert ss.resolve_redirect_uri("https://x/spotify/callback", "10.0.0.1") == \
        "https://x/spotify/callback"


def test_redirect_uri_falls_back_to_network_address():
    assert ss.resolve_redirect_uri(None, "10.0.0.5:5000") == \
        "http://10.0.0.5:5000/spotify/callback"


def test_redirect_uri_keeps_existing_scheme():
    assert ss.resolve_redirect_uri(None, "https://pharma.example") == \
        "https://pharma.example/spotify/callback"


def test_redirect_uri_last_resort_localhost():
    assert ss.resolve_redirect_uri(None, None) == "http://localhost/spotify/callback"


# ---------------------------------------------------------------------------
# 3. Contrôleur de sourdine (ducking)
# ---------------------------------------------------------------------------

def test_single_announcement_ducks_then_resumes():
    ctrl = ss.DuckController()
    should_duck, token = ctrl.begin()
    assert should_duck is True
    assert ctrl.is_ducked is True
    assert ctrl.end(token) is True
    assert ctrl.is_ducked is False


def test_overlapping_announcements_only_last_resumes():
    ctrl = ss.DuckController()
    duck1, t1 = ctrl.begin()
    duck2, t2 = ctrl.begin()
    # Seule la première annonce déclenche la sourdine.
    assert duck1 is True
    assert duck2 is False
    # La première annonce qui se termine ne relance pas (jeton périmé).
    assert ctrl.end(t1) is False
    assert ctrl.is_ducked is True
    # La dernière annonce relance.
    assert ctrl.end(t2) is True
    assert ctrl.is_ducked is False


def test_end_with_stale_token_is_noop():
    ctrl = ss.DuckController()
    _, t1 = ctrl.begin()
    ctrl.end(t1)
    # Rejouer une fin déjà consommée ne relance pas.
    assert ctrl.end(t1) is False


def test_reset_clears_ducked_state():
    ctrl = ss.DuckController()
    _, token = ctrl.begin()
    ctrl.reset()
    assert ctrl.is_ducked is False
    # Le jeton capturé avant reset est périmé.
    assert ctrl.end(token) is False


def test_run_duck_cycle_invokes_actions_in_order():
    ctrl = ss.DuckController()
    events = []
    ss.run_duck_cycle(
        ctrl,
        duck_action=lambda: events.append("duck"),
        resume_action=lambda: events.append("resume"),
        sleep_fn=lambda d: events.append(("sleep", d)),
        duration=7,
    )
    assert events == ["duck", ("sleep", 7), "resume"]
    assert ctrl.is_ducked is False


def test_run_duck_cycle_overlap_resumes_once():
    ctrl = ss.DuckController()
    events = []

    def duck():
        events.append("duck")

    def resume():
        events.append("resume")

    # Deux annonces qui se chevauchent : une seule sourdine, une seule reprise.
    def sleep_then_second(_):
        # Pendant le sommeil de la 1re annonce, une 2de démarre.
        ss.run_duck_cycle(ctrl, duck, resume, lambda d: None, 1)

    ss.run_duck_cycle(ctrl, duck, resume, sleep_then_second, 1)
    assert events.count("duck") == 1
    assert events.count("resume") == 1


# ---------------------------------------------------------------------------
# 4. Régression statique : plus de secret ni de jeton exposés
# ---------------------------------------------------------------------------

_MUSIC_SRC = None


def _music_src():
    global _MUSIC_SRC
    if _MUSIC_SRC is None:
        _MUSIC_SRC = _read("routes/admin_music.py")
    return _MUSIC_SRC


def test_no_hardcoded_spotify_secret():
    src = _music_src()
    # Anciennes valeurs codées en dur (à révoquer côté Spotify).
    assert "d061eca61b9b475dbffc3a15c57d6b5e" not in src
    assert "401f14a3f95e4c7fad1c525dfed3c808" not in src


def test_no_secret_logging():
    src = _music_src()
    # La clé/secret ne doit jamais être journalisée ni imprimée.
    assert 'MUSIC_SPOTIFY_KEY"]' not in src or "print" not in src
    assert "spotify_authorized" not in src
    assert 'print("spotify_authorized"' not in src


def test_token_not_stored_in_flask_session():
    src = _music_src()
    assert "session['token_info']" not in src
    assert 'session.get(\'token_info\'' not in src
    assert "SpotifyFlaskCacheHandler" not in src
    # Le cache est adossé à la base.
    assert "SpotifyDBCacheHandler" in src
    assert "SpotifyToken" in src


def test_all_spotify_routes_are_permission_protected():
    """Chaque route ``/spotify/*`` doit être suivie d'un décorateur de permission."""
    src = _music_src()
    lines = src.splitlines()
    route_re = re.compile(r"@admin_music_bp\.route\(\s*['\"]/spotify/")
    offenders = []
    for i, line in enumerate(lines):
        if route_re.search(line):
            # Cherche un @require_permission avant le 'def' suivant.
            guarded = False
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].lstrip().startswith("def "):
                    break
                if "require_permission(" in lines[j]:
                    guarded = True
                    break
            if not guarded:
                offenders.append(line.strip())
    assert offenders == [], f"Routes Spotify non protégées : {offenders}"


def test_show_saved_tracks_protected():
    src = _music_src()
    # La route show_saved_tracks doit aussi être protégée.
    idx = src.index("def show_saved_tracks")
    head = src[max(0, idx - 200):idx]
    assert "require_permission('music_play')" in head


def test_announce_ducking_routes_removed():
    """Les routes HTTP publiques start/stop_announce ne doivent plus exister."""
    src = _music_src()
    assert "/spotify/start_announce" not in src
    assert "/spotify/stop_announce" not in src


# ---------------------------------------------------------------------------
# 5. Régression statique : ducking serveur + écran d'annonce nettoyé
# ---------------------------------------------------------------------------

def test_server_triggers_ducking():
    comm = _read("communication.py")
    assert "duck_for_announcement" in comm


def test_announce_js_no_longer_calls_spotify():
    js = _read("static/js/announce.js")
    assert "/spotify/start_announce" not in js
    assert "/spotify/stop_announce" not in js
    assert "/announce/spotify/check_connection" not in js


def test_check_connection_route_removed():
    announce = _read("routes/announce.py")
    assert "check_connection" not in announce
