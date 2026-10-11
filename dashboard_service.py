"""Services du tableau de bord admin (création des cartes au démarrage).

Extrait de ``routes/admin_dashboard`` : la création des cartes du catalogue
manquantes se fait désormais dans les hooks de démarrage (init_restore),
plus au premier GET /admin. Sans dépendance de requête : ``record_audit``
tolère l'absence de contexte de requête (user/ip à None).
"""

from models import DashboardCard, db
from dashboard_catalog import card_info, missing_cards
from audit_service import record_audit
from audit_log import ACTION_CREATE, OUTCOME_SUCCESS


def ensure_catalog_cards():
    """Crée les cartes du catalogue absentes de la base (nouvelle carte après
    une mise à jour : « Aujourd'hui »…). Les cartes affichées par défaut sont
    placées en tête."""
    names = [name for (name,) in db.session.query(DashboardCard.name).all()]
    missing = missing_cards(names)
    if not missing:
        return
    first = (db.session.query(db.func.min(DashboardCard.position)).scalar() or 0) - len(missing)
    last = db.session.query(db.func.max(DashboardCard.position)).scalar() or 0
    for index, name in enumerate(missing, start=1):
        info = card_info(name)
        db.session.add(DashboardCard(
            name=name, visible=info.default_visible,
            position=(first + index) if info.default_visible else (last + index)))
    db.session.commit()
    record_audit(ACTION_CREATE, "dashboard_card", outcome=OUTCOME_SUCCESS,
                 details=f"ajout automatique : {','.join(missing)}")
