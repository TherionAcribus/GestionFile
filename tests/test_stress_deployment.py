"""Contrats statiques du conteneur runner Locust."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runner_is_decoupled_from_web_eventlet_app():
    source = (ROOT / 'stress_runner.py').read_text(encoding='utf-8')
    assert 'from app import app' not in source
    assert "SKIP_EVENTLET_PATCH', '1'" in source
    assert "app = Flask('stress_runner')" in source
    assert 'gevent.sleep' in source
    assert 'allow_redirects=False' in source
    assert 'redirection inattendue' in source


def test_runner_image_uses_versioned_locust_and_has_no_public_port():
    requirements = (ROOT / 'requirements-stress.txt').read_text(encoding='utf-8')
    compose = (ROOT / 'docker-compose.coolify.yaml').read_text(encoding='utf-8')
    assert 'locust==' in requirements
    assert 'locust==latest' not in requirements
    block = compose.split('\n  stress-runner:', 1)[1].split('\nvolumes:', 1)[0]
    assert 'Dockerfile.stress' in block
    assert 'SKIP_EVENTLET_PATCH: "1"' in block
    assert '\n    ports:' not in block


def test_admin_page_exposes_a_readable_report_and_history_action():
    template = (ROOT / 'templates' / 'admin' / 'performance.html').read_text(
        encoding='utf-8')
    script = (ROOT / 'static' / 'js' / 'admin_performance.js').read_text(
        encoding='utf-8')
    assert 'id="result-report"' in template
    assert 'id="endpoint-body"' in template
    assert 'Voir le rapport' in script
    assert 'result_message' in script
    assert 'first_error' in script
    assert 'selectedScenario' in script
    assert 'selectedProfile' in script
    assert 'selectedComparisons' in script
    assert 'id="business-summary"' in template
    assert 'business_interpretation' in script
    assert 'Lecture simple' in template
