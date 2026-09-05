"""Point 12 (audit Admin) — Restauration MySQL : validation et sécurité.

Trois problèmes corrigés dans ``init_restore.py`` :

1. **Injection SQL via db_name** : ``db_name`` provenait du nom du fichier
   .sql dans le ZIP uploadé et était interpolé directement dans
   ``f"USE {db_name}"``. Un ZIP malveillant contenant un fichier nommé
   ``DROP DATABASE mysql; --.sql`` permettait une injection SQL.
   Désormais : validation par regex (alphanumérique + _ uniquement) +
   échappement avec backticks.

2. **DROP TABLE non échappé** : les noms de tables venaient de ``SHOW TABLES``
   et étaient interpolés dans ``f"DROP TABLE IF EXISTS {table[0]}"``. Désormais
   échappés avec backticks + validés.

3. **split(';') naïf** : cassait sur les ``;`` contenus dans les chaînes
   littérales (ex. ``INSERT INTO config VALUES ('a;b')``). Remplacé par
   ``_split_sql_statements`` qui respecte les délimiteurs de chaînes.

Tests :
- Statiques pour la validation et l'échappement (lecture du source)
- Unitaires pour ``_split_sql_statements`` (fonction pure, testable sans MySQL)
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Validation des identifiants (db_name)
# ---------------------------------------------------------------------------

def test_init_restore_has_identifier_validation():
    """init_restore.py doit valider les identifiants MySQL avant usage."""
    source = _read("init_restore.py")
    assert "_VALID_IDENTIFIER" in source
    assert "_validate_identifier" in source
    assert "re.compile" in source


def test_init_restore_identifier_regex_is_safe():
    """La regex ne doit accepter que des caractères sûrs (alphanum + _)."""
    source = _read("init_restore.py")
    m = re.search(r'_VALID_IDENTIFIER\s*=\s*re\.compile\(\s*r["\']([^"\']+)["\']', source)
    assert m, "_VALID_IDENTIFIER regex introuvable"
    pattern = m.group(1)
    # Ne doit pas accepter de ; (injection SQL), espaces, ou caractères spéciaux.
    assert ";" not in pattern
    assert " " not in pattern
    # Doit être ancré (^ et $) pour éviter les correspondances partielles.
    assert pattern.startswith("^")
    assert pattern.endswith("$")


def test_restore_mysql_database_validates_db_name():
    """restore_mysql_database doit appeler _validate_identifier sur db_name."""
    source = _read("init_restore.py")
    m = re.search(r"def restore_mysql_database\([^)]*\):(.*?)(?=\ndef |\Z)",
                  source, re.DOTALL)
    assert m, "restore_mysql_database introuvable"
    body = m.group(1)
    assert "_validate_identifier" in body
    assert "db_name" in body


# ---------------------------------------------------------------------------
# 2. Échappement avec backticks
# ---------------------------------------------------------------------------

def test_restore_mysql_database_uses_backticks():
    """Les identifiants (USE, DROP TABLE) doivent être échappés avec backticks."""
    source = _read("init_restore.py")
    m = re.search(r"def restore_mysql_database\([^)]*\):(.*?)(?=\ndef |\Z)",
                  source, re.DOTALL)
    assert m, "restore_mysql_database introuvable"
    body = m.group(1)
    # Retirer les commentaires pour ne tester que le code.
    code_only = re.sub(r'#.*$', '', body, flags=re.MULTILINE)
    # USE doit utiliser des backticks, pas une interpolation brute.
    assert "USE `{db_name}`" in code_only or "USE `{table_name}`" in code_only
    assert "DROP TABLE IF EXISTS `{table_name}`" in code_only
    # Ne doit plus contenir d'interpolation sans backticks (dans le code, pas les commentaires).
    assert 'f"USE {db_name}"' not in code_only
    assert 'f"DROP TABLE IF EXISTS {table[0]}"' not in code_only


# ---------------------------------------------------------------------------
# 3. _split_sql_statements : fonction pure extraite du source (sans MySQL)
# ---------------------------------------------------------------------------

def _extract_split_function():
    """Extrait _split_sql_statements depuis init_restore.py par exec.

    init_restore.py importe mysql.connector (non installé en test), donc on
    ne peut pas l'importer directement. On extrait la fonction depuis le
    source et on l'évalue dans un namespace isolé.
    """
    import textwrap
    source = _read("init_restore.py")
    m = re.search(
        r"(def _split_sql_statements\(sql_text\):.*?)(?=^(?:def |@|\Z))",
        source, re.DOTALL | re.MULTILINE,
    )
    assert m, "_split_sql_statements introuvable"
    func_src = textwrap.dedent(m.group(1))
    ns = {}
    exec(func_src, ns)
    return ns["_split_sql_statements"]


# Fixture : la fonction extraite, disponible pour tous les tests unitaires
@pytest.fixture(scope="module")
def split_fn():
    return _extract_split_function()


def test_split_sql_statements_exists():
    source = _read("init_restore.py")
    assert "def _split_sql_statements" in source


def test_split_sql_statements_basic(split_fn):
    """Split basique sur ;."""
    sql = "INSERT INTO a VALUES (1); INSERT INTO b VALUES (2);"
    stmts = split_fn(sql)
    assert len(stmts) == 2
    assert "INSERT INTO a" in stmts[0]
    assert "INSERT INTO b" in stmts[1]


def test_split_sql_statements_respects_single_quotes(split_fn):
    """Un ; dans une chaîne simple ne doit pas couper le statement."""
    sql = "INSERT INTO config VALUES ('a;b'); SELECT 1;"
    stmts = split_fn(sql)
    assert len(stmts) == 2
    assert "'a;b'" in stmts[0]
    assert "SELECT 1" in stmts[1]


def test_split_sql_statements_respects_double_quotes(split_fn):
    """Un ; dans une chaîne double-quoted ne doit pas couper."""
    sql = 'INSERT INTO config VALUES ("a;b"); SELECT 1;'
    stmts = split_fn(sql)
    assert len(stmts) == 2
    assert '"a;b"' in stmts[0]


def test_split_sql_statements_handles_escaped_quotes(split_fn):
    """Les échappements \\' et \\" dans les chaînes ne doivent pas casser le split."""
    sql = "INSERT INTO t VALUES ('it\\'s; ok'); SELECT 1;"
    stmts = split_fn(sql)
    assert len(stmts) == 2
    assert "it\\'s; ok" in stmts[0]


def test_split_sql_statements_no_trailing_semicolon(split_fn):
    """Un dernier statement sans ; final doit être conservé."""
    sql = "INSERT INTO a VALUES (1); SELECT 2"
    stmts = split_fn(sql)
    assert len(stmts) == 2
    assert "SELECT 2" in stmts[1]


def test_split_sql_statements_empty_input(split_fn):
    """Une entrée vide ne doit pas produire de statement."""
    assert split_fn("") == []
    assert split_fn("   ") == []


def test_split_sql_statements_only_semicolons(split_fn):
    """Des ; seuls ne doivent pas produire de statement vide."""
    stmts = split_fn(";;;")
    assert stmts == []


def test_split_sql_statements_strips_whitespace(split_fn):
    """Les statements doivent être stripped (pas d'espaces en début/fin)."""
    sql = "  SELECT 1  ;  SELECT 2  "
    stmts = split_fn(sql)
    assert all(s == s.strip() for s in stmts)
    assert stmts[0] == "SELECT 1"
    assert stmts[1] == "SELECT 2"


# ---------------------------------------------------------------------------
# 4. restore_mysql_database utilise _split_sql_statements (pas split naïf)
# ---------------------------------------------------------------------------

def test_restore_mysql_database_uses_split_function():
    """restore_mysql_database doit utiliser _split_sql_statements, pas .split(';')."""
    source = _read("init_restore.py")
    m = re.search(r"def restore_mysql_database\([^)]*\):(.*?)(?=\ndef |\Z)",
                  source, re.DOTALL)
    assert m, "restore_mysql_database introuvable"
    body = m.group(1)
    assert "_split_sql_statements" in body
    # Ne doit plus contenir le split naïf.
    assert ".split(';')" not in body
