import os
from variables import MultiCssVariableManager


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
        
        # Définit les configurations pour chaque mode
        self.css_configs = {
            "patient": {
                "source": "patient.css",
                "custom": "custom.css"
            },
            "announce": {
                "source": "display.css",
                "custom": "custom_display.css"
            },
            "phone": {
                "source": "phone.css",
                "custom": "custom_phone.css"
            }
        }

        # Génération des fichiers CSS au démarrage
        for mode in self.css_configs:
            variables = MultiCssVariableManager(app=self.app).get_all_variables(mode)
            self.generate_css(variables, mode=mode)
        
        @app.context_processor
        def utility_processor():
            def get_css_url(mode="patient"):
                custom_path = os.path.join(self.css_dir, self.css_configs[mode]["custom"])
                # Si un fichier personnalisé existe, retourne son URL
                if os.path.exists(custom_path):
                    return f'/static/css/{self.css_configs[mode]["custom"]}'
                # Sinon, retourne le fichier source
                return f'/static/css/{self.css_configs[mode]["source"]}'
            return dict(get_css_url=get_css_url)
        

    def generate_css(self, variables, mode="patient"):
        """
        Génère uniquement les surcharges de variables pour le mode spécifié
        """
        if mode not in self.css_configs:
            raise ValueError("Mode must be either 'patient' or 'announce'")
            
        custom_path = os.path.join(self.css_dir, self.css_configs[mode]["custom"])
        
        css_content = ":root {\n"
        
        # Pour chaque variable modifiée
        for var_name, value in variables.items():
            css_content += f"    --{var_name}: {value};\n"
            
        css_content += "}\n"
        
        # Sauvegarde dans le fichier personnalisé approprié
        try:
            with open(custom_path, 'w') as f:
                f.write(css_content)
            return f'/static/css/{self.css_configs[mode]["custom"]}'
        except Exception as e:
            print(f"Erreur lors de la génération du CSS : {e}")
            return f'/static/css/{self.css_configs[mode]["source"]}'

    def _generate_css_content(self, variables, mode="patient"):
        """Génère le contenu CSS complet"""
        source_path = os.path.join(self.css_dir, self.css_configs[mode]["source"])
        
        # Copie tous les styles de base depuis le fichier source
        with open(source_path, 'r') as f:
            css_content = f.read()
        
        # Ajoute ou met à jour les variables personnalisées
        custom_vars = ":root {\n"
        for name, value in variables.items():
            custom_vars += f"    --{name}: {value};\n"
        custom_vars += "}\n"
        
        return custom_vars + css_content