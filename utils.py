import re

def validate_and_transform_text(user_input):
    """ Verif et conversion des entrées pour les annonces"""
    # Convertir les minuscules {p}, {c}, {m} en majuscules {P}, {C}, {M}
    corrected_input = re.sub(r"\{(p|c|m)\}", lambda m: "{" + m.group(1).upper() + "}", user_input)

    # Vérifier si tous les placeholders sont {P}, {C}, {M} après correction
    # Cette regex recherche toute chaîne qui ne suit pas exactement le modèle autorisé
    if re.search(r"\{[^PCM]?\}", corrected_input) or re.search(r"\{[PCM][^}]", corrected_input):
        return {"success": False, "value": "Certaines balises sont incorrectes. Vous ne pouvez utiliser que {P}, {C} ou {M}."}

    
    print("corrected_input", corrected_input)
    return {"success": True, "value": corrected_input}
