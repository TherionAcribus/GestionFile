"""Politique minimale de mot de passe (point 3.4) — logique pure."""

from password_policy import (
    validate_password,
    is_valid_password,
    MIN_LENGTH,
)


def test_empty_password_rejected():
    assert validate_password("") == ["Le mot de passe est obligatoire."]
    assert validate_password(None)


def test_too_short_rejected():
    problems = validate_password("court")
    assert any("au moins" in p for p in problems)
    assert not is_valid_password("court")


def test_minimum_length_boundary():
    # Exactement MIN_LENGTH caractères, non trivial : accepté.
    ok = "Xk7" + "a" * (MIN_LENGTH - 3)
    assert len(ok) == MIN_LENGTH
    assert is_valid_password(ok)
    # Un caractère de moins : refusé.
    assert not is_valid_password(ok[:-1])


def test_common_passwords_rejected():
    for weak in ("password", "azertyuiop", "1234567890", "changeme"):
        assert not is_valid_password(weak), weak


def test_default_admin_password_rejected():
    # Les identifiants par défaut du projet ne doivent jamais passer la politique.
    assert not is_valid_password("admin")
    assert not is_valid_password("gestionfile")


def test_common_password_rejected_case_insensitive():
    assert not is_valid_password("PassWord")


def test_password_equal_to_username_rejected():
    problems = validate_password("SuperUtilisateur", username="superutilisateur")
    assert any("identique au nom" in p for p in problems)


def test_whitespace_only_rejected():
    assert not is_valid_password("          ")  # 10 espaces


def test_strong_password_accepted():
    assert is_valid_password("Zt9!parapluie-lune", username="alice")
    assert validate_password("Zt9!parapluie-lune", username="alice") == []
