"""Écritures multi-tables atomiques (Phase 8, point 6).

Plusieurs routes d'administration créaient un objet, **committaient**, puis lui
rattachaient ses relations et committaient une seconde fois ::

    db.session.add(new_counter)
    db.session.commit()             # <- 1re transaction close ici
    for activity_id in activities_ids:
        new_counter.activities.append(...)
    db.session.commit()             # <- 2e transaction

Un échec entre les deux (activité supprimée entre-temps, contrainte violée,
coupure réseau vers la base) laissait un comptoir créé **sans ses activités**, et
le ``db.session.rollback()`` du bloc ``except`` ne pouvait plus rien annuler : le
premier commit était déjà acquis. L'utilisateur voyait une erreur alors que la
moitié de l'écriture avait bien eu lieu.

``atomic()`` regroupe la séquence en une seule transaction : tout est validé, ou
rien ne l'est.
"""

from contextlib import contextmanager

from models import db


@contextmanager
def atomic(session=None):
    """Exécute le bloc dans UNE transaction : commit à la sortie, rollback si erreur.

    L'exception est propagée après le rollback — l'appelant reste responsable de
    la réponse à renvoyer, mais il a la garantie que la base n'a pas été laissée
    à moitié écrite.

    Usage ::

        with atomic():
            db.session.add(nouveau)
            db.session.flush()          # attribue l'identifiant sans clore la transaction
            nouveau.activites.extend(...)

    ``flush()`` (et non ``commit()``) est le bon outil quand on a besoin de la clé
    primaire avant la fin du bloc : il envoie l'INSERT à la base sans terminer la
    transaction, donc sans rendre l'écriture définitive.
    """
    session = session or db.session
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
