"""Hygiène des secrets : cache de jetons Spotify + configuration gitleaks.

Contexte : le fichier `.cache` de spotipy (access_token + refresh_token en
clair) a été suivi par git pendant 40 commits sans que gitleaks ne signale
quoi que ce soit — l'outil n'a aucune règle Spotify par défaut. Ces tests
verrouillent les trois pièces du correctif :

* `.cache` ignoré et non suivi ;
* une règle `spotify-oauth-token` dans `.gitleaks.toml` (sinon le scan reste
  aveugle sur ces jetons) ;
* un `.gitleaksignore` bien formé qui acquitte TOUS les commits historiques
  (sinon la CI reste rouge en permanence et plus personne ne la lit).
"""

import io
import os
import re
import shutil
import subprocess
import tomllib

import pytest

_RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_EMPREINTE = re.compile(r"^(?P<commit>[0-9a-f]{40}):(?P<chemin>[^:]+):(?P<regle>[\w-]+):(?P<ligne>\d+)$")

# Forme d'un couple de jetons Spotify (valeurs factices, mêmes préfixes et
# longueurs que celles produites par spotipy).
_ACCESS_FACTICE = "BQ" + "a1_-Z" * 50
_REFRESH_FACTICE = "AQ" + "b2_-Y" * 26


def _lire(nom):
    return io.open(os.path.join(_RACINE, nom), encoding="utf-8").read()


def _empreintes():
    lignes = [l.strip() for l in _lire(".gitleaksignore").splitlines()]
    return [l for l in lignes if l and not l.startswith("#")]


def _git(*args):
    return subprocess.run(["git", *args], cwd=_RACINE, capture_output=True, text=True)


besoin_de_git = pytest.mark.skipif(
    shutil.which("git") is None or not os.path.isdir(os.path.join(_RACINE, ".git")),
    reason="dépôt git ou binaire git indisponible",
)


def test_cache_spotipy_ignore():
    assert re.search(r"^\.cache$", _lire(".gitignore"), re.M), ".cache doit rester dans .gitignore"


@besoin_de_git
def test_cache_spotipy_non_suivi():
    suivis = _git("ls-files", "--", ".cache", ".cache/").stdout.split()
    assert suivis == [], f"fichier(s) de cache OAuth de nouveau suivi(s) par git : {suivis}"


def test_regle_gitleaks_spotify_presente_et_ciblee():
    config = tomllib.loads(_lire(".gitleaks.toml"))
    assert config["extend"]["useDefault"] is True, "les règles gitleaks par défaut doivent rester actives"

    regles = {r["id"]: r for r in config["rules"]}
    assert "spotify-oauth-token" in regles
    motif = re.compile(regles["spotify-oauth-token"]["regex"])

    assert motif.search(_ACCESS_FACTICE), "access_token Spotify non détecté"
    assert motif.search(_REFRESH_FACTICE), "refresh_token Spotify non détecté"
    # Pas de filet trop large : du texte courant ou un court identifiant ne
    # doivent pas déclencher la règle.
    assert not motif.search("BQ-court"), "règle trop large"
    assert not motif.search("Bonjour, ceci est une phrase parfaitement anodine.")


def test_empreintes_bien_formees():
    config = tomllib.loads(_lire(".gitleaks.toml"))
    connues = {r["id"] for r in config["rules"]}
    for ligne in _empreintes():
        trouve = _EMPREINTE.match(ligne)
        # gitleaks compare la ligne entière : un commentaire de fin de ligne
        # casse silencieusement l'acquittement.
        assert trouve, f"empreinte mal formée (commentaire de fin de ligne ?) : {ligne!r}"
        regle = trouve.group("regle")
        if regle.startswith("spotify-"):
            assert regle in connues, f"empreinte sur une règle inexistante : {regle}"


@besoin_de_git
def test_tous_les_commits_du_cache_sont_acquittes():
    # --diff-filter=ACMR : ajouts, copies, modifications, renommages -- mais PAS
    # les suppressions (D). gitleaks n'analyse que les lignes ajoutées d'un diff ;
    # le commit qui a retiré `.cache` du dépôt ne produit donc aucune détection
    # et n'a pas d'empreinte à acquitter. Exiger le contraire rendrait ce test
    # rouge à perpétuité pour le commit qui a justement corrigé la fuite.
    commits = _git("log", "--all", "--format=%H", "--diff-filter=ACMR", "--", ".cache").stdout.split()
    assert commits, "l'historique de .cache a disparu : test à revoir"
    acquittes = {_EMPREINTE.match(l).group("commit") for l in _empreintes() if _EMPREINTE.match(l)}
    manquants = sorted(set(commits) - acquittes)
    assert manquants == [], f"commits fuitant .cache sans empreinte dans .gitleaksignore : {manquants}"
