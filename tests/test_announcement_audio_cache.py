"""Tests unitaires du cache audio, sans fournisseur TTS ni base de donnees."""

from pathlib import Path
from threading import Event

from flask import Flask

from announcement_audio import cached_announcement_url
from announcement_dispatcher import AnnouncementDispatcher


def _app(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    return Flask(__name__, static_folder=str(static))


def test_same_announcement_reuses_immutable_file(tmp_path):
    app = _app(tmp_path)
    generated = []

    def writer(path):
        generated.append(path)
        Path(path).write_bytes(b"ID3" + (b"a" * 256))

    # Le worker TTS n'a pas de requete HTTP : l'URL doit donc etre construite
    # correctement avec le seul contexte applicatif.
    with app.app_context():
        first = cached_announcement_url(
            text="Patient A12 au comptoir 2", provider="gtts", voice="fr",
            language="fr", writer=writer,
        )
        second = cached_announcement_url(
            text="Patient A12 au comptoir 2", provider="gtts", voice="fr",
            language="fr", writer=writer,
        )

    assert first == second
    assert len(generated) == 1
    assert first.startswith("/static/audio/annonces/")
    assert first.endswith(".mp3")


def test_voice_or_text_change_creates_a_different_cache_entry(tmp_path):
    app = _app(tmp_path)

    def writer(path):
        Path(path).write_bytes(b"ID3" + (b"a" * 256))

    with app.app_context():
        french = cached_announcement_url(
            text="Patient 12 au comptoir 2", provider="google", voice="fr-FR-Neural2-A",
            language="fr", voice_region="fr-FR", writer=writer,
        )
        other_voice = cached_announcement_url(
            text="Patient 12 au comptoir 2", provider="google", voice="fr-FR-Neural2-C",
            language="fr", voice_region="fr-FR", writer=writer,
        )
        other_text = cached_announcement_url(
            text="Patient 12 au comptoir 3", provider="google", voice="fr-FR-Neural2-A",
            language="fr", voice_region="fr-FR", writer=writer,
        )

    assert len({french, other_voice, other_text}) == 3


def test_failed_generation_never_leaves_a_playable_cache_file(tmp_path):
    app = _app(tmp_path)

    def failing_writer(path):
        Path(path).write_bytes(b"broken")

    with app.app_context():
        try:
            cached_announcement_url(
                text="Patient A12", provider="gtts", voice="fr", language="fr",
                writer=failing_writer,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Une generation incomplete doit echouer.")

    assert list((tmp_path / "static" / "audio" / "annonces").iterdir()) == []


def test_dispatcher_keeps_submission_order(tmp_path):
    app = _app(tmp_path)
    dispatcher = AnnouncementDispatcher()
    done = Event()
    seen = []

    def first():
        seen.append("first")

    def second():
        seen.append("second")
        done.set()

    assert dispatcher.submit(app, first) is True
    assert dispatcher.submit(app, second) is True
    assert done.wait(timeout=1)
    assert seen == ["first", "second"]
