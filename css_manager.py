import os

class CSSManager:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.css_dir = os.path.join(app.static_folder, 'css')
        
        # S'assure que le dossier css existe
        os.makedirs(self.css_dir, exist_ok=True)
        
        # Vérifie si un fichier personnalisé existe déjà
        self.custom_css_path = os.path.join(self.css_dir, 'custom.css')
        
        @app.context_processor
        def utility_processor():
            def get_css_url():
                # Si un fichier personnalisé existe, retourne son URL
                if os.path.exists(self.custom_css_path):
                    return '/static/css/custom.css'
                # Sinon, retourne le fichier par défaut
                return '/static/css/test.css'
            return dict(get_css_url=get_css_url)

    def generate_css(self, variables):
        """
        Génère uniquement les surcharges de variables, sans dupliquer le contenu du default.css
        """
        # Génère uniquement les surcharges de variables
        css_content = ":root {\n"
        
        # Pour chaque variable modifiée
        for var_name, value in variables.items():
            css_content += f"    --{var_name}: {value};\n"
            
        css_content += "}\n"
        
        # Sauvegarde dans custom.css
        try:
            with open(self.custom_css_path, 'w') as f:
                f.write(css_content)
            return '/static/css/custom.css'
        except Exception as e:
            print(f"Erreur lors de la génération du CSS : {e}")
            return '/static/css/default.css'

    def _generate_css_content(self, variables):
        """Génère le contenu CSS complet"""
        # Copie tous les styles de base depuis default.css
        with open(os.path.join(self.css_dir, 'test.css'), 'r') as f:
            css_content = f.read()
        
        # Ajoute ou met à jour les variables personnalisées
        custom_vars = ":root {\n"
        for name, value in variables.items():
            custom_vars += f"    --{name}: {value};\n"
        custom_vars += "}\n"
        
        return custom_vars + css_content