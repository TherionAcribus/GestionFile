from playwright.sync_api import sync_playwright

def test_vaccination_button():
    with sync_playwright() as p:
        # Lance le navigateur Chromium
        browser = p.chromium.launch(headless=False)  # Mettre headless=True pour le mode sans interface
        page = browser.new_page()

        # Ouvre la page http://127.0.0.1:5000/patient
        page.goto("http://127.0.0.1:5000/patient")

        page.fill('input[name="username"]', 'admin')  # Remplace 'test_user' par un nom d'utilisateur valide
        # Saisir le mot de passe
        page.fill('input[name="password"]', 'admin')  # Remplace 'password123' par le mot de passe valide

        # Soumettre le formulaire en cliquant sur le bouton "Login"
        page.click('input[type="submit"][value="Login"]')

        # Attendre la redirection vers la page /patient après la connexion
        page.wait_for_url("http://127.0.0.1:5000/patient")

        # Cliquer sur le bouton "Vaccination" (ciblage basé sur l'attribut hx-post)
        page.click('div[hx-post="/patients_submit"]')

        # Attendre la redirection ou le chargement de la nouvelle page après le clic sur Vaccination
        page.wait_for_selector('div[hx-post="patient/scan_and_validate"]')

        # Cliquer sur le bouton "Scanner puis valider" (ciblage basé sur l'attribut hx-post)
        page.click('div[hx-post="patient/scan_and_validate"]')

        # Attendre que le texte dans la balise <p class="text_summary"> apparaisse
        page.wait_for_selector('p.text_summary')

        # Récupérer le contenu de la balise <p class="text_summary">
        text_summary = page.inner_text('p.text_summary')
        print('TS', text_summary)

        # Vérifier que le texte contient la partie fixe "Votre demande est bien prise en charge"
        assert "Votre demande est bien prise en charge" in text_summary

        browser.close()

# Lancer le test avec pytest
if __name__ == "__main__":
    test_vaccination_button()