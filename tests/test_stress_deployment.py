"""Contrats statiques du conteneur runner Locust."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_is_decoupled_from_web_eventlet_app():
    source = (ROOT / 'stress_runner.py').read_text(encoding='utf-8')
    assert 'from app import app' not in source
    assert "SKIP_EVENTLET_PATCH', '1'" in source
    assert "app = Flask('stress_runner')" in source
    assert 'gevent.sleep' in source


def test_runner_image_uses_versioned_locust_and_has_no_public_port():
    requirements = (ROOT / 'requirements-stress.txt').read_text(encoding='utf-8')
    compose = (ROOT / 'docker-compose.coolify.yaml').read_text(encoding='utf-8')
    assert 'locust==' in requirements
    assert 'locust==latest' not in requirements
    block = compose.split('\n  stress-runner:', 1)[1].split('\nvolumes:', 1)[0]
    assert 'Dockerfile.stress' in block
    assert 'SKIP_EVENTLET_PATCH: "1"' in block
    assert '\n    ports:' not in block
