"""Mode debug : le surveillant du rechargement ne doit rien demarrer en double.

``socketio.run(debug=True)`` (eventlet) passe par le reloader de Werkzeug : le
processus lance par ``python app.py`` relance un enfant (``WERKZEUG_RUN_MAIN``)
et ne sert aucune requete. Avant, il executait aussi les taches de demarrage,
un ordonnanceur actif et la sonde : chaque tache planifiee tournait deux fois.

Importer app.py exige MySQL : la fonction est extraite du source et les gardes
sont verifiees par analyse syntaxique.
"""
import ast
import types
from pathlib import Path

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)


def _watcher(module_name, role, environ):
    node = next(n for n in TREE.body
                if isinstance(n, ast.FunctionDef) and n.name == '_is_reloader_watcher')
    namespace = {'__name__': module_name, 'APP_ROLE': role,
                 'os': types.SimpleNamespace(environ=environ)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), namespace)
    return namespace['_is_reloader_watcher']


@pytest.mark.parametrize('module_name, role, debug, environ, expected', [
    ('__main__', 'all', True, {}, True),                            # surveillant
    ('__main__', 'web', True, {}, True),
    ('__main__', 'all', True, {'WERKZEUG_RUN_MAIN': 'true'}, False),  # enfant servi
    ('__main__', 'all', False, {}, False),                          # pas de reloader
    ('__main__', 'scheduler', True, {}, False),                     # pas de socketio.run
    ('app', 'all', True, {}, False),                                # import (manage.py)
])
def test_watcher_detection(module_name, role, debug, environ, expected):
    assert _watcher(module_name, role, environ)(debug) is expected


def _calls_in(nodes):
    return [getattr(n.func, 'id', getattr(n.func, 'attr', None))
            for node in nodes for n in ast.walk(node) if isinstance(n, ast.Call)]


def test_startup_hooks_are_skipped_in_the_watcher():
    for node in ast.walk(TREE):
        if (isinstance(node, ast.If) and 'start_fonctions' in _calls_in(node.orelse)
                and '_is_reloader_watcher' in _calls_in([node.test])):
            return
    pytest.fail('start_fonctions doit etre dans le else de `if _is_reloader_watcher(...)`')


def test_watcher_branch_runs_the_server_without_scheduler():
    main = next(n for n in TREE.body if isinstance(n, ast.If)
                and isinstance(n.test, ast.Compare)
                and getattr(n.test.left, 'id', None) == '__name__')
    for node in ast.walk(main):
        if isinstance(node, ast.If) and '_is_reloader_watcher' in _calls_in([node.test]):
            branch = _calls_in(node.body)
            assert 'run' in branch  # socketio.run : le reloader relance l'enfant
            assert not {'start_scheduler', '_reconcile_scheduler_jobs',
                        '_ensure_stress_probe'} & set(branch)
            assert 'start_scheduler' in _calls_in(node.orelse)
            return
    pytest.fail('branche `elif _is_reloader_watcher(app.debug)` absente du bloc __main__')
