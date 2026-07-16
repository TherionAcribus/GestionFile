"""Limitation des tentatives de connexion (point 3.4) — logique pure.

Ces tests exercent ``login_guard`` avec une horloge injectée : ni Flask ni MySQL.
"""

import pytest

from login_guard import (
    LoginThrottle,
    ip_key,
    identity_key,
    worst_retry_after,
    get_shared_throttle,
)


class FakeClock:
    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def throttle(clock):
    # free_attempts=1 : le 1er échec ne pénalise pas ; les suivants oui.
    return LoginThrottle(base_delay=2.0, max_delay=100.0, window=900.0,
                         free_attempts=1, time_func=clock)


def test_no_delay_before_any_failure(throttle):
    assert throttle.retry_after("user:alice") == 0
    assert not throttle.is_blocked("user:alice")


def test_first_failure_is_free(throttle):
    delay = throttle.register_failure("user:alice")
    assert delay == 0
    assert throttle.retry_after("user:alice") == 0


def test_delay_is_progressive(throttle, clock):
    throttle.register_failure("user:alice")            # 1er : gratuit
    assert throttle.register_failure("user:alice") == 2.0   # 2e : base
    # Toujours dans le même instant : le blocage est actif.
    assert throttle.retry_after("user:alice") == pytest.approx(2.0)
    clock.advance(2.0)
    assert throttle.register_failure("user:alice") == 4.0   # 3e : 2·base
    clock.advance(4.0)
    assert throttle.register_failure("user:alice") == 8.0   # 4e : 4·base


def test_delay_is_capped(clock):
    t = LoginThrottle(base_delay=2.0, max_delay=10.0, window=900.0,
                      free_attempts=1, time_func=clock)
    delay = 0
    for _ in range(10):
        delay = t.register_failure("user:bob")
        clock.advance(delay)
    assert delay == 10.0  # plafonné, jamais au-delà de max_delay


def test_retry_after_decreases_with_time(throttle, clock):
    throttle.register_failure("user:alice")
    throttle.register_failure("user:alice")  # impose 2s
    assert throttle.retry_after("user:alice") == pytest.approx(2.0)
    clock.advance(1.5)
    assert throttle.retry_after("user:alice") == pytest.approx(0.5)
    clock.advance(1.0)
    assert throttle.retry_after("user:alice") == 0


def test_success_resets_counter(throttle, clock):
    throttle.register_failure("user:alice")
    throttle.register_failure("user:alice")
    throttle.register_success("user:alice")
    assert throttle.retry_after("user:alice") == 0
    # Le compteur repart de zéro : le prochain échec est de nouveau « gratuit ».
    assert throttle.register_failure("user:alice") == 0


def test_window_forgets_old_failures(clock):
    t = LoginThrottle(base_delay=2.0, max_delay=100.0, window=60.0,
                      free_attempts=1, time_func=clock)
    t.register_failure("user:alice")
    t.register_failure("user:alice")  # 2 échecs récents
    clock.advance(120.0)              # au-delà de la fenêtre
    # Les échecs sont oubliés : le prochain redevient gratuit.
    assert t.register_failure("user:alice") == 0


def test_ip_and_identity_are_independent_keys(throttle):
    throttle.register_failure(ip_key("1.2.3.4"))
    throttle.register_failure(ip_key("1.2.3.4"))
    assert throttle.retry_after(ip_key("1.2.3.4")) > 0
    # L'identité n'a pas été pénalisée.
    assert throttle.retry_after(identity_key("alice")) == 0


def test_identity_key_normalizes_case_and_space(throttle):
    assert identity_key("Alice") == identity_key("  alice ")
    assert identity_key(None) == "user:unknown"


def test_ip_key_handles_missing_ip():
    assert ip_key(None) == "ip:unknown"


def test_worst_retry_after_takes_max(throttle, clock):
    # Pénalise fortement l'IP, pas l'identité.
    throttle.register_failure(ip_key("9.9.9.9"))
    throttle.register_failure(ip_key("9.9.9.9"))  # ~2s
    worst = worst_retry_after(throttle, "9.9.9.9", "carol")
    assert worst == pytest.approx(2.0)


def test_shared_throttle_is_singleton():
    assert get_shared_throttle() is get_shared_throttle()
