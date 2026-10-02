"""Feature gate et permission des routes de tests de performance."""
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager, login_user

from models import Role, StressTestLease, User, db
from routes.admin_performance import admin_performance_bp
from routes.admin_security import user_has_permission


@pytest.fixture()
def app():
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / 'templates'))
    app.config.update(
        TESTING=True, SECRET_KEY='stress-route-test',
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        STRESS_TEST_MODE='disabled', STRESS_TARGET_URL='http://web:5000',
        STRESS_TEST_ENABLED=False,
        STRESS_RUNNER_SECRET='x' * 40, STRESS_RUNNER_STALE_SECONDS=15)
    db.init_app(app)
    login = LoginManager(app)
    app.jinja_env.globals['csrf_token'] = lambda: ''

    @app.context_processor
    def admin_template_context():
        return {'user_has_permission': user_has_permission}

    @login.user_loader
    def load_user(user_id):
        return User.query.filter_by(fs_uniquifier=user_id).first()

    @app.route('/login/<name>', methods=['POST'])
    def login_as(name):
        login_user(User.query.filter_by(username=name).one())
        return '', 204

    app.register_blueprint(admin_performance_bp)
    with app.app_context():
        db.create_all()
        allowed = Role(name='performance', admin_performance=True)
        denied = Role(name='denied', admin_performance=False)
        alice = User(username='alice', password='x', fs_uniquifier='alice-stress',
                     active=True, roles=[allowed])
        bob = User(username='bob', password='x', fs_uniquifier='bob-stress',
                   active=True, roles=[denied])
        db.session.add_all([alice, bob, StressTestLease(id=1)])
        db.session.commit()
    return app


def test_api_refuses_anonymous_and_wrong_permission(app):
    client = app.test_client()
    assert client.get('/admin/performance/api/runs').status_code == 401
    client.post('/login/bob')
    assert client.get('/admin/performance/api/runs').status_code == 403


def test_disabled_launch_keeps_read_only_api_accessible(app):
    client = app.test_client()
    client.post('/login/alice')
    page = client.get('/admin/performance')
    assert page.status_code == 200
    assert 'Tests de performance' in page.get_data(as_text=True)
    assert client.get('/admin/performance/api/runs').status_code == 200
    capabilities = client.get('/admin/performance/api/capabilities')
    assert capabilities.status_code == 200
    assert capabilities.get_json()['launch_allowed'] is False
    assert client.post('/admin/performance/api/runs', json={}).status_code == 409


def test_enabled_permitted_api_is_available(app):
    app.config['STRESS_TEST_MODE'] = 'staging'
    app.config['STRESS_TEST_ENABLED'] = True
    client = app.test_client()
    client.post('/login/alice')
    response = client.get('/admin/performance/api/runs')
    assert response.status_code == 200
    assert response.get_json() == {'runs': []}
