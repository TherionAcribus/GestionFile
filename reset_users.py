from app import app, db
from routes.admin_security import reset_admin_user


"""
Supprime tous les utilisateurs de la base de données. 
Au prochain démarrage de l'App, un utilisateur "admin" sera créé avec le mot de passe "admin".
"""

def main():
    with app.app_context():
        print("Suppression de tous les utilisateurs...")
        if reset_admin_user():
            print("Tous les utilisateurs ont été supprimés avec succès")
        else:
            print("Erreur lors de la suppression des utilisateurs")

if __name__ == '__main__':
    main()
