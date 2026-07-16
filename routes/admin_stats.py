import random
from flask import Blueprint, render_template, request, jsonify, current_app as app
from sqlalchemy import func, text, or_
from datetime import datetime, timedelta
from models import DashboardCard, Activity, Language, Counter, Patient, PatientHistory, AggregatedStats, db
from routes.admin_security import require_permission, require_permission_api
from pagination import parse_page_params, paginate_query
from stats_params import parse_chart_request
import pytz

admin_stats_bp = Blueprint('admin_stats', __name__)

time_tz = pytz.timezone('Europe/Paris')

# Colonnes de tri autorisées (liste blanche) pour l'historique détaillé.
HISTORY_SORT_COLUMNS = {
    'call_number': PatientHistory.call_number,
    'timestamp': PatientHistory.timestamp,
    'status': PatientHistory.status,
    'day_of_week': PatientHistory.day_of_week,
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
    # Validation stricte + bornage de période (point 5.4) dans le cœur pur.
    req = parse_chart_request(request.args, now=datetime.now(time_tz))
    if not req.ok:
        return jsonify({'error': req.error}), 400

    chart_type = req.chart_type
    chart_style = req.chart_style
    time_granularity = req.time_granularity
    start_date, end_date = req.start_date, req.end_date

    # 1. Fetch Detailed Data (Patient or PatientHistory)
    if req.is_history:
        model = PatientHistory
        detailed_data = fetch_detailed_data(model, start_date, end_date, chart_type, req, chart_style, time_granularity)
    else:
        model = Patient
        detailed_data = fetch_detailed_data(model, start_date, end_date, chart_type, req, chart_style, time_granularity, join_models=True)

    # 2. Fetch Compressed Data (AggregatedStats) - Only for history
    compressed_data = []
    if req.is_history:
        compressed_data = fetch_compressed_data(start_date, end_date, chart_type, chart_style, time_granularity)

    # 3. Merge Data
    merged_data = merge_datasets(detailed_data, compressed_data, req.is_time)

    # 4. Format for Chart.js
    response_data = format_chart_data(merged_data, chart_type, chart_style, start_date, end_date, time_granularity)

    return jsonify(response_data)


def fetch_detailed_data(model, start_date, end_date, chart_type, req, chart_style, time_granularity, join_models=False):
    """Fetches data from Patient or PatientHistory tables."""

    base_query = db.session.query(model).filter(model.timestamp.between(start_date, end_date))
    base_query = apply_filters(base_query, model, req)

    # Prepare metrics and grouping
    date_func = get_date_func(model.timestamp, time_granularity, chart_style)
    
    query = None
    is_time = '_times' in chart_type
    
    if chart_type == 'languages':
        entity = Language
        join_condition = (model.language_id == Language.id) if not join_models else None
        group_col = Language.name
    elif chart_type in ['activities', '_by_activity']:
        entity = Activity
        join_condition = (model.activity_id == Activity.id) if not join_models else None
        group_col = Activity.name
    elif chart_type == 'counters':
        entity = Counter
        join_condition = (model.counter_id == Counter.id) if not join_models else None
        group_col = Counter.name
    else:
        entity = None
        group_col = None

    # Build Query
    entities = []
    if chart_style == 'line':
        entities.append(date_func.label('date'))
    
    if group_col is not None:
        entities.append(group_col.label('category'))
    
    # Metrics
    if is_time:
        time_col = get_time_column(model, chart_type)
        # Filter nulls
        if 'waiting' in chart_type: base_query = base_query.filter(model.timestamp_counter.isnot(None))
        if 'counter' in chart_type or 'total' in chart_type: base_query = base_query.filter(model.timestamp_end.isnot(None))
        
        entities.append(func.avg(time_col).label('value'))
        entities.append(func.count(model.id).label('count')) # Need count for weighted average merging
    else:
        entities.append(func.count(model.id).label('value')) # Value is count
        entities.append(func.count(model.id).label('count')) # Duplicate for uniform handling

    query = base_query.with_entities(*entities)

    if entity:
        if join_models:
            query = query.join(entity)
        else:
            query = query.join(entity, join_condition)
            
    # Grouping
    groups = []
    if chart_style == 'line':
        groups.append(text('date'))
    if group_col is not None:
        groups.append(group_col)
        
    if groups:
        query = query.group_by(*groups)
    
    return query.all()


def fetch_compressed_data(start_date, end_date, chart_type, chart_style, time_granularity):
    """Fetches data from AggregatedStats."""
    
    # Determine category_type based on chart_type
    category_type = None
    if chart_type == 'languages': category_type = 'language'
    elif 'activity' in chart_type: category_type = 'activity' # activities or *_by_activity
    elif chart_type == 'counters': category_type = 'counter'
    elif '_times' in chart_type: category_type = 'global' # waiting_times, etc.
    
    if not category_type: return []

    base_query = db.session.query(AggregatedStats).filter(
        AggregatedStats.date.between(start_date.date(), end_date.date()),
        AggregatedStats.category_type == category_type
    )

    # We need to join with related tables to get names (Language.name, etc.)
    date_func = get_date_func(AggregatedStats.date, time_granularity, chart_style)
    
    query = None
    entities = []
    groups = []
    
    if chart_style == 'line':
        entities.append(date_func.label('date'))
        groups.append(text('date'))

    # Join and Select Category Name
    if category_type == 'language':
        query = base_query.join(Language, AggregatedStats.category_id == Language.id)
        entities.append(Language.name.label('category'))
        groups.append(Language.name)
    elif category_type == 'activity':
        query = base_query.join(Activity, AggregatedStats.category_id == Activity.id)
        entities.append(Activity.name.label('category'))
        groups.append(Activity.name)
    elif category_type == 'counter':
        query = base_query.join(Counter, AggregatedStats.category_id == Counter.id)
        entities.append(Counter.name.label('category'))
        groups.append(Counter.name)
    else:
        query = base_query
    
    # Metrics selection
    if 'waiting_time' in chart_type: metric = AggregatedStats.avg_waiting_time
    elif 'counter_time' in chart_type: metric = AggregatedStats.avg_counter_time
    elif 'total_time' in chart_type: metric = AggregatedStats.avg_total_time
    else: metric = AggregatedStats.count # Default count

    # For aggregated queries (like pie chart of history), we sum counts or weighted average times
    # Aggregation is needed if multiple rows match grouping (e.g. multiple days for pie chart)
    
    is_time_metric = '_times' in chart_type
    
    if is_time_metric:
        # Weighted average: Sum(avg * count) / Sum(count)
        # SQL: SUM(avg * count) / NULLIF(SUM(count), 0)
        val = func.sum(metric * AggregatedStats.count) / func.nullif(func.sum(AggregatedStats.count), 0)
        entities.append(val.label('value'))
        entities.append(func.sum(AggregatedStats.count).label('count'))
    else:
        entities.append(func.sum(metric).label('value'))
        entities.append(func.sum(metric).label('count'))

    query = query.with_entities(*entities).group_by(*groups)
    
    return query.all()


def merge_datasets(detailed, compressed, is_time):
    """Merges detailed and compressed data."""
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
            # val is average.
            data_map[key]['weighted_sum'] += val * cnt
            data_map[key]['total_count'] += cnt
        else:
            # val is count.
            data_map[key]['weighted_sum'] += val
            data_map[key]['total_count'] += 0 # Irrelevant for sum
            
    # Reconstruct list
    result = []
    for key, v in data_map.items():
        date, category = key
        if is_time:
            final_val = v['weighted_sum'] / v['total_count'] if v['total_count'] > 0 else 0
        else:
            final_val = v['weighted_sum']
            
        # Create a dummy object or dict
        obj = type('obj', (object,), {'date': date, 'category': category, 'value': final_val, 'count': v['total_count']})
        result.append(obj)
        
    return result


def format_chart_data(data, chart_type, chart_style, start_date, end_date, time_granularity):
    is_time = '_times' in chart_type
    
    if chart_style == 'line':
        # Organize by category
        categories = set(d.category for d in data)
        datasets = []

        # Index (date, catégorie) -> valeur, construit en une passe. Remplace la
        # recherche linéaire ``next(...)`` refaite pour chaque case du produit
        # cartésien dates × catégories (point 5.4) : on passe d'un coût
        # O(dates × catégories × lignes) à un accès dictionnaire O(1).
        value_by_key = {(str(d.date), d.category): d.value for d in data}

        # Generate all dates
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
                if is_time: val = val / 60 # Minutes
                cat_data.append({'x': date, 'y': val})
                
            color = get_random_color()
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
        # Pie/Bar
        labels = [d.category for d in data]
        values = [d.value for d in data]
        if is_time: values = [v/60 for v in values]
        
        return {
            'labels': labels,
            'datasets': [{
                'data': values,
                'backgroundColor': generate_colors(len(labels))
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
    if req.is_history and req.day_of_week:
        # Gabarit : 1=lundi … 7=dimanche ; MySQL DAYOFWEEK : 1=dimanche … 7=samedi.
        days = [d + 1 if d < 7 else 1 for d in req.day_of_week]
        query = query.filter(func.dayofweek(model.timestamp).in_(days))

    return query

def get_date_func(col, granularity, style):
    if style != 'line':
        # For non-line charts, we don't group by date usually, but fetch function does.
        # We can return a dummy constant if we want to aggregate everything?
        # No, fetch functions expect a valid SQL expression.
        # If pie chart, we aggregate over the whole period.
        # So we can just use a constant string?
        return func.max(col) # Dummy aggregation
        
    fmt = '%Y-%m-%d %H:00:00' if granularity == 'hour' else '%Y-%m-%d'
    return func.date_format(col, fmt)

def get_time_column(model, chart_type):
    if 'waiting' in chart_type:
        return func.timestampdiff(text('SECOND'), model.timestamp, model.timestamp_counter)
    elif 'counter' in chart_type:
        return func.timestampdiff(text('SECOND'), model.timestamp_counter, model.timestamp_end)
    else:
        return func.timestampdiff(text('SECOND'), model.timestamp, model.timestamp_end)

def generate_colors(count):
    colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40']
    return colors[:count] if count <= len(colors) else [
        f'hsl({int(360 * i / count)}, 70%, 50%)' for i in range(count)
    ]

def get_random_color():
    return f'rgba({random.randint(0,255)}, {random.randint(0,255)}, {random.randint(0,255)}, 1)'

def get_chart_title(chart_type):
    titles = {
        'languages': 'Distribution des langues',
        'activities': 'Distribution des activités',
        'counters': 'Distribution des comptoirs',
        'waiting_times': 'Évolution des temps d\'attente',
        'counter_times': 'Évolution des temps au comptoir',
        'total_times': 'Évolution des temps totaux',
        'waiting_times_by_activity': 'Temps d\'attente moyen par activité',
        'counter_times_by_activity': 'Temps au comptoir moyen par activité',
        'total_times_by_activity': 'Temps total moyen par activité'
    }
    return titles.get(chart_type, 'Statistiques')
