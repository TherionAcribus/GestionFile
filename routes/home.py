from flask import Blueprint, render_template

home_bp = Blueprint('home', __name__)

@home_bp.route('/')
def home():
    """Page d'accueil avec navigation vers les différentes sections"""
    return render_template('home.html')
