import pytest
from playwright.sync_api import Page
import mysql.connector
from dotenv import load_dotenv
import os
import re

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

@pytest.fixture(scope="session")
def browser(playwright):
    """Lancer un navigateur une seule fois pour tous les tests."""
    browser = playwright.chromium.launch(headless=False)
    yield browser
    browser.close()

@pytest.fixture(scope="session")
def page(browser):
    """Ouvre une nouvelle page et se connecte une seule fois pour tous les tests."""
    page = browser.new_page()

    # Se connecter une seule fois
    page.goto("http://127.0.0.1:5000/patient")

    # Attendre que le formulaire de login soit présent
    page.wait_for_selector('input[name="username"]')

    # Remplir le formulaire de login
    page.fill('input[name="username"]', 'admin')
    page.fill('input[name="password"]', 'admin')

    # Soumettre le formulaire en cliquant sur le bouton "Login"
    page.click('input[type="submit"][value="Login"]')

    # Vérifier si la redirection vers /patient a eu lieu
    page.wait_for_url("http://127.0.0.1:5000/patient")

    yield page  # La page est maintenant prête à être utilisée pour tous les tests

@pytest.fixture
def db_connection():
    """Connexion à la base de données MySQL en utilisant les informations du fichier .env."""
    conn = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST"),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE")
    )
    yield conn
    conn.close()

def click_button(page: Page, button_id: str):
    """Fonction pour cliquer sur un bouton en utilisant son ID."""
    page.wait_for_selector(f'#{button_id}')  # Attendre que le bouton soit visible
    page.click(f'#{button_id}')

    # Attendre le chargement de la nouvelle page après le clic
    page.wait_for_selector('div[hx-post="patient/scan_and_validate"]')

    # Cliquer sur le bouton "Scanner puis valider"
    page.click('div[hx-post="patient/scan_and_validate"]')

    # Attendre que le texte de confirmation apparaisse
    page.wait_for_selector("#conclusion_text")

    # Récupérer le texte de confirmation
    text_summary = page.inner_text("#conclusion_text")
    print(f'Texte de confirmation : {text_summary}')

    # Vérifier que le texte de confirmation contient "Votre demande est bien prise en charge"
    assert "Votre demande est bien prise en charge" in text_summary

    # Extraire le code d'appel avec une expression régulière (ex : O-22, T-1, B-34)
    match = re.search(r'[A-Z]-\d+', text_summary)
    if match:
        call_number = match.group(0)
        print(f"Numéro d'appel trouvé : {call_number}")
        return call_number
    else:
        raise ValueError("Numéro d'appel non trouvé dans le texte de confirmation.")

def check_patient_in_db(conn, call_number: str):
    """Vérifie si un patient est enregistré dans la base de données avec le call_number."""
    cursor = conn.cursor()
    query = "SELECT * FROM patient WHERE call_number = %s"
    cursor.execute(query, (call_number,))
    result = cursor.fetchone()
    cursor.close()

    # Si le résultat n'est pas None, cela signifie que le patient est bien enregistré
    assert result is not None, f"Le patient avec le call_number {call_number} n'a pas été trouvé dans la base de données."

@pytest.mark.parametrize("button_id", ["ordonnances", "retrait_de_commande"])
def test_button_click(page: Page, button_id: str):
    """Test pour cliquer sur différents boutons en utilisant leur ID."""
    click_button(page, button_id)

@pytest.mark.parametrize("button_id", ["ordonnances", "retrait_de_commande"])
def test_patient_in_db(page: Page, db_connection, button_id: str):
    """Test pour vérifier que le patient est bien enregistré après l'interaction avec le bouton."""
    # Récupérer le code d'appel après avoir cliqué sur le bouton
    call_number = click_button(page, button_id)

    # Vérifier que le patient est bien enregistré dans la base de données avec le code d'appel extrait
    check_patient_in_db(db_connection, call_number)
