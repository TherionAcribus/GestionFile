"""
Script de migration pour mettre à jour les tailles des cartes du dashboard.
Convertit les anciennes valeurs Bootstrap (col-md-6, etc.) vers le nouveau système numérique (18, 24, 36, 48).
"""

from app import app, db
from models import DashboardCard

# Mapping des anciennes valeurs vers les nouvelles
SIZE_MAPPING = {
    'col-md-3': '18',   # 25%
    'col-md-4': '24',   # 33%
    'col-md-6': '36',   # 50%
    'col-md-8': '48',   # 66%
    'col-md-12': '48',  # 100% -> on mappe vers 66% (le plus grand disponible)
}

def migrate_card_sizes():
    """Migre les tailles des cartes vers le nouveau format."""
    with app.app_context():
        cards = DashboardCard.query.all()
        
        print(f"Migration de {len(cards)} cartes...")
        updated_count = 0
        
        for card in cards:
            old_size = card.size
            
            # Si la taille est déjà au nouveau format (numérique), on ne fait rien
            if old_size in ['18', '24', '36', '48']:
                print(f"  ✓ {card.name}: déjà au bon format ({old_size})")
                continue
            
            # Sinon, on convertit
            if old_size in SIZE_MAPPING:
                new_size = SIZE_MAPPING[old_size]
                card.size = new_size
                updated_count += 1
                print(f"  → {card.name}: {old_size} → {new_size}")
            else:
                # Valeur par défaut si format inconnu
                card.size = '36'
                updated_count += 1
                print(f"  ⚠ {card.name}: {old_size} (inconnu) → 36 (par défaut)")
        
        if updated_count > 0:
            db.session.commit()
            print(f"\n✅ Migration terminée : {updated_count} carte(s) mise(s) à jour")
        else:
            print("\n✅ Aucune mise à jour nécessaire")

if __name__ == '__main__':
    migrate_card_sizes()
