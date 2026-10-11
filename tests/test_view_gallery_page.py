"""Page /admin/info (« Galerie média ») refaite + diaporama de l'écran d'annonce.

Couvre le noyau pur ``gallery_library`` (noms, ordre, phrase d'état), les
routes d'administration (création, affichage à l'écran, renommage, ajout,
rangement et suppression d'images) et la liste servie à l'écran.

Régressions verrouillées :
- clic sur une vignette sans effet (``bootstrap.Modal.getInstance`` nul) ;
- bouton de relance des écrans en GET sur une route POST (405) ;
- interrupteurs tous éteints après la création d'une galerie ;
- galerie au nom contenant une espace impossible à cocher ;
- diaporama relancé à la première image à chaque appel de patient.

Nom de fichier trié après test_upload_security : ce fichier enregistre le
bind 'users' dans db.metadatas, partagé entre fichiers de test.
"""

import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager
from werkzeug.security import generate_password_hash

import gallery_library as lib
import routes.admin_gallery as admin_gallery
from models import ConfigOption, Role, User, db

SERVEUR_DIR = Path(__file__).resolve().parents[1]

# PNG 1×1 valide ; des octets ajoutés après la fin du fichier donnent un
# contenu (donc une empreinte) différent sans l'invalider.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


def _png(variant=0):
    return _PNG + bytes([variant]) * variant


def _read(rel):
    return (SERVEUR_DIR / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Noyau pur
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["Promo", "Promotions d'automne".replace("'", " "),
                                  "Été 2026", "a", "Conseils-santé", "x" * 40])
def test_nom_de_galerie_accepte(name):
    assert lib.gallery_name_error(name) is None


@pytest.mark.parametrize("name", ["", "   ", "x" * 41, "a/b", "..", "promo.", "-promo",
                                  "promo#1", "a?b", "CON", "thumbnails", "list", None])
def test_nom_de_galerie_refuse(name):
    assert lib.gallery_name_error(name)


def test_nom_de_galerie_unique_sans_tenir_compte_de_la_casse():
    assert "déjà" in lib.gallery_name_error("promo", existing=["Promo"])
    assert lib.normalize_gallery_name("  Promo   été ") == "Promo été"


def test_nom_de_stockage_garde_le_nom_d_origine():
    stored = lib.stored_image_name("Promo Été (1).JPG", "0123456789abcdef.jpg")
    assert stored == "promo-ete-1-0123456789abcdef.jpg"
    assert lib.image_label(stored) == "promo ete 1"
    assert lib.content_digest(stored) == "0123456789abcdef"
    # Nom d'origine inexploitable : on garde l'empreinte seule.
    assert lib.stored_image_name("???.png", "0123456789abcdef.png") == "0123456789abcdef.png"
    assert lib.image_label("0123456789abcdef.png") == "Image sans nom"
    assert lib.image_label("affiche.png") == "affiche"
    assert lib.content_digest("affiche.png") is None


def test_selection_toleree_meme_abimee():
    assert lib.parse_selection('["a", "b"]') == ["a", "b"]
    for raw in (None, "", "{", '"a"', '{"a": 1}', "[1, null]"):
        assert lib.parse_selection(raw) == []


def test_deplacement_d_une_image():
    names = ["a", "b", "c"]
    assert lib.move_image(names, "b", "up") == ["b", "a", "c"]
    assert lib.move_image(names, "b", "down") == ["a", "c", "b"]
    assert lib.move_image(names, "c", "first") == ["c", "a", "b"]
    assert lib.move_image(names, "a", "last") == ["b", "c", "a"]
    # Impossible ou inconnu : inchangé.
    assert lib.move_image(names, "a", "up") == names
    assert lib.move_image(names, "c", "down") == names
    assert lib.move_image(names, "z", "up") == names
    assert lib.move_image(names, "a", "n'importe quoi") == names


def test_ordre_des_images(tmp_path):
    for index, name in enumerate(["c.png", "a.png", "b.jpg"]):
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (1000 + index, 1000 + index))
    (tmp_path / "notes.txt").write_text("pas une image")
    (tmp_path / "thumbnails").mkdir()
    (tmp_path / "thumbnails" / "thumb_a.png").write_bytes(b"t")

    # Sans rangement : de la plus ancienne à la plus récente.
    images = lib.list_images(tmp_path)
    assert [img["filename"] for img in images] == ["c.png", "a.png", "b.jpg"]
    assert images[1]["thumbnail"] == "thumb_a.png" and images[0]["thumbnail"] is None

    # Rangement enregistré ; une image inconnue du fichier d'ordre arrive en fin,
    # un nom disparu est ignoré.
    lib.write_order(tmp_path, ["b.jpg", "disparue.png", "c.png"])
    assert [img["filename"] for img in lib.list_images(tmp_path)] == ["b.jpg", "c.png", "a.png"]

    # Fichier d'ordre abîmé : retour à l'ordre par date, sans erreur.
    (tmp_path / lib.ORDER_FILENAME).write_text("{pas du json")
    assert [img["filename"] for img in lib.list_images(tmp_path)] == ["c.png", "a.png", "b.jpg"]
    assert lib.list_images(tmp_path / "absente") == []


def test_phrase_d_etat_du_diaporama():
    def summary(**kw):
        base = dict(display_enabled=True, selected_count=1, image_count=4,
                    display_time=30, shuffled=False)
        base.update(kw)
        return lib.slideshow_summary(**base)

    ok = summary()
    assert ok["level"] == "success" and "4 images dans 1 galerie affichée" in ok["title"]
    assert "toutes les 30 s" in ok["detail"] and "2 min" in ok["detail"]
    assert "mélangé" in summary(shuffled=True)["detail"]
    assert "1 image est affichée en continu" in summary(image_count=1)["title"]

    off = summary(display_enabled=False)
    assert off["level"] == "secondary" and "désactivé" in off["title"]
    assert summary(selected_count=0, image_count=0)["level"] == "warning"
    assert "aucune image" in summary(image_count=0)["title"]


# ---------------------------------------------------------------------------
# Routes d'administration
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path):
    static = tmp_path / "static"
    app = Flask(__name__, root_path=str(SERVEUR_DIR), static_folder=str(static))
    app.config.update(
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/test.db",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        ANNOUNCE_GALLERY_FOLDERS=str(static / "galleries"),
        ANNOUNCE_INFOS_DISPLAY=True,
        ANNOUNCE_INFOS_DISPLAY_TIME=30,
        ANNOUNCE_INFOS_MIX_FOLDERS=False,
        ANNOUNCE_INFOS_TRANSITION="fade",
        ANNOUNCE_INFOS_WIDTH=800,
        ANNOUNCE_INFOS_HEIGHT=600,
        SECURITY_LOGIN_SCREEN=False,
    )
    db.init_app(app)
    login_manager = LoginManager(app)

    @login_manager.user_loader
    def load_user(user_id):
        user = User.query.filter_by(fs_uniquifier=user_id).first()
        return user if user and user.active else None

    from routes.announce import announce_bp
    app.register_blueprint(admin_gallery.admin_gallery_bp)
    app.register_blueprint(announce_bp)
    app.add_url_rule("/login", endpoint="security.login", view_func=lambda: "")

    def test_login():
        from flask_login import login_user
        login_user(User.query.filter_by(username="admin").first())
        return jsonify(authenticated=True)

    app.add_url_rule("/_test/login", view_func=test_login, methods=["POST"])

    with app.app_context():
        db.create_all()
        admin = User(username="admin", email="a@a.a",
                     password=generate_password_hash("x"), active=True)
        admin.roles.append(Role(name="admin", admin_gallery=True))
        db.session.add(admin)
        db.session.commit()

    app.sent = []

    def fake_communikation(stream, data=None, flag=None, event="update", **kwargs):
        app.sent.append((stream, event))

    # Miniature factice : Pillow n'est pas requis par la suite de référence.
    with patch.object(admin_gallery, "communikation", fake_communikation), \
            patch.object(admin_gallery, "generate_thumbnail", lambda data: b"mini"), \
            patch("ui_feedback.communikation", lambda *a, **k: None):
        yield app


@pytest.fixture()
def client(app):
    c = app.test_client()
    c.post("/_test/login")
    return c


def _dir(app, *parts):
    return Path(app.config["ANNOUNCE_GALLERY_FOLDERS"]).joinpath(*parts)


def _selection(app):
    with app.app_context():
        option = ConfigOption.query.filter_by(config_key="announce_infos_gallery").first()
        return json.loads(option.value_str) if option else []


def _create(client, name):
    return client.post("/admin/gallery/create_gallery", data={"name": name})


def _upload(client, gallery, *files):
    data = {"photos": [(io.BytesIO(content), filename) for filename, content in files]}
    return client.post(f"/admin/gallery/upload/{gallery}", data=data,
                       content_type="multipart/form-data")


def _images(app, gallery):
    return [img["filename"] for img in lib.list_images(_dir(app, gallery))]


def _screen_refreshes(app):
    return app.sent.count(("update_screen", "refresh_gallery"))


def test_acces_reserve(app):
    assert app.test_client().get("/admin/gallery/list").status_code == 302


def test_liste_vide(client):
    html = client.get("/admin/gallery/list").get_data(as_text=True)
    assert "Aucune galerie pour l" in html
    assert "Aucune galerie n&#39;est affichée" in html or "Aucune galerie n'est affichée" in html


def test_creation(app, client):
    response = _create(client, "  Promo   été ")
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "Promo été" in html
    assert "galleryChanged" in response.headers["HX-Trigger"]
    assert _dir(app, "Promo été").is_dir()
    # La vue ouverte propose l'ajout d'images avec ses limites.
    assert 'accept=".gif,.jpeg,.jpg,.png,.webp"' in html and "16 Mo par image" in html


@pytest.mark.parametrize("name", ["", "a/b", "..", "promo#1", "x" * 41])
def test_creation_refusee_sans_rien_remplacer(app, client, name):
    response = _create(client, name)
    assert response.status_code == 204
    assert lib.list_galleries(_dir(app)) == []


def test_creation_en_double_refusee(app, client):
    _create(client, "Promo")
    assert _create(client, "promo").status_code == 204
    assert lib.list_galleries(_dir(app)) == ["Promo"]


def test_afficher_a_l_ecran_y_compris_un_nom_avec_espace(app, client):
    _create(client, "Promo Noel")
    _create(client, "Conseils")
    response = client.post("/admin/gallery/choose_gallery",
                           data={"gallery_name": "Promo Noel", "checked": "true"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and _selection(app) == ["Promo Noel"]
    assert html.count(" checked") == 1 and "Dans le diaporama" in html
    assert _screen_refreshes(app) == 1

    # Créer une autre galerie ne « décoche » pas les interrupteurs existants.
    _create(client, "Nouvelle")
    assert client.get("/admin/gallery/list").get_data(as_text=True).count(" checked") == 1

    # Case décochée : le navigateur n'envoie pas « checked ».
    client.post("/admin/gallery/choose_gallery", data={"gallery_name": "Promo Noel"})
    assert _selection(app) == []


def test_afficher_une_galerie_inconnue(app, client):
    response = client.post("/admin/gallery/choose_gallery",
                           data={"gallery_name": "Fantôme", "checked": "true"})
    assert response.status_code == 404 and _selection(app) == []
    assert client.post("/admin/gallery/choose_gallery",
                       data={"gallery_name": "..", "checked": "true"}).status_code == 400


def test_ajout_d_images(app, client):
    _create(client, "Promo")
    response = _upload(client, "Promo",
                       ("Affiche Été.png", _png(1)),
                       ("copie.png", _png(1)),          # même contenu
                       ("faux.png", b"<?php echo 1; ?>"),
                       ("notes.txt", b"bonjour"))
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "galleryChanged" in response.headers["HX-Trigger"]
    stored = _images(app, "Promo")
    assert len(stored) == 1 and stored[0].startswith("affiche-ete-") and stored[0].endswith(".png")
    assert "affiche ete" in html and "thumbnails/thumb_affiche-ete-" in html
    assert _dir(app, "Promo", "thumbnails", f"thumb_{stored[0]}").read_bytes() == b"mini"
    # Galerie non affichée : inutile de solliciter les écrans.
    assert _screen_refreshes(app) == 0

    # Le même fichier renvoyé plus tard n'est pas dupliqué.
    _upload(client, "Promo", ("encore.png", _png(1)))
    assert len(_images(app, "Promo")) == 1

    client.post("/admin/gallery/choose_gallery", data={"gallery_name": "Promo", "checked": "true"})
    _upload(client, "Promo", ("deux.png", _png(2)))
    assert len(_images(app, "Promo")) == 2 and _screen_refreshes(app) == 2


def test_ajout_refuse(app, client):
    _create(client, "Promo")
    assert client.post("/admin/gallery/upload/Promo", data={},
                       content_type="multipart/form-data").status_code == 204
    trop = [(f"i{n}.png", _png(n + 1)) for n in range(admin_gallery.MAX_IMAGES_PER_UPLOAD + 1)]
    assert _upload(client, "Promo", *trop).status_code == 204
    assert _images(app, "Promo") == []
    assert _upload(client, "Absente", ("a.png", _png(1))).status_code == 404
    assert not _dir(app, "Absente").exists()


def test_miniature_creee_a_l_affichage_pour_les_images_anciennes(app, client):
    _create(client, "Promo")
    _dir(app, "Promo", "ancienne.jpg").write_bytes(b"\xff\xd8\xff contenu")
    html = client.get("/admin/gallery/images_list/Promo").get_data(as_text=True)
    assert "thumbnails/thumb_ancienne.jpg" in html
    assert _dir(app, "Promo", "thumbnails", "thumb_ancienne.jpg").is_file()
    # Agrandir ouvre toujours l'image d'origine.
    assert 'data-full-src="/static/galleries/Promo/ancienne.jpg"' in html


def test_rangement_des_images(app, client):
    _create(client, "Promo")
    for index, name in enumerate(["a.png", "b.png", "c.png"]):
        _dir(app, "Promo", name).write_bytes(_png(index + 1))
        os.utime(_dir(app, "Promo", name), (1000 + index, 1000 + index))

    response = client.post("/admin/gallery/move_image/Promo/c.png", data={"direction": "up"})
    html = response.get_data(as_text=True)
    assert _images(app, "Promo") == ["a.png", "c.png", "b.png"]
    assert html.index("/c.png") < html.index("/b.png")
    assert "galleryChanged" in response.headers["HX-Trigger"]

    # Déjà en tête : rien à enregistrer, rien à diffuser.
    app.sent.clear()
    response = client.post("/admin/gallery/move_image/Promo/a.png", data={"direction": "up"})
    assert response.status_code == 200 and "HX-Trigger" not in response.headers
    assert app.sent == []


def test_suppression_d_une_image(app, client):
    _create(client, "Promo")
    _upload(client, "Promo", ("a.png", _png(1)), ("b.png", _png(2)))
    first, second = _images(app, "Promo")
    client.post(f"/admin/gallery/move_image/Promo/{second}", data={"direction": "up"})

    response = client.delete(f"/admin/gallery/delete_image/Promo/{second}")
    assert response.status_code == 200 and _images(app, "Promo") == [first]
    assert not _dir(app, "Promo", "thumbnails", f"thumb_{second}").exists()
    assert lib.read_order(_dir(app, "Promo")) == [first]

    # Le fichier d'ordre et les chemins douteux ne se suppriment pas par cette route.
    client.delete(f"/admin/gallery/delete_image/Promo/{lib.ORDER_FILENAME}")
    assert _dir(app, "Promo", lib.ORDER_FILENAME).is_file()
    assert client.delete("/admin/gallery/delete_image/Promo/..").status_code in (400, 404, 405)


def test_renommer(app, client):
    _create(client, "Promo")
    _create(client, "Conseils")
    _upload(client, "Promo", ("a.png", _png(1)))
    client.post("/admin/gallery/choose_gallery", data={"gallery_name": "Promo", "checked": "true"})

    assert client.post("/admin/gallery/rename/Promo", data={"name": "conseils"}).status_code == 204
    assert client.post("/admin/gallery/rename/Promo", data={"name": "a/b"}).status_code == 204
    assert _dir(app, "Promo").is_dir()

    response = client.post("/admin/gallery/rename/Promo", data={"name": "Automne"})
    assert response.status_code == 200 and "Automne" in response.get_data(as_text=True)
    assert not _dir(app, "Promo").exists() and len(_images(app, "Automne")) == 1
    # La galerie reste affichée à l'écran sous son nouveau nom.
    assert _selection(app) == ["Automne"]


def test_supprimer_une_galerie(app, client):
    _create(client, "Promo")
    _upload(client, "Promo", ("a.png", _png(1)))
    client.post("/admin/gallery/choose_gallery", data={"gallery_name": "Promo", "checked": "true"})

    response = client.delete("/admin/gallery/delete_gallery/Promo")
    assert response.status_code == 200 and "galleryChanged" in response.headers["HX-Trigger"]
    assert not _dir(app, "Promo").exists() and _selection(app) == []
    assert client.delete("/admin/gallery/delete_gallery/Promo").status_code == 404


def test_supprimer_une_galerie_au_contenu_inattendu_ne_supprime_rien(app, client):
    _create(client, "Promo")
    _upload(client, "Promo", ("a.png", _png(1)))
    _dir(app, "Promo", "sous-dossier").mkdir()
    assert client.delete("/admin/gallery/delete_gallery/Promo").status_code == 400
    assert len(_images(app, "Promo")) == 1


# ---------------------------------------------------------------------------
# Écran d'annonce
# ---------------------------------------------------------------------------

def test_diaporama_de_l_ecran(app, client):
    assert "swiper" not in client.get("/announce/init_gallery").get_data(as_text=True)

    for gallery, names in (("Zoo", ["z1.png"]), ("automne", ["b.png", "a.png"]), ("Cachée", ["h.png"])):
        _create(client, gallery)
        for index, name in enumerate(names):
            _dir(app, gallery, name).write_bytes(_png(index + 1))
            os.utime(_dir(app, gallery, name), (1000 + index, 1000 + index))
    client.get("/admin/gallery/images_list/automne")        # crée thumbnails/
    lib.write_order(_dir(app, "automne"), ["a.png", "b.png"])
    with app.app_context():
        db.session.add(ConfigOption(config_key="announce_infos_gallery",
                                    value_str=json.dumps(["Zoo", "automne", "Disparue"])))
        db.session.commit()

    html = client.get("/announce/init_gallery").get_data(as_text=True)
    order = [html.index(part) for part in ("automne/a.png", "automne/b.png", "Zoo/z1.png")]
    assert order == sorted(order)
    assert "Cachée" not in html and "thumb_" not in html and lib.ORDER_FILENAME not in html
    assert 'data-swiper-delay="30"' in html and 'data-swiper-effect="fade"' in html


# ---------------------------------------------------------------------------
# Front (statique)
# ---------------------------------------------------------------------------

def test_page_et_scripts():
    page = _read("templates/admin/gallery.html")
    # La relance des écrans est un POST (la route refuse GET).
    assert 'hx-post="/announce/refresh"' in page and 'hx-get="/announce/refresh"' not in page
    assert "galleryChanged from:body" in page
    assert page.count('id="invisible"') == 0          # déjà fourni par base.html

    cards = _read("templates/admin/gallery_list_galleries.html")
    assert "form-switch" in cards and "lever" not in cards
    assert "data-param-checked" not in cards            # sélecteur cassé par les espaces

    script = _read("static/js/admin_gallery.js")
    assert "Modal.getOrCreateInstance" in script and "Modal.getInstance" not in script


def test_le_diaporama_n_est_relance_que_par_son_propre_rechargement():
    script = _read("static/js/announce.js")
    block = script[script.index("// --- Carrousel d'informations"):]
    assert "evt.target !== zone" in block
    assert "crossFade: true" in block
    assert "screenSocket.on('refresh_gallery'" in script
    assert 'hx-trigger="load, refresh_gallery"' in _read("templates/announce/announce.html")
