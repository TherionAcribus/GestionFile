"""Point 5 (audit Admin) — Navigation : surbrillance, permissions, mobile.

Trois problèmes corrigés dans ``templates/admin/base.html`` :

1. **Surbrillance statique** : le lien « Accueil » était toujours marqué
   ``active`` + ``aria-current="page"``, quelle que soit la page courante.
   L'utilisateur ne savait donc jamais où il était dans le menu.

2. **Aucun masquage par permission** : toutes les entrées étaient visibles par
   tout utilisateur connecté, même sans la permission correspondante. Un
   utilisateur sans permission ``security`` voyait le lien « Sécurité » (et
   recevait une 403 au clic).

3. **Pas de menu mobile** : la sidebar était ``d-md-block`` (visible ≥768px)
   mais n'avait aucun bouton hamburger : sur mobile, le menu disparaissait
   sans moyen de l'ouvrir.

Vérifications statiques (on lit le source du template, comme les autres tests
de régression statique de ce dépôt).
"""

import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def _read(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# 1. Surbrillance dynamique selon request.path
# ---------------------------------------------------------------------------

def test_no_static_active_on_accueil():
    """L'ancien code marquait 'Accueil' toujours actif. On vérifie que la
    classe active est maintenant conditionnelle (request.path)."""
    src = _read("templates/admin/base.html")
    # L'ancien code : <a class="nav-link active" aria-current="page" href="/admin">
    # Le nouveau code doit conditionner via request.path.
    accueil_match = re.search(
        r'<a class="nav-link [^"]*"\s*[^>]*href="/admin">', src)
    assert accueil_match, "Lien Accueil introuvable"
    accueil_block = accueil_match.group(0)
    assert "request.path" in accueil_block or "'active'" in accueil_block
    # Ne doit plus être statiquement active.
    assert 'class="nav-link active"' not in accueil_block


def test_sidebar_uses_request_path_for_active():
    """Au moins une entrée doit utiliser request.path pour la surbrillance."""
    src = _read("templates/admin/base.html")
    assert "request.path" in src
    assert "'active'" in src
    assert "aria-current" in src


# ---------------------------------------------------------------------------
# 2. Masquage par permission
# ---------------------------------------------------------------------------

def test_sidebar_uses_user_has_permission():
    """Les entrées du menu doivent être conditionnées par user_has_permission."""
    src = _read("templates/admin/base.html")
    assert "user_has_permission" in src


def test_sidebar_guards_security_link():
    """Le lien Sécurité ne doit être affiché que si l'utilisateur a la
    permission 'security'."""
    src = _read("templates/admin/base.html")
    # Trouver le bloc contenant /admin/security
    m = re.search(r"{% if.*%}.*?href=\"/admin/security\".*?{% endif %}",
                  src, re.DOTALL)
    assert m, "Le lien Sécurité doit être gardé par un {% if %}"
    block = m.group(0)
    assert "user_has_permission(current_user, 'security')" in block


def test_sidebar_guards_queue_link():
    src = _read("templates/admin/base.html")
    m = re.search(r"{% if.*%}.*?href=\"/admin/queue\".*?{% endif %}",
                  src, re.DOTALL)
    assert m, "Le lien File d'attente doit être gardé par un {% if %}"
    assert "user_has_permission(current_user, 'queue')" in m.group(0)


def test_sidebar_guards_app_link():
    src = _read("templates/admin/base.html")
    m = re.search(r"{% if.*%}.*?href=\"/admin/app\".*?{% endif %}",
                  src, re.DOTALL)
    assert m, "Le lien Application doit être gardé par un {% if %}"
    assert "user_has_permission(current_user, 'app')" in m.group(0)


def test_context_processor_exposes_user_has_permission():
    """app.py doit exposer user_has_permission aux templates via context
    processor."""
    src = _read("app.py")
    assert "user_has_permission" in src
    # Doit être dans le context processor inject_user.
    m = re.search(r"def inject_user\(\):(.*?)(?=\ndef |\Z)", src, re.DOTALL)
    assert m, "context processor inject_user introuvable"
    assert "user_has_permission" in m.group(1)


# ---------------------------------------------------------------------------
# 3. Menu mobile (bouton hamburger)
# ---------------------------------------------------------------------------

def test_sidebar_has_hamburger_button():
    """Un bouton hamburger doit être présent pour mobile (d-md-none)."""
    src = _read("templates/admin/base.html")
    assert 'd-md-none' in src
    assert 'data-bs-toggle="collapse"' in src
    assert 'data-bs-target="#sidebarMenu"' in src


def test_sidebar_is_collapse_class():
    """La sidebar doit avoir la classe 'collapse' pour être repliable sur
    mobile (Bootstrap collapse)."""
    src = _read("templates/admin/base.html")
    # La nav doit avoir la classe collapse (en plus de d-md-block).
    m = re.search(r'<nav id="sidebarMenu"[^>]*>', src)
    assert m, "nav#sidebarMenu introuvable"
    nav_tag = m.group(0)
    assert 'collapse' in nav_tag


# ---------------------------------------------------------------------------
# 4. Réorganisation : groupes cohérents
# ---------------------------------------------------------------------------

def test_sidebar_has_exploitation_group():
    src = _read("templates/admin/base.html")
    assert "Exploitation" in src


def test_sidebar_has_configuration_group():
    src = _read("templates/admin/base.html")
    assert "Configuration" in src


def test_sidebar_has_affichage_group():
    src = _read("templates/admin/base.html")
    assert "Affichage" in src


def test_sidebar_has_analyse_group():
    src = _read("templates/admin/base.html")
    assert "Analyse" in src


def test_sidebar_has_administration_group():
    src = _read("templates/admin/base.html")
    assert "Administration" in src


def test_sidebar_renamed_admin_to_preferences():
    """L'ancien libellé 'Admin' doit devenir plus explicite."""
    src = _read("templates/admin/base.html")
    assert "Préférences d'administration" in src
    # L'ancien libellé bare "Admin" ne doit plus être un libellé de lien.
    assert ">Admin<" not in src


def test_sidebar_renamed_patient_to_borne():
    """'Page Patient' devient 'Borne patient' (plus explicite)."""
    src = _read("templates/admin/base.html")
    assert "Borne patient" in src


def test_sidebar_renamed_announce_to_ecran():
    """'Page Annonce' devient 'Écran d'annonce'."""
    src = _read("templates/admin/base.html")
    assert "Écran d'annonce" in src


# ---------------------------------------------------------------------------
# 5. Parcours de configuration guidée (onboarding)
# ---------------------------------------------------------------------------

def test_sidebar_has_onboarding_link():
    """« Configuration guidée » est un lien vers la page du guide, visible de
    tout compte admin connecté (les étapes sont filtrées par permission plus
    loin, dans le guide lui-même)."""
    src = _read("templates/admin/base.html")
    m = re.search(r'<li class="nav-item">\s*<a[^>]*href="/admin/onboarding"',
                  src)
    assert m, "Lien « Configuration guidée » introuvable dans base.html"
    assert "Configuration guidée" in src
    # Regroupé dans la rubrique « Aide » de la sidebar.
    assert re.search(r">Aide<", src)
    assert src.index(">Aide<") < m.start()
    # Pas de garde de permission autour du lien : tout compte admin voit le guide.
    block = src[m.start():src.index("</li>", m.end())]
    assert "user_has_permission" not in block


def test_base_includes_onboarding_panel():
    """base.html inclut l'encart contextuel via onboarding_panel_context()."""
    src = _read("templates/admin/base.html")
    assert "onboarding_panel_context" in src
    assert "_onboarding_panel.html" in src
    # Inclusion après la barre d'admin, avant le contenu.
    assert src.index("_onboarding_panel.html") < src.index("{% block content %}")


def test_onboarding_templates_et_blueprint():
    """Les gabarits du guide, le blueprint et son enregistrement existent."""
    panel = _read("templates/admin/_onboarding_panel.html")
    # HTMX : les actions postent vers l'endpoint, les fragments visent le slot.
    assert "hx-post" in panel and "/admin/onboarding/action" in panel
    assert "#onboarding-panel-slot" in panel
    for label in ("Marquer comme vérifié", "Passer cette étape",
                  "Mettre en pause"):
        assert label in panel
    home = _read("templates/admin/_onboarding_home.html")
    assert "Commencer la configuration guidée" in home
    assert "Pas maintenant" in home
    checklist = _read("templates/admin/_onboarding_checklist.html")
    assert "onboarding-checklist-slot" in checklist
    app_src = _read("app.py")
    assert "admin_onboarding_bp" in app_src
