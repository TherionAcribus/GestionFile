#!/bin/sh
# Synchronisation des médias par défaut vers les volumes persistants.
#
# Problème : les dossiers static/images/buttons, static/images/flags,
# static/images/annonces et static/audio/signals contiennent des fichiers
# par défaut livrés avec l'image Docker (drapeaux, sons, images de boutons).
# Monter un volume nommé dessus les vide au premier démarrage : l'app perd
# tous ses médias par défaut.
#
# Solution : le Dockerfile copie ces dossiers vers /app/default_media/ (hors
# volume). Cet entrypoint, exécuté avant l'app, restaure les fichiers manquants
# dans chaque volume à partir de cette sauvegarde. Les fichiers déjà présents
# (uploadés par l'utilisateur) ne sont JAMAIS écrasés.
#
# La synchro est idempotente et rapide : rsync -u --ignore-existing ne copie
# que les fichiers absents. Sans rsync (image minimale), on retombe sur cp -n.

set -e

DEFAULT_MEDIA="/app/default_media"
STATIC_DIR="/app/static"

# Dossiers médias persistants (relatifs à static/) qui contiennent des
# fichiers par défaut livrés avec l'image.
MEDIA_DIRS="images/buttons images/flags images/annonces audio/signals"

for rel_dir in $MEDIA_DIRS; do
    src="$DEFAULT_MEDIA/$rel_dir"
    dst="$STATIC_DIR/$rel_dir"

    # Créer le dossier cible (le volume peut être vide mais existe).
    mkdir -p "$dst"

    # Pas de source de référence -> rien à synchroniser.
    if [ ! -d "$src" ]; then
        continue
    fi

    # rsync est préférable (efficace, idempotent) ; cp -n comme repli.
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --ignore-existing "$src/" "$dst/"
    else
        cp -rn "$src/." "$dst/" 2>/dev/null || true
    fi
done

# Lancer la commande originale (passée via exec).
exec "$@"
