import os
from flask import render_template, current_app as app

def admin_admin():
    themes_path = os.path.join(app.static_folder, 'css/themes')
    
    # Récupérer la liste des fichiers CSS
    theme_files = [f for f in os.listdir(themes_path) if f.endswith('.css')]
    
    # Extraire le nom du thème sans l'extension .css
    themes = [os.path.splitext(f)[0] for f in theme_files]

    return render_template('/admin/admin_options.html',
                            admin_colors = app.config['ADMIN_COLORS'],
                            themes=themes)

