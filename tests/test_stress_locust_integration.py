"""Mini integration Locust : 2 utilisateurs pendant 5 secondes."""
import threading

import pytest

locust = pytest.importorskip('locust')
gevent = pytest.importorskip('gevent')

from flask import Flask
from locust import HttpUser, task
from locust.env import Environment
from werkzeug.serving import make_server


class _HealthUser(HttpUser):
    @task
    def health(self):
        self.client.get('/healthz')


def test_locust_two_users_for_five_seconds():
    application = Flask(__name__)

    @application.route('/healthz')
    def healthz():
        return {'status': 'alive'}

    server = make_server('127.0.0.1', 0, application)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = Environment(user_classes=[_HealthUser],
                              host=f'http://127.0.0.1:{server.server_port}')
    runner = environment.create_local_runner()
    try:
        runner.start(2, spawn_rate=2)
        gevent.sleep(5)
        runner.quit()
        assert environment.stats.total.num_requests > 0
        assert environment.stats.total.num_failures == 0
    finally:
        runner.quit()
        server.shutdown()
        thread.join(timeout=2)
