"""Limitation des tentatives de connexion (point 3.4).

Module **pur** et testable sans Flask ni MySQL : toute la décision de
limitation/verrouillage vit ici, avec une horloge injectable pour des tests
déterministes. La route de connexion (``routes/admin_security.py``) se contente
d'appeler cette logique et de traduire le résultat en message générique.

Principe
--------
On limite indépendamment **par IP** et **par identité** (nom d'utilisateur), afin
qu'une adresse qui balaye plusieurs comptes soit freinée même si chaque compte
n'a subi qu'une tentative, et qu'un compte visé depuis plusieurs adresses le soit
aussi. La route interroge les deux clés et applique le délai le plus long.

Plutôt qu'un verrouillage brutal, on applique un **délai progressif** : chaque
échec repousse la prochaine tentative autorisée d'un délai qui double
(``base_delay``, 2·, 4·, …) plafonné à ``max_delay``. C'est à la fois le « délai
progressif » et le « verrouillage temporaire » demandés : après quelques échecs
le compte/IP est de fait bloqué pour ``max_delay``. Un succès ré-initialise la
clé ; les échecs plus vieux que ``window`` sont oubliés (récupération auto).

Périmètre pragmatique : l'état est **en mémoire, par process**. Derrière
plusieurs workers, chaque worker a son propre compteur. C'est volontairement
simple et suffisant pour une administration interne ; un stockage partagé
(table/redis) reste possible plus tard sans changer l'interface.
"""

from __future__ import annotations

import threading
import time
from collections import deque


# Valeurs par défaut : un plancher raisonnable pour une admin interne. Elles sont
# surchargeables à la construction (et donc en test) mais ne sont pas exposées en
# configuration admin — ce ne sont pas des réglages « métier ».
DEFAULT_BASE_DELAY = 2.0        # secondes avant la 1re re-tentative après 1 échec
DEFAULT_MAX_DELAY = 300.0       # plafond du délai progressif (verrouillage de fait)
DEFAULT_WINDOW = 900.0          # fenêtre glissante de mémorisation des échecs
DEFAULT_FREE_ATTEMPTS = 1       # nb d'échecs tolérés sans délai (évite de punir 1 faute de frappe)


class LoginThrottle:
    """Suivi des échecs et calcul du délai avant nouvelle tentative.

    Sûr pour un usage concurrent (un verrou protège l'état). Les clés sont des
    chaînes opaques : la route préfixe pour ne pas mélanger espaces
    (``ip:1.2.3.4`` vs ``user:alice``)."""

    def __init__(
        self,
        *,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
        window: float = DEFAULT_WINDOW,
        free_attempts: int = DEFAULT_FREE_ATTEMPTS,
        time_func=time.monotonic,
    ):
        self.base_delay = float(base_delay)
        self.max_delay = float(max_delay)
        self.window = float(window)
        self.free_attempts = int(free_attempts)
        self._time = time_func
        self._lock = threading.Lock()
        # clé -> deque[timestamps des échecs] (élagué à la fenêtre)
        self._failures: dict[str, deque[float]] = {}
        # clé -> instant avant lequel toute tentative est refusée
        self._blocked_until: dict[str, float] = {}

    # -- helpers internes (appelés sous verrou) -----------------------------
    def _prune(self, key: str, now: float) -> None:
        dq = self._failures.get(key)
        if dq is None:
            return
        horizon = now - self.window
        while dq and dq[0] < horizon:
            dq.popleft()
        if not dq:
            self._failures.pop(key, None)
            # Le blocage expire naturellement ; on le nettoie s'il est passé.
            if self._blocked_until.get(key, 0.0) <= now:
                self._blocked_until.pop(key, None)

    def _delay_for(self, count: int) -> float:
        """Délai progressif pour ``count`` échecs récents.

        Les ``free_attempts`` premiers échecs n'imposent aucun délai. Au-delà, le
        délai double à chaque échec (base, 2·base, 4·base…) jusqu'à ``max_delay``.
        """
        over = count - self.free_attempts
        if over <= 0:
            return 0.0
        delay = self.base_delay * (2 ** (over - 1))
        return min(delay, self.max_delay)

    # -- API publique -------------------------------------------------------
    def retry_after(self, key: str) -> float:
        """Secondes à attendre avant une tentative autorisée (0 si libre)."""
        with self._lock:
            now = self._time()
            self._prune(key, now)
            remaining = self._blocked_until.get(key, 0.0) - now
            return remaining if remaining > 0 else 0.0

    def is_blocked(self, key: str) -> bool:
        return self.retry_after(key) > 0

    def register_failure(self, key: str) -> float:
        """Enregistre un échec pour ``key`` et renvoie le nouveau délai imposé."""
        with self._lock:
            now = self._time()
            self._prune(key, now)
            dq = self._failures.setdefault(key, deque())
            dq.append(now)
            delay = self._delay_for(len(dq))
            if delay > 0:
                self._blocked_until[key] = now + delay
            return delay

    def register_success(self, key: str) -> None:
        """Efface tout l'historique d'échecs de ``key`` (connexion réussie)."""
        with self._lock:
            self._failures.pop(key, None)
            self._blocked_until.pop(key, None)

    def clear(self) -> None:
        """Vide entièrement l'état (utile en test)."""
        with self._lock:
            self._failures.clear()
            self._blocked_until.clear()


def ip_key(ip: str | None) -> str:
    return f"ip:{ip or 'unknown'}"


def identity_key(username: str | None) -> str:
    # Normalisation basique : on regroupe les variantes de casse/espaces d'un même
    # identifiant pour ne pas contourner la limite en changeant la casse.
    return f"user:{(username or '').strip().lower() or 'unknown'}"


def worst_retry_after(throttle: LoginThrottle, ip: str | None, username: str | None) -> float:
    """Délai effectif = le plus contraignant entre la clé IP et la clé identité."""
    return max(
        throttle.retry_after(ip_key(ip)),
        throttle.retry_after(identity_key(username)),
    )


# Singleton par process : partagé par toutes les requêtes du worker.
_shared_throttle: LoginThrottle | None = None
_shared_lock = threading.Lock()


def get_shared_throttle() -> LoginThrottle:
    global _shared_throttle
    if _shared_throttle is None:
        with _shared_lock:
            if _shared_throttle is None:
                _shared_throttle = LoginThrottle()
    return _shared_throttle
