"""File FIFO bornee pour les annonces vocales.

Une synthese peut etre lente, mais l'ordre des appels ne doit jamais dependre
de l'ordre de retour des fournisseurs TTS. Un seul worker par processus traite
donc les annonces dans leur ordre de soumission sans bloquer les routes HTTP.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable


MAX_PENDING_ANNOUNCEMENTS = 128


class AnnouncementDispatcher:
    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[object, Callable[[], None]]] = queue.Queue(
            maxsize=MAX_PENDING_ANNOUNCEMENTS
        )
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def submit(self, flask_app, job: Callable[[], None]) -> bool:
        """Ajoute une annonce sans attendre ; ``False`` si la file est saturee."""
        self._ensure_worker()
        try:
            self._queue.put_nowait((flask_app, job))
            return True
        except queue.Full:
            flask_app.logger.critical(
                "File des annonces vocales saturee (%s messages) ; annonce ignoree.",
                MAX_PENDING_ANNOUNCEMENTS,
            )
            return False

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run,
                name="announcement-audio-worker",
                daemon=True,
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            flask_app, job = self._queue.get()
            try:
                with flask_app.app_context():
                    job()
            except Exception:
                flask_app.logger.exception("Echec de la generation/diffusion d'une annonce vocale")
            finally:
                # Une session SQLAlchemy creee par le job ne doit jamais vivre
                # plus longtemps que le travail qui l'a utilisee.
                if "sqlalchemy" in flask_app.extensions:
                    try:
                        from models import db
                        db.session.remove()
                    except Exception:
                        flask_app.logger.exception("Nettoyage de session apres annonce impossible")
                self._queue.task_done()


announcement_dispatcher = AnnouncementDispatcher()
