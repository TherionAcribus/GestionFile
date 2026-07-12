"""Tests du durcissement du secret applicatif (APP_SECRET).

Couvre les helpers ``auth_utils.is_valid_app_secret_config`` et
``auth_utils.check_app_secret`` qui sous-tendent :
- le refus de démarrage du serveur quand APP_SECRET n'est pas configuré ;
- la garde de ``/api/get_app_token`` (ne jamais émettre de token si le secret
  serveur est vide/placeholder, ni sur un secret client vide ; comparaison en
  temps constant).

Importable et exécutable sans MySQL (auth_utils n'importe pas app.py).
"""

import pytest

from auth_utils import is_valid_app_secret_config, check_app_secret


@pytest.mark.parametrize("value", [
    None,
    "",
    "   ",
    "changez_moi",
    "CHANGEZ_MOI",
    "change_me",
    "changeme",
    "secret",
    "password",
])
def test_invalid_secret_configs_are_rejected(value):
    assert is_valid_app_secret_config(value) is False


@pytest.mark.parametrize("value", [
    "un-vrai-secret-long-et-unique",
    "9f83b2c1a7d4e6",
    "  entouré-d-espaces-mais-valide  ",
])
def test_valid_secret_configs_are_accepted(value):
    assert is_valid_app_secret_config(value) is True


def test_check_rejects_when_server_secret_not_configured():
    """Cœur du correctif : APP_SECRET absent/vide => aucun secret client accepté,
    y compris un secret vide (l'ancien `'' == ''` renvoyait vrai)."""
    for configured in (None, "", "changez_moi"):
        assert check_app_secret("", configured) is False
        assert check_app_secret("n'importe quoi", configured) is False


def test_check_rejects_empty_provided_secret():
    assert check_app_secret("", "le-bon-secret") is False
    assert check_app_secret(None, "le-bon-secret") is False


def test_check_rejects_wrong_secret():
    assert check_app_secret("mauvais", "le-bon-secret") is False


def test_check_accepts_matching_secret():
    assert check_app_secret("le-bon-secret", "le-bon-secret") is True
