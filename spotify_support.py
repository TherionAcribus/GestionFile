"""Logique Spotify pure et testable (sans réseau, DB ni contexte Flask).

Ce module regroupe trois responsabilités qui ne dépendent d'aucune ressource
externe, afin de pouvoir être testées sans MySQL ni serveur :

1. :func:`resolve_spotify_credentials` — résout l'identifiant/secret client de
   l'application Spotify **sans jamais recourir à une valeur codée en dur**. Une
   variable d'environnement (gestionnaire de secrets) est prioritaire sur la
   configuration stockée en base.
2. :func:`resolve_redirect_uri` — construit l'URL de redirection OAuth, avec un
   repli utilisable hors contexte de requête (rafraîchissement de jeton dans une
   tâche de fond, où ``url_for(_external=True)`` n'est pas disponible).
3. :class:`DuckController` — décide, de façon thread-safe, quand mettre la
   musique en sourdine pendant une annonce et quand la relancer. Le *token*
   empêche une reprise prématurée lorsque plusieurs annonces se chevauchent :
   seule la dernière annonce planifiée relance la musique.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional, Tuple


SPOTIFY_SCOPE = (
    "user-library-read user-read-playback-state "
    "user-modify-playback-state streaming"
)


def resolve_spotify_credentials(
    env_client_id: Optional[str],
    env_client_secret: Optional[str],
    config_client_id: Optional[str],
    config_client_secret: Optional[str],
) -> Optional[Tuple[str, str]]:
    """Retourne ``(client_id, client_secret)`` ou ``None`` si non configuré.

    Priorité au gestionnaire de secrets (variables d'environnement
    ``SPOTIFY_CLIENT_ID`` / ``SPOTIFY_CLIENT_SECRET``) puis à la configuration
    de l'officine. Aucune valeur n'est jamais codée en dur : si ni l'un ni
    l'autre ne fournit les deux valeurs, la fonction renvoie ``None`` et
    l'appelant doit traiter Spotify comme « non configuré ».
    """
    client_id = _first_non_empty(env_client_id, config_client_id)
    client_secret = _first_non_empty(env_client_secret, config_client_secret)
    if not client_id or not client_secret:
        return None
    return client_id, client_secret


def resolve_redirect_uri(
    external_url: Optional[str],
    network_address: Optional[str],
    *,
    path: str = "/spotify/callback",
) -> str:
    """URL de redirection OAuth.

    ``external_url`` est le résultat de ``url_for(..., _external=True)`` quand un
    contexte de requête existe. Hors requête (tâche de fond de
    rafraîchissement), il vaut ``None`` : on reconstruit alors l'URL à partir de
    l'adresse réseau configurée. Ce repli ne sert qu'au rafraîchissement du
    jeton — Spotify ne vérifie l'``redirect_uri`` que lors de l'échange du code
    d'autorisation, réalisé, lui, dans un vrai contexte de requête.
    """
    if external_url:
        return external_url

    base = (network_address or "").strip().rstrip("/")
    if base:
        if not base.startswith(("http://", "https://")):
            base = "http://" + base
        return f"{base}{path}"
    return f"http://localhost{path}"


def _first_non_empty(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if value and value.strip():
            return value.strip()
    return None


class DuckController:
    """Coordonne la mise en sourdine de la musique pendant les annonces.

    Thread-safe et sans effet de bord : les actions concrètes (mettre en pause /
    baisser le volume, puis relancer / restaurer) sont réalisées par l'appelant
    en fonction des valeurs retournées. Le compteur ``_active`` et le jeton
    monotone gèrent le chevauchement d'annonces :

    - :meth:`begin` signale le début d'une annonce et indique s'il faut
      réellement déclencher la sourdine (uniquement la première annonce active) ;
    - :meth:`end` indique s'il faut relancer la musique (uniquement quand la
      *dernière* annonce planifiée se termine, d'où la comparaison de jeton).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ducked = False
        self._token = 0

    def begin(self) -> Tuple[bool, int]:
        """Enregistre une annonce. Retourne ``(should_duck, token)``.

        ``should_duck`` est vrai seulement pour la première annonce active :
        les annonces suivantes se contentent de renouveler le jeton pour
        prolonger la sourdine.
        """
        with self._lock:
            self._token += 1
            token = self._token
            should_duck = not self._ducked
            self._ducked = True
            return should_duck, token

    def end(self, token: int) -> bool:
        """Retourne vrai s'il faut relancer la musique pour ce ``token``.

        Faux si une annonce plus récente est survenue entre-temps (jeton
        périmé) ou si la musique n'est plus en sourdine.
        """
        with self._lock:
            if token != self._token or not self._ducked:
                return False
            self._ducked = False
            return True

    @property
    def is_ducked(self) -> bool:
        with self._lock:
            return self._ducked

    def reset(self) -> None:
        """Réinitialise l'état (ex : déconnexion Spotify)."""
        with self._lock:
            self._ducked = False
            self._token += 1


def run_duck_cycle(
    controller: DuckController,
    duck_action: Callable[[], None],
    resume_action: Callable[[], None],
    sleep_fn: Callable[[float], None],
    duration: float,
) -> None:
    """Déroule un cycle complet de sourdine pour une annonce.

    Séparé de :class:`DuckController` pour rester testable en injectant un
    ``sleep_fn`` synchrone. En production, ``sleep_fn`` est ``socketio.sleep``
    et l'ensemble tourne dans une tâche de fond, hors du chemin de l'appel
    patient.
    """
    should_duck, token = controller.begin()
    if should_duck:
        duck_action()
    sleep_fn(duration)
    if controller.end(token):
        resume_action()
