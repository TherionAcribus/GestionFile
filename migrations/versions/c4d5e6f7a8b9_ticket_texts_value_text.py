"""Textes de ticket : value_str (bornee a 200) -> value_text

Les zones de saisie des textes de ticket (administration) n'ont aucune limite
alors que ``value_str`` est une String(200) : au-dela, erreur ou troncature
MySQL. Pire a la restauration : la sauvegarde rangeait les chaines >= 200
caracteres dans ``value_text`` alors que le registre relisait ``value_str`` —
le texte disparaissait apres une restauration.

Les trois cles ``ticket_header`` / ``ticket_message`` / ``ticket_footer`` sont
desormais declarees ``value_text`` dans params_registry. Cette migration
deplace les donnees existantes et realigne les traductions collectees
(``translation.column_name`` 'value_str' -> 'value_text'). Elle purge aussi
les cles derivees ``ticket_*_printer`` (ESC/POS preformate a 42, rendu
obsolet par la conversion a l'impression) pour qu'elles ne subsistent pas
en base ni dans les sauvegardes.

Revision ID: c4d5e6f7a8b9
Revises: a1b2c3d4e5f7
Create Date: 2026-09-23 00:00:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c4d5e6f7a8b9'
down_revision = 'a1b2c3d4e5f7'
branch_labels = None
depends_on = None

_TICKET_KEYS = "('ticket_header', 'ticket_message', 'ticket_footer')"


def upgrade():
    # Deplacer le texte existant vers value_text (seule colonne lue par le
    # chargeur pour ces cles desormais) et vider l'ancienne colonne.
    op.execute(
        "UPDATE config_option SET value_text = value_str, value_str = NULL "
        "WHERE config_key IN " + _TICKET_KEYS + " AND value_str IS NOT NULL"
    )
    # Purger les cles derivees ticket_*_printer : versions ESC/POS preformatees
    # a 42 caracteres, obsoletes depuis le rendu a la demande — et exportables
    # dans les sauvegardes si elles restaient en base.
    op.execute(
        "DELETE FROM config_option WHERE config_key IN "
        "('ticket_header_printer', 'ticket_message_printer', 'ticket_footer_printer')"
    )
    # Traductions collectees sur l'ancienne colonne : supprimer d'abord les
    # lignes qui entreraient en conflit avec un doublon 'value_text' existant
    # (unicite table_name/column_name/row_id/language_code), puis rebasculer.
    # La sous-requete derivee est requise par MySQL (on ne peut pas lire la
    # table en cours de DELETE) et reste valide sous SQLite.
    op.execute(
        "DELETE FROM translation WHERE table_name = 'ConfigOption' "
        "AND column_name = 'value_str' AND key_name IN " + _TICKET_KEYS + " "
        "AND EXISTS (SELECT 1 FROM (SELECT 1 FROM translation t2 "
        "WHERE t2.table_name = 'ConfigOption' AND t2.column_name = 'value_text' "
        "AND t2.key_name = translation.key_name "
        "AND t2.row_id = translation.row_id "
        "AND t2.language_code = translation.language_code) AS dup)"
    )
    op.execute(
        "UPDATE translation SET column_name = 'value_text' "
        "WHERE table_name = 'ConfigOption' AND column_name = 'value_str' "
        "AND key_name IN " + _TICKET_KEYS
    )


def downgrade():
    # Retour vers value_str, bornee a 200 caracteres : on ne ramene que ce qui
    # tient (un downgrade sans perte est impossible pour les textes longs).
    op.execute(
        "UPDATE config_option SET value_str = value_text, value_text = NULL "
        "WHERE config_key IN " + _TICKET_KEYS + " AND value_text IS NOT NULL "
        "AND CHAR_LENGTH(value_text) <= 200"
    )
    op.execute(
        "DELETE FROM translation WHERE table_name = 'ConfigOption' "
        "AND column_name = 'value_text' AND key_name IN " + _TICKET_KEYS + " "
        "AND EXISTS (SELECT 1 FROM (SELECT 1 FROM translation t2 "
        "WHERE t2.table_name = 'ConfigOption' AND t2.column_name = 'value_str' "
        "AND t2.key_name = translation.key_name "
        "AND t2.row_id = translation.row_id "
        "AND t2.language_code = translation.language_code) AS dup)"
    )
    op.execute(
        "UPDATE translation SET column_name = 'value_str' "
        "WHERE table_name = 'ConfigOption' AND column_name = 'value_text' "
        "AND key_name IN " + _TICKET_KEYS
    )
