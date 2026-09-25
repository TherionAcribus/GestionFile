# Vérification locale

- Sous Windows, utiliser `.venv/Scripts/python.exe` pour les commandes Python.
- Suite de référence : `.venv/Scripts/python.exe -m pytest -q`. Garder les exclusions par défaut de `pytest.ini` (MySQL et E2E).
- Éditeur visuel : `.venv/Scripts/python.exe -m pytest tests/test_page_editor.py tests/test_advanced_editor_design.py -q`.
- Parcours navigateur des thèmes, sur serveur éphémère et SQLite en mémoire : `.venv/Scripts/python.exe -m pytest tests/test_page_editor.py -m e2e -k builtin_themes_browser_flow -q`. Nécessite Playwright et Chromium ; ne requiert pas le serveur de production ni MySQL.
- Syntaxe JavaScript : `node --check static/js/page_editor.js` et `node --check static/js/page_editor_preview.js`.
- Pour les vérifications isolées, réutiliser le fixture `editor_app` plutôt que démarrer l’application réelle : les hooks de démarrage peuvent avoir des effets sur les données et les écrans connectés.
