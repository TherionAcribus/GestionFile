"""Reference vers l'application, pour les taches de fond de l'ordonnanceur.

Pourquoi ce singleton subsiste malgre la fabrique d'application : les taches
APScheduler sont **persistees** dans un SQLAlchemyJobStore. Une tache deja
enregistree en base reference un chemin de fonction et des arguments serialises ;
passer l'application en argument aux fonctions de tache changerait leur signature
et casserait toutes les taches deja planifiees d'un deploiement existant. Le
singleton reste donc le moyen le plus sur pour une tache restauree depuis la base
de retrouver l'application.

Il est pose une seule fois, par ``create_app``.
"""

class AppHolder:
    app = None

    @classmethod
    def set_app(cls, app_instance):
        cls.app = app_instance

    @classmethod
    def get_app(cls):
        if cls.app is None:
            raise RuntimeError("L'instance de l'application n'est pas disponible.")
        return cls.app