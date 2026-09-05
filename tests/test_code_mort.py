"""Phase 8, point 3 — le code retiré ne doit pas revenir (cliquet).

Deux familles de vérifications :

1. **Cliquet** : les gabarits, macros et routes supprimés restent absents. Un
   copier-coller depuis une ancienne branche les ferait réapparaître sans que
   rien ne le signale.

2. **Invariants** qui auraient permis de détecter ce code mort plus tôt :
   tout ``render_template`` littéral doit viser un gabarit existant, et toute
   fonction décorée par ``@…route`` doit être appelable avec les seules
   variables de l'URL.

L'invariant 2 a réellement attrapé deux défauts :

- ``/pharmacists`` rendait ``pharmacists.html``, **fichier inexistant** : la
  route répondait 500 à chaque appel.
- ``/patient/conclusion_page/<call_number>`` était posée sur une fonction dont
  l'argument ``print_ticket`` était **obligatoire** : Flask n'appelant la vue
  qu'avec les variables de l'URL, tout accès direct levait un ``TypeError``
  (500). C'était le chemin de repli non-HTMX de l'inscription patient.
"""

import ast
import os
import re

import pytest

_SERVEUR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_TEMPLATES = os.path.join(_SERVEUR, "templates")


def _lire(rel):
    with open(os.path.join(_SERVEUR, rel), encoding="utf-8") as fh:
        return fh.read()


def _fichiers_python():
    for base, dirs, noms in os.walk(_SERVEUR):
        dirs[:] = [d for d in dirs
                   if d not in {".venv", "__pycache__", "migrations", ".git",
                                ".ruff_cache", "tests", "instance"}]
        for nom in noms:
            if nom.endswith(".py"):
                yield os.path.join(base, nom)


# ---------------------------------------------------------------------------
# 1. Cliquet : ce qui a été retiré reste retiré
# ---------------------------------------------------------------------------

GABARITS_RETIRES = [
    # Fichier vide (0 octet).
    "admin/macro_save_restore.html",
    # Ébauche cassée : importait security/_macros.html (interne à
    # flask_security) et appelait render_field_with_errors() sans argument.
    "security/custom_login.html",
    # `image_url` / `image_announcement` ne sont référencés nulle part ailleurs.
    "htmx/display_image_announcement.html",
    # Reliquat de mise au point (« Grippe / Covid », classe .btn-test).
    "htmx/patient_right_page_test.html",
    # `save_to_history` n'existait que dans ce fichier.
    "admin/queue_list_modal_confirm_delete.html",
    # Rendu par la seule route /patients_queue, elle-même retirée.
    "htmx/patients_queue.html",
    # Ancienne interface (HTML malformé : <h1> fermé par </h2>).
    "menu_pharmacie_counters.html",
    "menu_pharmacie_pharmacists.html",
]


@pytest.mark.parametrize("rel", GABARITS_RETIRES)
def test_gabarit_retire_reste_absent(rel):
    chemin = os.path.join(_TEMPLATES, rel)
    assert not os.path.exists(chemin), (
        f"{rel} a été retiré (Phase 8 pt 3) : il ne doit pas revenir")


MACROS_RETIREES = [
    # Jamais appelée ; css_unit_input couvre le besoin.
    "css_textbloc",
    # Jamais appelée, et cassée : le corps interpole `source`, qui n'est pas un
    # paramètre de la macro.
    "number_input",
]


@pytest.mark.parametrize("nom", MACROS_RETIREES)
def test_macro_retiree_reste_absente(nom):
    source = _lire("templates/admin/macros.html")
    assert not re.search(r"\{%-?\s*macro\s+" + nom + r"\s*\(", source), (
        f"la macro {nom}() a été retirée (Phase 8 pt 3)")


ROUTES_RETIREES = [
    # Doublon obsolète de /admin/update_css_variable (6 références vivantes).
    ("routes/admin_config.py", "/admin/update_css_variable_old"),
    # Rendait htmx/patients_queue.html ; la file comptoir passe par
    # /counter/patients_queue_for_counter/<id>.
    ("routes/calling.py", "/patients_queue"),
    # Rendait pharmacists.html, fichier inexistant → 500 systématique.
    ("routes/admin_staff.py", "/pharmacists"),
    # Rendait patients.html, fichier inexistant → 500 systématique. Invisible
    # à une recherche d'URL : « /patients » matche backref('patients') dans
    # models.py ; c'est l'invariant render_template ci-dessous qui l'a trouvée.
    ("routes/calling.py", "/patients/<lang>"),
]


@pytest.mark.parametrize("fichier,regle", ROUTES_RETIREES)
def test_route_retiree_reste_absente(fichier, regle):
    source = _lire(fichier)
    assert f"route('{regle}'" not in source, (
        f"la route {regle} a été retirée (Phase 8 pt 3)")


def test_bibliotheque_anime_absente():
    """anime.js (3 builds, ~104 Ko) n'était chargé par aucune page."""
    assert not os.path.exists(os.path.join(_SERVEUR, "static", "js", "anime-master"))


# ---------------------------------------------------------------------------
# 2. Invariants : les défauts qui ont produit ce code mort
# ---------------------------------------------------------------------------

def _gabarits_rendus():
    """(fichier, ligne, nom) de chaque render_template à nom littéral."""
    for chemin in _fichiers_python():
        with open(chemin, encoding="utf-8") as fh:
            try:
                arbre = ast.parse(fh.read())
            except SyntaxError:
                continue
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Call):
                continue
            f = noeud.func
            nom_f = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if nom_f != "render_template" or not noeud.args:
                continue
            arg = noeud.args[0]
            # Les noms construits dynamiquement (f-string des cartes du tableau
            # de bord) ne sont pas vérifiables statiquement : on les ignore.
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield (os.path.relpath(chemin, _SERVEUR), noeud.lineno, arg.value)


def test_tout_render_template_vise_un_gabarit_existant():
    """Un render_template littéral doit pointer sur un fichier présent.

    C'est ce qui manquait pour voir que /pharmacists rendait un gabarit
    inexistant et répondait donc 500 à chaque appel.
    """
    manquants = []
    for fichier, ligne, nom in _gabarits_rendus():
        chemin = os.path.join(_TEMPLATES, nom.lstrip("/").replace("/", os.sep))
        if not os.path.exists(chemin):
            manquants.append(f"{fichier}:{ligne} -> {nom}")
    assert not manquants, "render_template vers un gabarit inexistant :\n" + "\n".join(manquants)


def _vues():
    """(fichier, ligne, fonction, regle, arguments obligatoires sans defaut)."""
    for chemin in _fichiers_python():
        with open(chemin, encoding="utf-8") as fh:
            try:
                arbre = ast.parse(fh.read())
            except SyntaxError:
                continue
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in noeud.decorator_list:
                if not (isinstance(deco, ast.Call)
                        and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr == "route"
                        and deco.args
                        and isinstance(deco.args[0], ast.Constant)):
                    continue
                regle = deco.args[0].value
                args = noeud.args
                positionnels = args.posonlyargs + args.args
                # Les arguments sans valeur par defaut, en partant de la fin.
                nb_sans_defaut = len(positionnels) - len(args.defaults)
                obligatoires = [a.arg for a in positionnels[:nb_sans_defaut]]
                yield (os.path.relpath(chemin, _SERVEUR), noeud.lineno,
                       noeud.name, regle, obligatoires)


def test_toute_vue_est_appelable_avec_les_variables_de_l_url():
    """Flask n'appelle une vue qu'avec les variables déclarées dans la règle.

    Tout autre argument obligatoire garantit un TypeError (500) à l'appel.
    Régression : /patient/conclusion_page/<call_number> exigeait aussi
    ``print_ticket`` — la redirection non-HTMX de l'inscription patient menait
    donc à une erreur 500.
    """
    fautives = []
    for fichier, ligne, fonction, regle, obligatoires in _vues():
        variables = set(re.findall(r"<(?:[^:<>]+:)?([^<>]+)>", regle))
        manquants = [a for a in obligatoires if a not in variables]
        if manquants:
            fautives.append(
                f"{fichier}:{ligne} {fonction}{tuple(manquants)} "
                f"non fournis par la regle {regle}")
    assert not fautives, (
        "vues dont un argument obligatoire n'est pas dans l'URL :\n"
        + "\n".join(fautives))


# ---------------------------------------------------------------------------
# 3. Routes désactivées (décorateur commenté), en attente d'arbitrage
# ---------------------------------------------------------------------------
# Ces routes n'ont aucune référence dans le dépôt (gabarits, JS, App_Comptoir,
# borne), mais leur suppression engage plus qu'un nettoyage : fonctionnalité
# inaboutie à finir, outil d'exploitation utilisé à la main, ou compatibilité
# avec un client déjà déployé. Elles sont donc **désactivées** plutôt que
# retirées : le code reste lisible et la réactivation tient en une ligne, ce qui
# permet de les reprendre et de les tester une par une.
#
# Réactiver une route = décommenter son décorateur ET retirer son entrée
# ci-dessous. Les deux gestes ensemble : c'est ce qui rend la décision explicite
# plutôt que subie.

ROUTES_DESACTIVEES = [
    # Réglage de l'algorithme : aucun formulaire ne l'envoie.
    ("routes/admin_algo.py", "/admin/algo/change_overtaken_limit"),
    # Personnalisation du tableau de bord : sélection, redimensionnement, ajout
    # de carte. L'interface actuelle n'expose aucune de ces trois actions.
    ("routes/admin_dashboard.py", "/admin/dashboard/valide_select"),
    ("routes/admin_dashboard.py", "/admin/dashboard/resize"),
    ("routes/admin_dashboard.py", "/admin/dashboard/add"),
    # Lecteur Spotify : commandes et bibliothèque.
    ("routes/admin_music.py", "/admin/music/player/<action>"),
    # Corps VIDE (`# Code existant...` puis `pass`) : n'a jamais rien fait.
    ("routes/admin_music.py", "/admin/music/save_options"),
    ("routes/admin_music.py", "/show_saved_tracks"),
    # Réinitialisation du compte admin : protégée par permission et tracée dans
    # le journal d'audit (pt 7), mais sans bouton dans l'interface. Ressemble à
    # un filet de secours volontaire — à confirmer avant suppression.
    ("routes/admin_security.py", "/admin/reset_admin"),
    # Collecte des clés à traduire (outil de maintenance des traductions).
    ("routes/admin_translation.py", "/admin/translations/collect"),
    # Sondes de diagnostic protégées par jeton : émission Socket.IO de test et
    # ping RabbitMQ. Jamais appelées par le code, utilisables à la main.
    ("routes/api_system.py", "/send_message"),
    ("routes/api_system.py", "/test_local"),
    ("routes/calling.py", "/patient_right_page_default"),
    # Ancienne interface de gestion du personnel, remplacee par /admin/staff
    # (seul lien de la barre de navigation). Tout l'ilot etait deja hors
    # service : son point d'entree /pharmacists rendait un gabarit inexistant
    # (500), et le tableau #pharmacist_table vise par le formulaire d'ajout
    # vivait dans menu_pharmacie_pharmacists.html, reference par rien.
    # update_pharmacist redirigeait par ailleurs vers /pharmacists.
    ("routes/admin_staff.py", "/update_pharmacist/<int:pharmacist_id>"),
    ("routes/admin_staff.py", "/add_pharmacist"),
    ("routes/admin_staff.py", "/new_pharmacist_form"),
    # App_Comptoir documente explicitement l'avoir abandonnée au profit de
    # /api/counter/<id>/state (main.py : « l'ancien couple
    # connexion_for_app_init()/handle_init_app() … a été supprimé »). Un poste
    # comptoir resté en version ancienne l'appellerait encore : ne la retirer
    # définitivement qu'une fois le parc à jour.
    ("routes/counter.py", "/app/counter/init_app"),
]


@pytest.mark.parametrize("fichier,regle", ROUTES_DESACTIVEES)
def test_route_desactivee_le_reste(fichier, regle):
    """Le décorateur doit rester commenté tant que la route n'est pas arbitrée."""
    source = _lire(fichier)
    actif = re.search(r"^\s*@\w+\.route\('" + re.escape(regle) + r"'", source, re.M)
    assert not actif, (
        f"{regle} a été réactivée : retirer son entrée de ROUTES_DESACTIVEES")
    assert f"# @" in source and re.search(
        r"^#\s*@\w+\.route\('" + re.escape(regle) + r"'", source, re.M), (
        f"{regle} n'est plus dans le fichier : si elle a été supprimée, "
        f"deplacer son entree vers ROUTES_RETIREES")


def test_marqueur_pt3_sur_chaque_route_desactivee():
    """Chaque décorateur commenté porte le marqueur explicatif [PT3].

    Sans lui, un décorateur commenté ressemble à du code laissé en plan.
    """
    for fichier, regle in ROUTES_DESACTIVEES:
        lignes = _lire(fichier).split("\n")
        i = next(i for i, l in enumerate(lignes)
                 if re.match(r"^#\s*@\w+\.route\('" + re.escape(regle) + r"'", l))
        contexte = "\n".join(lignes[max(0, i - 5):i])
        assert "[PT3]" in contexte, (
            f"{regle} : décorateur commenté sans marqueur [PT3] au-dessus")
