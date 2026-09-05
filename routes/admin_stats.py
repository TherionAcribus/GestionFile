import zlib
from datetime import datetime, timedelta

import pytz
from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import func, text

from models import Activity, AggregatedStats, Counter, Language, Patient, PatientHistory, db
from pagination import paginate_query, parse_page_params
from routes.admin_security import require_permission, require_permission_api
from stats_params import (
    CATEGORY_ACTIVITY,
    CATEGORY_COUNTER,
    CATEGORY_LANGUAGE,
    aggregated_category_type,
    compressed_filter_plan,
    compressed_skipped_warning,
    is_time_chart,
    mysql_weekdays,
    parse_chart_request,
    time_metric,
)

admin_stats_bp = Blueprint('admin_stats', __name__)

time_tz = pytz.timezone('Europe/Paris')

# Colonnes de tri autorisées (liste blanche) pour l'historique détaillé.
HISTORY_SORT_COLUMNS = {
    'call_number': PatientHistory.call_number,
    'timestamp': PatientHistory.timestamp,
    'status': PatientHistory.status,
    'day_of_week': PatientHistory.day_of_week,
}

# Dimension de regroupement -> (entité à joindre, colonne de clé étrangère).
# La colonne de libellé est toujours ``entity.name``.
CATEGORY_ENTITIES = {
    CATEGORY_LANGUAGE: (Language, 'language_id'),
    CATEGORY_ACTIVITY: (Activity, 'activity_id'),
    CATEGORY_COUNTER: (Counter, 'counter_id'),
}

# Métrique de durée -> (colonne de moyenne, colonne d'effectif) dans
# ``AggregatedStats``. Les colonnes d'effectif sont NULL pour les lignes
# agrégées avant leur introduction : ``count`` sert alors de repli.
AGGREGATED_TIME_COLUMNS = {
    'waiting': (AggregatedStats.avg_waiting_time, AggregatedStats.count_waiting_time),
    'counter': (AggregatedStats.avg_counter_time, AggregatedStats.count_counter_time),
    'total': (AggregatedStats.avg_total_time, AggregatedStats.count_total_time),
}


@admin_stats_bp.route('/admin/stats')
@require_permission('stats')
def admin_stats():
    counters = Counter.query.all()
    activities = Activity.query.all()
    languages = Language.query.all()
    today = datetime.now(time_tz).date()
    return render_template('admin/stats.html',
                            current_date=today,
                            counters=counters,
                            activities=activities,
                            languages=languages)


@admin_stats_bp.route('/admin/stats/history')
@require_permission('stats')
def admin_history():
    """Page de l'historique détaillé (table paginée des patients archivés)."""
    return render_template('admin/history.html')


@admin_stats_bp.route('/admin/stats/history/table')
@require_permission('stats')
def display_history_table():
    """Fragment HTMX : table paginée + triée + recherchable de PatientHistory.

    Les colonnes activité / comptoir / langue de PatientHistory sont des entiers
    (pas de relation ORM) : on les résout en noms via des dictionnaires id→nom
    construits en une requête chacun, plutôt que par jointure, pour garder la
    pagination simple et le comptage exact sur PatientHistory.
    """
    params = parse_page_params(
        request.values,
        allowed_sort=tuple(HISTORY_SORT_COLUMNS),
        default_sort='timestamp',
    )
    pager = paginate_query(
        PatientHistory.query,
        params,
        sort_columns=HISTORY_SORT_COLUMNS,
        search_columns=[
            PatientHistory.call_number,
            PatientHistory.status,
            PatientHistory.day_of_week,
        ],
    )

    activity_names = dict(db.session.query(Activity.id, Activity.name).all())
    counter_names = dict(db.session.query(Counter.id, Counter.name).all())
    language_names = dict(db.session.query(Language.id, Language.code).all())

    return render_template('admin/history_htmx_table.html',
                            rows=pager.items, pager=pager, params=params,
                            activity_names=activity_names,
                            counter_names=counter_names,
                            language_names=language_names)


@admin_stats_bp.route('/admin/stats/chart')
@require_permission_api('stats')
def get_chart_data():
    """Données du graphique, au format attendu par Chart.js.

    Répond toujours 200 : une entrée invalide devient ``{'error': ...}`` dans le
    corps, que le front affiche dans son encart dédié. htmx n'échange pas le
    fragment sur un statut 4xx (``responseHandling`` par défaut), et le message
    de validation — notamment le bornage de période — n'atteignait donc jamais
    l'utilisateur.
    """
    # Validation stricte + bornage de période (point 5.4) dans le cœur pur.
    req = parse_chart_request(request.args, now=datetime.now(time_tz))
    if not req.ok:
        return jsonify({'error': req.error})

    warning = None

    # 1. Données détaillées (Patient pour la journée en cours, PatientHistory
    #    pour l'historique).
    if req.is_history:
        detailed_data = fetch_detailed_data(PatientHistory, req)
    else:
        detailed_data = fetch_detailed_data(Patient, req, join_models=True)

    # 2. Données compressées (AggregatedStats) : uniquement sur l'historique, et
    #    uniquement si les filtres actifs sont représentables au niveau
    #    d'agrégation (une seule dimension par ligne).
    compressed_data = []
    if req.is_history:
        category_ids, unsupported = compressed_filter_plan(req)
        if unsupported:
            warning = compressed_skipped_warning(unsupported)
        else:
            compressed_data = fetch_compressed_data(req, category_ids)

    # 3. Fusion (moyenne pondérée pour les durées, somme pour les comptages).
    merged_data = merge_datasets(detailed_data, compressed_data, req.is_time)

    # 4. Mise en forme Chart.js.
    response_data = format_chart_data(merged_data, req.chart_type, req.chart_style,
                                      req.start_date, req.end_date, req.time_granularity)
    if warning:
        response_data['warning'] = warning

    return jsonify(response_data)


def fetch_detailed_data(model, req, join_models=False):
    """Agrège les lignes détaillées de ``Patient`` ou ``PatientHistory``.

    ``join_models`` : ``Patient`` porte de vraies clés étrangères, SQLAlchemy
    déduit donc la condition de jointure ; ``PatientHistory`` stocke des entiers
    nus et exige une condition explicite.
    """
    query = db.session.query(model).filter(model.timestamp.between(req.start_date, req.end_date))
    query = apply_filters(query, model, req)

    entities = []
    groups = []

    # Axe temporel (graphique en courbe uniquement).
    if req.chart_style == 'line':
        entities.append(get_date_func(model.timestamp, req.time_granularity).label('date'))
        groups.append(text('date'))

    # Dimension de regroupement.
    entity = None
    if req.category is not None:
        entity, fk_name = CATEGORY_ENTITIES[req.category]
        entities.append(entity.name.label('category'))
        groups.append(entity.name)

    # Métrique.
    metric = time_metric(req.chart_type)
    if metric:
        query = filter_complete_timestamps(query, model, metric)
        entities.append(func.avg(get_time_column(model, metric)).label('value'))
        # ``count`` sert de poids à la fusion détaillé/compressé : il doit
        # compter les lignes réellement moyennées, donc après filtrage des
        # timestamps manquants.
        entities.append(func.count(model.id).label('count'))
    else:
        entities.append(func.count(model.id).label('value'))
        entities.append(func.count(model.id).label('count'))

    query = query.with_entities(*entities)

    if entity is not None:
        if join_models:
            query = query.join(entity)
        else:
            query = query.join(entity, getattr(model, fk_name) == entity.id)

    if groups:
        query = query.group_by(*groups)

    return query.all()


def fetch_compressed_data(req, category_ids=None):
    """Agrège les lignes pré-calculées de ``AggregatedStats``.

    ``category_ids`` restreint ``category_id`` quand le filtre actif porte sur
    la dimension même du graphique (cf. ``compressed_filter_plan``). Les filtres
    portant sur une autre dimension ne sont pas représentables ici : l'appelant
    écarte alors les agrégats au lieu de les additionner sans filtre.
    """
    category_type = aggregated_category_type(req.chart_type)
    if category_type is None:
        return []

    query = db.session.query(AggregatedStats).filter(
        AggregatedStats.date.between(req.start_date.date(), req.end_date.date()),
        AggregatedStats.category_type == category_type,
    )
    if category_ids:
        query = query.filter(AggregatedStats.category_id.in_(category_ids))
    if req.day_of_week:
        query = query.filter(
            func.dayofweek(AggregatedStats.date).in_(mysql_weekdays(req.day_of_week))
        )

    entities = []
    groups = []

    if req.chart_style == 'line':
        entities.append(get_date_func(AggregatedStats.date, req.time_granularity).label('date'))
        groups.append(text('date'))

    if req.category is not None:
        entity, _fk_name = CATEGORY_ENTITIES[req.category]
        query = query.join(entity, AggregatedStats.category_id == entity.id)
        entities.append(entity.name.label('category'))
        groups.append(entity.name)

    metric = time_metric(req.chart_type)
    if metric:
        avg_col, count_col = AGGREGATED_TIME_COLUMNS[metric]
        # L'effectif de la métrique (patients dont les deux timestamps sont
        # renseignés) est le seul poids correct ; ``count`` (tous les patients du
        # jour) ne sert que de repli pour les lignes agrégées avant son
        # introduction.
        weight = func.coalesce(count_col, AggregatedStats.count)
        entities.append(
            (func.sum(avg_col * weight) / func.nullif(func.sum(weight), 0)).label('value')
        )
        entities.append(func.sum(weight).label('count'))
    else:
        entities.append(func.sum(AggregatedStats.count).label('value'))
        entities.append(func.sum(AggregatedStats.count).label('count'))

    query = query.with_entities(*entities)
    if groups:
        query = query.group_by(*groups)

    return query.all()


def merge_datasets(detailed, compressed, is_time):
    """Fusionne les lignes détaillées et compressées d'une même période.

    Les durées se recombinent en moyenne pondérée par l'effectif, les comptages
    par simple somme.
    """
    data_map = {}

    all_rows = list(detailed) + list(compressed)

    for row in all_rows:
        date = getattr(row, 'date', 'Total')
        category = getattr(row, 'category', 'Total')
        val = float(row.value) if row.value else 0
        cnt = int(row.count) if row.count else 0

        key = (date, category)
        if key not in data_map:
            data_map[key] = {'weighted_sum': 0, 'total_count': 0}

        if is_time:
            # val est une moyenne : on repasse en somme pondérée.
            data_map[key]['weighted_sum'] += val * cnt
            data_map[key]['total_count'] += cnt
        else:
            # val est déjà un comptage : il s'additionne tel quel.
            data_map[key]['weighted_sum'] += val

    result = []
    for (date, category), v in data_map.items():
        if is_time:
            final_val = v['weighted_sum'] / v['total_count'] if v['total_count'] > 0 else 0
        else:
            final_val = v['weighted_sum']

        obj = type('obj', (object,), {'date': date, 'category': category,
                                      'value': final_val, 'count': v['total_count']})
        result.append(obj)

    return result


def format_chart_data(data, chart_type, chart_style, start_date, end_date, time_granularity):
    is_time = is_time_chart(chart_type)

    if chart_style == 'line':
        # Une série par catégorie, dans un ordre stable (les couleurs et la
        # légende ne doivent pas se réorganiser d'un rafraîchissement à l'autre).
        categories = sorted({d.category for d in data}, key=str)
        datasets = []

        # Index (date, catégorie) -> valeur, construit en une passe. Remplace la
        # recherche linéaire ``next(...)`` refaite pour chaque case du produit
        # cartésien dates × catégories (point 5.4) : on passe d'un coût
        # O(dates × catégories × lignes) à un accès dictionnaire O(1).
        value_by_key = {(str(d.date), d.category): d.value for d in data}

        # Toutes les dates de la plage, y compris celles sans donnée (y=0).
        all_dates = []
        current = start_date
        fmt = '%Y-%m-%d %H:00:00' if time_granularity == 'hour' else '%Y-%m-%d'
        increment = timedelta(hours=1) if time_granularity == 'hour' else timedelta(days=1)

        while current <= end_date:
            all_dates.append(current.strftime(fmt))
            current += increment

        for cat in categories:
            cat_data = []
            for date in all_dates:
                val = value_by_key.get((date, cat), 0)
                if is_time:
                    val = val / 60  # Minutes
                cat_data.append({'x': date, 'y': val})

            color = color_for_label(cat)
            datasets.append({
                'label': cat,
                'data': cat_data,
                'fill': False,
                'borderColor': color,
                'backgroundColor': color,
                'tension': 0.1
            })

        return {
            'datasets': datasets,
            'title': get_chart_title(chart_type),
            'isTime': is_time
        }
    else:
        # Camembert / histogramme.
        labels = [d.category for d in data]
        values = [d.value for d in data]
        if is_time:
            values = [v / 60 for v in values]

        return {
            'labels': labels,
            'datasets': [{
                'data': values,
                'backgroundColor': generate_colors(labels)
            }],
            'title': get_chart_title(chart_type),
            'isTime': is_time
        }


def apply_filters(query, model, req):
    """Applique les filtres numériques déjà validés (point 5.4).

    Les identifiants proviennent de ``parse_chart_request`` : ce sont des
    entiers, dédoublonnés, avec les jours de semaine bornés à 1..7. Plus aucune
    conversion ``int(...)`` non gardée ici (elle levait auparavant une 500 sur
    une saisie forgée).
    """
    if req.counter_ids:
        query = query.filter(model.counter_id.in_(req.counter_ids))
    if req.activity_ids:
        query = query.filter(model.activity_id.in_(req.activity_ids))
    if req.language_ids:
        query = query.filter(model.language_id.in_(req.language_ids))

    # Le jour de la semaine n'a de sens que sur l'historique (colonne dérivée
    # d'un balayage temporel long) ; on le réserve au modèle PatientHistory.
    # ``dayofweek`` n'est pas indexable, mais il ne s'applique ici qu'aux lignes
    # déjà restreintes par l'index (timestamp) de la plage demandée.
    if req.is_history and req.day_of_week:
        query = query.filter(func.dayofweek(model.timestamp).in_(mysql_weekdays(req.day_of_week)))

    return query


def filter_complete_timestamps(query, model, metric):
    """Écarte les lignes dont un timestamp de la durée mesurée manque.

    ``AVG`` ignore déjà les NULL, mais pas ``COUNT(id)`` : sans ce filtre le
    poids de la moyenne pondérée compterait des patients qui ne participent pas
    à la moyenne.
    """
    if metric == 'waiting':
        return query.filter(model.timestamp_counter.isnot(None))
    if metric == 'counter':
        return query.filter(model.timestamp_counter.isnot(None),
                            model.timestamp_end.isnot(None))
    return query.filter(model.timestamp_end.isnot(None))


def get_date_func(col, granularity):
    """Expression de troncature de date, pour l'axe des graphiques en courbe."""
    fmt = '%Y-%m-%d %H:00:00' if granularity == 'hour' else '%Y-%m-%d'
    return func.date_format(col, fmt)


def get_time_column(model, metric):
    """Durée mesurée, en secondes, pour la métrique demandée."""
    if metric == 'waiting':
        return func.timestampdiff(text('SECOND'), model.timestamp, model.timestamp_counter)
    if metric == 'counter':
        return func.timestampdiff(text('SECOND'), model.timestamp_counter, model.timestamp_end)
    return func.timestampdiff(text('SECOND'), model.timestamp, model.timestamp_end)


def color_for_label(label):
    """Couleur stable, dérivée du libellé de la série.

    Le tirage aléatoire précédent changeait de couleur à chaque rafraîchissement
    et pouvait produire des teintes illisibles (quasi-blanc). ``crc32`` est
    utilisé plutôt que ``hash()`` : ce dernier est randomisé par processus, donc
    deux workers auraient rendu des couleurs différentes pour une même série.
    Saturation et luminosité sont fixées pour garantir la lisibilité.
    """
    hue = zlib.crc32(str(label).encode('utf-8')) % 360
    return f'hsl({hue}, 65%, 45%)'


def generate_colors(labels):
    """Palette d'un graphique en secteurs / barres : une couleur par libellé."""
    return [color_for_label(label) for label in labels]


def get_chart_title(chart_type):
    titles = {
        'languages': 'Distribution des langues',
        'activities': 'Distribution des activités',
        'counters': 'Distribution des comptoirs',
        'waiting_times': "Évolution des temps d'attente",
        'counter_times': 'Évolution des temps au comptoir',
        'total_times': 'Évolution des temps totaux',
        'waiting_times_by_activity': "Temps d'attente moyen par activité",
        'counter_times_by_activity': 'Temps au comptoir moyen par activité',
        'total_times_by_activity': 'Temps total moyen par activité'
    }
    return titles.get(chart_type, 'Statistiques')
