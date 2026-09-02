"""Phase 8, point 5 — cœur de validation des formulaires.

Tests du module **pur** ``form_validation`` : aucune application Flask, aucune
base. On vérifie que les règles autrefois recopiées à la main dans chaque route
(champ obligatoire, longueur, conversion) sont désormais appliquées de façon
uniforme, et qu'une entrée aberrante produit une *erreur* et non une exception.
"""

import pytest

from form_validation import (
    BOOLEEN,
    ENTIER,
    LISTE_ENTIERS,
    TEXTE,
    Champ,
    extraire,
    valider,
)


# --- Champs obligatoires ----------------------------------------------------

def test_champ_obligatoire_absent():
    schema = (Champ("name", obligatoire=True, libelle="Le nom"),)
    valeurs, erreurs = valider({}, schema)
    assert valeurs == {}
    assert erreurs == ["Le nom est obligatoire."]


@pytest.mark.parametrize("vide", ["", "   ", "\t", None])
def test_champ_obligatoire_vide_ou_blanc(vide):
    """Une chaîne d'espaces ne vaut pas une valeur : c'est le cas que les
    contrôles `if not name` attrapaient, mais pas `if request.form.get('name') == ''`."""
    schema = (Champ("name", obligatoire=True, libelle="Le nom"),)
    _, erreurs = valider({"name": vide}, schema)
    assert erreurs == ["Le nom est obligatoire."]


def test_valeur_est_nettoyee_des_espaces():
    schema = (Champ("name", obligatoire=True),)
    valeurs, erreurs = valider({"name": "  Comptoir 1  "}, schema)
    assert erreurs == []
    assert valeurs["name"] == "Comptoir 1"


def test_champ_facultatif_absent_prend_le_defaut():
    schema = (Champ("message", defaut=""),)
    valeurs, erreurs = valider({}, schema)
    assert erreurs == []
    assert valeurs["message"] == ""


# --- Longueur ---------------------------------------------------------------

def test_longueur_maximale_respectee():
    schema = (Champ("letter", libelle="La lettre", longueur_max=1),)
    valeurs, erreurs = valider({"letter": "A"}, schema)
    assert (valeurs["letter"], erreurs) == ("A", [])


def test_longueur_maximale_depassee():
    """Le `maxlength` du gabarit est un confort client : le serveur doit border."""
    schema = (Champ("letter", libelle="La lettre", longueur_max=1),)
    _, erreurs = valider({"letter": "ABC"}, schema)
    assert erreurs == ["La lettre ne doit pas dépasser 1 caractères."]


# --- Conversions ------------------------------------------------------------

@pytest.mark.parametrize("brut,attendu", [
    ("true", True), ("True", True), ("on", True), ("1", True), ("oui", True),
    ("false", False), ("", False), (None, False), ("nimporte quoi", False),
])
def test_booleen(brut, attendu):
    schema = (Champ("notification", type=BOOLEEN),)
    valeurs, erreurs = valider({"notification": brut}, schema)
    assert erreurs == []
    assert valeurs["notification"] is attendu


def test_entier_valide():
    schema = (Champ("staff_id", type=ENTIER),)
    valeurs, _ = valider({"staff_id": " 42 "}, schema)
    assert valeurs["staff_id"] == 42


def test_entier_invalide_produit_une_erreur_pas_une_exception():
    """`int(request.form.get(...))` levait une ValueError non rattrapée."""
    schema = (Champ("staff_id", type=ENTIER, libelle="Le membre"),)
    valeurs, erreurs = valider({"staff_id": "abc"}, schema)
    assert erreurs == ["Le membre doit être un nombre entier."]
    assert "staff_id" not in valeurs


def test_entier_absent_vaut_le_defaut():
    schema = (Champ("staff_id", type=ENTIER),)
    valeurs, erreurs = valider({"staff_id": ""}, schema)
    assert erreurs == []
    assert valeurs["staff_id"] is None


def test_liste_entiers():
    schema = (Champ("activities", type=LISTE_ENTIERS),)
    valeurs, erreurs = valider({"activities": ["1", "2", "3"]}, schema)
    assert (valeurs["activities"], erreurs) == ([1, 2, 3], [])


def test_liste_entiers_vide():
    schema = (Champ("activities", type=LISTE_ENTIERS),)
    valeurs, erreurs = valider({"activities": []}, schema)
    assert (valeurs["activities"], erreurs) == ([], [])


def test_liste_entiers_avec_valeur_invalide():
    schema = (Champ("activities", type=LISTE_ENTIERS, libelle="Les activités"),)
    _, erreurs = valider({"activities": ["1", "oups"]}, schema)
    assert erreurs == ["Les activités contient une valeur invalide."]


# --- Choix restreints -------------------------------------------------------

def test_choix_autorise():
    schema = (Champ("ordre", choix=("order", "random")),)
    valeurs, erreurs = valider({"ordre": "random"}, schema)
    assert (valeurs["ordre"], erreurs) == ("random", [])


def test_choix_refuse():
    schema = (Champ("ordre", libelle="L'ordre", choix=("order", "random")),)
    _, erreurs = valider({"ordre": "n'importe quoi"}, schema)
    assert erreurs == ["L'ordre a une valeur non autorisée."]


# --- Normalisation ----------------------------------------------------------

def test_normalisation_appliquee_avant_controle():
    schema = (Champ("initials", obligatoire=True, normaliser=str.upper),)
    valeurs, _ = valider({"initials": " ab "}, schema)
    assert valeurs["initials"] == "AB"


# --- Plusieurs erreurs ------------------------------------------------------

def test_toutes_les_erreurs_sont_remontees_dans_l_ordre_du_schema():
    schema = (
        Champ("name", obligatoire=True, libelle="Le nom"),
        Champ("letter", obligatoire=True, libelle="La lettre"),
    )
    _, erreurs = valider({}, schema)
    assert erreurs == ["Le nom est obligatoire.", "La lettre est obligatoire."]


def test_resultat_est_faux_si_erreurs_et_vrai_sinon():
    schema = (Champ("name", obligatoire=True),)
    assert not valider({}, schema)
    assert valider({"name": "ok"}, schema)


# --- Extraction depuis un formulaire ---------------------------------------

def test_extraire_utilise_getlist_pour_les_listes():
    schema = (Champ("name"), Champ("activities", type=LISTE_ENTIERS))
    formulaire = {"name": "Comptoir", "activities": ["1", "2"]}
    brut = extraire(schema,
                    get=lambda cle: formulaire.get(cle),
                    getlist=lambda cle: formulaire.get(cle, []))
    assert brut == {"name": "Comptoir", "activities": ["1", "2"]}


def test_extraire_sans_getlist_donne_une_liste_vide():
    schema = (Champ("activities", type=LISTE_ENTIERS),)
    assert extraire(schema, get=lambda cle: None) == {"activities": []}


# --- Garde-fou de déclaration ----------------------------------------------

def test_type_de_champ_inconnu_refuse_a_la_declaration():
    with pytest.raises(ValueError):
        Champ("x", type="chaine_magique")


def test_type_par_defaut_est_texte():
    assert Champ("x").type == TEXTE
