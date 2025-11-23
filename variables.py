from flask import Flask, current_app
from typing import Dict, Optional, Type
from dataclasses import dataclass
from models import db, PatientCssVariable, AnnounceCssVariable, PhoneCssVariable

@dataclass
class CssSource:
    """Configuration d'une source de variables CSS"""
    model: Type[db.Model]
    config_key: str
    description: str

class MultiCssVariableManager:
    def __init__(self, app=None):
        # Définition des sources de variables CSS
        self.sources = {
            'patient': CssSource(
                model=PatientCssVariable,
                config_key='PATIENT_CSS_VARIABLES',
                description='Variables CSS pour les patients'
            ),
            'announce': CssSource(
                model=AnnounceCssVariable,
                config_key='ANNOUNCE_CSS_VARIABLES',
                description='Variables CSS pour les annonces'
            ),
            'phone': CssSource(
                model=PhoneCssVariable,
                config_key='PHONE_CSS_VARIABLES',
                description='Variables CSS pour le téléphone'
            )
        }
        
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask):
        # Chargement initial de toutes les sources
        self._load_all_css_variables(app)
        
        # Ajout du gestionnaire à l'application
        app.css_variable_manager = self

    def _load_css_variables_for_source(self, app: Flask, source_name: str) -> None:
        """Charge les variables CSS pour une source spécifique"""
        source = self.sources[source_name]
        with app.app_context():
            css_vars = source.model.query.all()
            app.config[source.config_key] = {
                var.variable: var.value for var in css_vars
            }

    def _load_all_css_variables(self, app: Flask) -> None:
        """Charge toutes les variables CSS de toutes les sources"""
        for source_name in self.sources:
            self._load_css_variables_for_source(app, source_name)

    def get_variable(self, source_name: str, variable_name: str) -> Optional[str]:
        """Récupère une variable CSS d'une source spécifique"""
        source = self.sources[source_name]
        return current_app.config[source.config_key].get(variable_name)

    def get_all_variables(self, source_name: str) -> Dict[str, str]:
        """Récupère toutes les variables CSS d'une source"""
        source = self.sources[source_name]
        return current_app.config[source.config_key]

    def update_variable(self, source_name: str, variable_name: str, new_value: str) -> None:
        """Met à jour une variable CSS pour une source spécifique"""
        source = self.sources[source_name]
        with current_app.app_context():
            # Mise à jour en base
            var = source.model.query.filter_by(variable=variable_name).first()
            if var:
                var.value = new_value
            else:
                # Création si n'existe pas
                var = source.model(variable=variable_name, value=new_value)
                db.session.add(var)
            
            db.session.commit()
            # Mise à jour dans app.config
            if source.config_key not in current_app.config:
                current_app.config[source.config_key] = {}
            current_app.config[source.config_key][variable_name] = new_value

    def reload_source(self, source_name: str) -> None:
        """Recharge toutes les variables d'une source spécifique"""
        self._load_css_variables_for_source(current_app, source_name)

    def reload_all(self) -> None:
        """Recharge toutes les variables de toutes les sources"""
        self._load_all_css_variables(current_app)