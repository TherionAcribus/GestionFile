import csv
import io
import zlib
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytz
from flask import Blueprint, Response, current_app, jsonify, render_template, request
from sqlalchemy import func, text

from models import Activity, AggregatedStats, Counter, Language, Patient, PatientHistory, db
from pagination import paginate_query, parse_page_params
from history_explain import (
    PRESETS as HISTORY_PRESETS,
    day_bounds as history_day_bounds,
    format_minutes,
    minutes as history_minutes,
    parse_filters as parse_history_filters,
    summarize as summarize_history,
)
from queue_explain import STATS_EXCLUDED_STATUSES, STATUS_BADGES, STATUS_LABELS
import stats_insights as insights
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
    parse_int_list,
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
    counters = Counter.query.order_by(Counter.sort_order).all()
    activities = Activity.query.order_by(Activity.letter, Activity.name).all()
    languages = Language.query.order_by(Language.sort_order).all()
    today = datetime.now(time_tz).date()
    return render_template('admin/stats.html',
                            current_date=today,
                            counters=counters,
                            activities=activities,
                            languages=languages,
                            periods=insights.PERIODS,
                            default_period=insights.DEFAULT_PERIOD,
                            weekdays=list(enumerate(insights.WEEKDAYS_FR, start=1)))


# Garde-fou : au-delà, les indicateurs portent sur un échantillon (les plus
# récents) et la page le signale.
INSIGHTS_MAX_ROWS = 300_000

_INSIGHT_COLUMNS = ('timestamp', 'timestamp_counter', 'timestamp_end', 'status',
                    'activity_id', 'counter_id', 'language_id', 'overtaken')


def _insight_rows(period, counter_ids, activity_ids, language_ids, weekdays):
    """Lignes détaillées de la période : patients du jour (Patient) et
    journées archivées (PatientHistory), statuts hors file exclus."""
    start, end = period.bounds()
    rows, truncated = [], False
    for model in (Patient, PatientHistory):
        query = (db.session.query(*[getattr(model, c) for c in _INSIGHT_COLUMNS])
                 .filter(model.timestamp >= start, model.timestamp < end,
                         model.status.notin_(STATS_EXCLUDED_STATUSES)))
        if counter_ids:
            query = query.filter(model.counter_id.in_(counter_ids))
        if activity_ids:
            query = query.filter(model.activity_id.in_(activity_ids))
        if language_ids:
            query = query.filter(model.language_id.in_(language_ids))
        found = query.order_by(model.timestamp.desc()).limit(INSIGHTS_MAX_ROWS + 1).all()
        truncated = truncated or len(found) > INSIGHTS_MAX_ROWS
        rows.extend(found[:INSIGHTS_MAX_ROWS])
    if weekdays:
        # Jour de semaine filtré en Python (1 = lundi) : portable, sans
        # fonction SQL propre à MySQL.
        rows = [r for r in rows if r[0] is not None and r[0].isoweekday() in weekdays]
    return rows, truncated


@admin_stats_bp.route('/admin/stats/insights')
@require_permission('stats')
def stats_insights():
    """Fragment « tableau de bord » : indicateurs clés comparés à la période
    précédente, affluence par heure / jour, attentes, détail par dimension."""
    today = datetime.now(time_tz).date()
    period = insights.parse_period(request.values.get, today)
    counter_ids = parse_int_list(request.values.getlist('counter_filter'))
    activity_ids = parse_int_list(request.values.getlist('activity_filter'))
    language_ids = parse_int_list(request.values.getlist('language_filter'))
    weekdays = parse_int_list(request.values.getlist('day_of_week_filter'), valid=set(range(1, 8)))

    rows, truncated = _insight_rows(period, counter_ids, activity_ids, language_ids, weekdays)
    previous_rows, _ = _insight_rows(period.previous(), counter_ids, activity_ids,
                                     language_ids, weekdays)
    current = insights.kpis(rows)
    previous = insights.kpis(previous_rows)
    hours = insights.by_hour(rows)

    # Jours antérieurs déjà compressés en moyennes journalières : absents du
    # détail, donc de ce tableau de bord (le graphique personnalisé les inclut).
    compressed_days = (db.session.query(func.count(func.distinct(AggregatedStats.date)))
                       .filter(AggregatedStats.date >= period.date_from,
                               AggregatedStats.date <= period.date_to).scalar() or 0)

    names = {
        'activity': dict(db.session.query(Activity.id, Activity.name).all()),
        'counter': dict(db.session.query(Counter.id, Counter.name).all()),
        'language': dict(db.session.query(Language.id, Language.name).all()),
    }
    return render_template(
        'admin/stats_insights.html',
        period=period,
        kpi=current,
        previous=previous,
        deltas={
            'count': insights.delta(current['per_day'], previous['per_day']),
            'avg_wait': insights.delta(current['avg_wait'], previous['avg_wait']),
            'long_wait_pct': insights.delta(current['long_wait_pct'], previous['long_wait_pct']),
            'avg_counter': insights.delta(current['avg_counter'], previous['avg_counter']),
        },
        hours=hours,
        peak=insights.peak(hours),
        weekdays=insights.by_weekday(rows) if period.days >= 7 else [],
        waits=insights.wait_distribution(rows),
        by_activity=insights.breakdown(rows, 'activity', names['activity']),
        by_counter=insights.breakdown(rows, 'counter', names['counter']),
        by_language=insights.breakdown(rows, 'language', names['language']),
        truncated=truncated,
        compressed_days=compressed_days,
        long_wait=insights.LONG_WAIT_MINUTES,
        fmt=insights.format_minutes,
    )


@admin_stats_bp.route('/admin/stats/history')
@require_permission('stats')
def admin_history():
    """Page de l'historique détaillé (filtres + résumé + table paginée)."""
    present = [s for (s,) in db.session.query(PatientHistory.status).distinct().all() if s]
    return render_template('admin/history.html',
                           activities=Activity.query.order_by(Activity.letter, Activity.name).all(),
                           counters=Counter.query.order_by(Counter.sort_order).all(),
                           statuses=[(s, STATUS_LABELS.get(s, s)) for s in sorted(
                               set(present), key=lambda s: list(STATUS_LABELS).index(s)
                               if s in STATUS_LABELS else 99)],
                           presets=HISTORY_PRESETS,
                           archive_enabled=current_app.config.get("CRON_TRANSFER_PATIENT_TO_HISTORY", False),
                           # Filtres pré-remplis (préréglages = liens ?preset=…).
                           filters=_history_filters())


def _history_query(filters):
    """Requête PatientHistory restreinte aux filtres (période, motif, comptoir, statuts)."""
    start, end = history_day_bounds(filters)
    query = PatientHistory.query.filter(PatientHistory.timestamp >= start,
                                        PatientHistory.timestamp < end)
    if filters.activity_id:
        query = query.filter(PatientHistory.activity_id == filters.activity_id)
    if filters.counter_id:
        query = query.filter(PatientHistory.counter_id == filters.counter_id)
    if filters.statuses:
        query = query.filter(PatientHistory.status.in_(filters.statuses))
    return query


def _history_filters():
    return parse_history_filters(request.values.get, datetime.now(time_tz).date(),
                                 getlist=request.values.getlist,
                                 known_statuses=tuple(STATUS_LABELS) + tuple(
                                     s for (s,) in db.session.query(PatientHistory.status).distinct().all()))


# Au-delà, le résumé est calculé sur un échantillon (les plus récents) :
# garde-fou pour une période très longue.
HISTORY_SUMMARY_MAX_ROWS = 100_000


@admin_stats_bp.route('/admin/stats/history/table')
@require_permission('stats')
def display_history_table():
    """Fragment HTMX : résumé + table paginée/triée/recherchable, filtrée.

    Les colonnes activité / comptoir / langue de PatientHistory sont des entiers
    (pas de relation ORM) : on les résout en noms via des dictionnaires id→nom
    construits en une requête chacun, plutôt que par jointure, pour garder la
    pagination simple et le comptage exact sur PatientHistory.
    """
    filters = _history_filters()
    params = parse_page_params(
        request.values,
        allowed_sort=tuple(HISTORY_SORT_COLUMNS),
        default_sort='timestamp',
    )
    query = _history_query(filters)
    pager = paginate_query(
        query,
        params,
        sort_columns=HISTORY_SORT_COLUMNS,
        search_columns=[PatientHistory.call_number],
    )

    summary_rows = (query.with_entities(PatientHistory.timestamp, PatientHistory.timestamp_counter,
                                        PatientHistory.timestamp_end, PatientHistory.status,
                                        PatientHistory.overtaken)
                    .order_by(PatientHistory.timestamp.desc())
                    .limit(HISTORY_SUMMARY_MAX_ROWS + 1).all())
    truncated = len(summary_rows) > HISTORY_SUMMARY_MAX_ROWS
    summary = summarize_history(summary_rows[:HISTORY_SUMMARY_MAX_ROWS])

    activity_names = dict(db.session.query(Activity.id, Activity.name).all())
    counter_names = dict(db.session.query(Counter.id, Counter.name).all())
    language_names = dict(db.session.query(Language.id, Language.name).all())

    return render_template('admin/history_htmx_table.html',
                            rows=pager.items, pager=pager, params=params,
                            activity_names=activity_names,
                            counter_names=counter_names,
                            language_names=language_names,
                            filters=filters,
                            summary=summary,
                            summary_truncated=truncated,
                            export_query=urlencode(filters.as_query_args()),
                            status_labels=STATUS_LABELS,
                            status_badges=STATUS_BADGES,
                            minutes=history_minutes,
                            format_minutes=format_minutes)


# Borne de l'export CSV (une ligne par patient).
HISTORY_EXPORT_MAX_ROWS = 200_000


@admin_stats_bp.route('/admin/stats/history/export.csv')
@require_permission('stats')
def export_history_csv():
    """Export CSV (séparateur « ; », tableur français) des lignes filtrées."""
    filters = _history_filters()
    rows = (_history_query(filters)
            .order_by(PatientHistory.timestamp)
            .limit(HISTORY_EXPORT_MAX_ROWS).all())
    activity_names = dict(db.session.query(Activity.id, Activity.name).all())
    counter_names = dict(db.session.query(Counter.id, Counter.name).all())
    language_names = dict(db.session.query(Language.id, Language.name).all())

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';')
    writer.writerow(["Date", "Arrivée", "Au comptoir", "Fin", "N° d'appel", "Motif",
                     "Comptoir", "Langue", "Statut", "Attente (min)",
                     "Au comptoir (min)", "Dépassé (fois)"])

    def hhmm(value):
        return value.strftime('%H:%M:%S') if value else ''

    def num(value):
        return f"{value:.1f}".replace('.', ',') if value is not None else ''

    for row in rows:
        writer.writerow([
            row.timestamp.strftime('%d/%m/%Y') if row.timestamp else '',
            hhmm(row.timestamp), hhmm(row.timestamp_counter), hhmm(row.timestamp_end),
            row.call_number,
            activity_names.get(row.activity_id, row.activity_id),
            counter_names.get(row.counter_id, '') if row.counter_id else '',
            language_names.get(row.language_id, '') if row.language_id else '',
            STATUS_LABELS.get(row.status, row.status),
            num(history_minutes(row.timestamp, row.timestamp_counter)),
            num(history_minutes(row.timestamp_counter, row.timestamp_end)),
            row.overtaken or 0,
        ])

    filename = f"historique_{filters.date_from.isoformat()}_{filters.date_to.isoformat()}.csv"
    # BOM UTF-8 : Excel ouvre alors correctement les accents.
    return Response('﻿' + buffer.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


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
    # Seuls les parcours ayant rejoint la file comptent : les inscriptions
    # jamais activées (impression non confirmée, échouée, expirée) ne sont
    # ni des visites ni des durées mesurables.
    query = query.filter(model.status.notin_(STATS_EXCLUDED_STATUSES))
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
    # « Temps total » = durée d'un parcours mené à terme : seul 'done' a une
    # fin de parcours. ``cancelled`` pose aussi timestamp_end, mais mesure
    # l'attente avant retrait — pas une durée de visite.
    return query.filter(model.timestamp_end.isnot(None), model.status == 'done')


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
