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