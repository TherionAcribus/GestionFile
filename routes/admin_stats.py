import random
from flask import Blueprint, render_template, request, jsonify, current_app as app
from sqlalchemy import func, text, or_
from datetime import datetime, timedelta
from models import DashboardCard, Activity, Language, Counter, Patient, PatientHistory, AggregatedStats, db
from routes.admin_security import require_permission
import pytz

admin_stats_bp = Blueprint('admin_stats', __name__)

time_tz = pytz.timezone('Europe/Paris')


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


@admin_stats_bp.route('/admin/stats/chart')
def get_chart_data():
    chart_type = request.args.get('chart_type', 'languages')
    chart_style = request.args.get('chart_style', 'pie')
    time_granularity = request.args.get('time_granularity', 'day')
    date_type = request.args.get('date_type', 'current')
    
    start_date, end_date = get_date_range(date_type, request.args)
    if not start_date:
        return jsonify({'error': 'Dates manquantes'}), 400

    is_history = (date_type == 'history')
    
    # 1. Fetch Detailed Data (Patient or PatientHistory)
    if is_history:
        model = PatientHistory
        detailed_data = fetch_detailed_data(model, start_date, end_date, chart_type, request.args, chart_style, time_granularity)
    else:
        model = Patient
        detailed_data = fetch_detailed_data(model, start_date, end_date, chart_type, request.args, chart_style, time_granularity, join_models=True)

    # 2. Fetch Compressed Data (AggregatedStats) - Only for history
    compressed_data = []
    if is_history:
        compressed_data = fetch_compressed_data(start_date, end_date, chart_type, chart_style, time_granularity)

    # 3. Merge Data
    merged_data = merge_datasets(detailed_data, compressed_data, '_times' in chart_type)

    # 4. Format for Chart.js
    response_data = format_chart_data(merged_data, chart_type, chart_style, start_date, end_date, time_granularity)
    
    return jsonify(response_data)


def get_date_range(date_type, args):
    if date_type == 'history':
        period_type = args.get('period_type', '7')
        if period_type == 'custom':
            start_str = args.get('start_date')
            end_str = args.get('end_date')
            if not start_str or not end_str:
                return None, None
            start_date = datetime.strptime(start_str, '%Y-%m-%d')
            end_date = datetime.strptime(end_str, '%Y-%m-%d') + timedelta(days=1) - timedelta(seconds=1)
        else:
            days = int(period_type)
            end_date = datetime.now(time_tz)
            start_date = end_date - timedelta(days=days)
    else:
        today = datetime.now(time_tz).date()
        start_date = datetime.combine(today, datetime.min.time())
        end_date = datetime.combine(today, datetime.max.time())
    
    return start_date, end_date


def fetch_detailed_data(model, start_date, end_date, chart_type, filters, chart_style, time_granularity, join_models=False):
    """Fetches data from Patient or PatientHistory tables."""
    
    base_query = db.session.query(model).filter(model.timestamp.between(start_date, end_date))
    base_query = apply_filters(base_query, model, filters, not join_models)

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
                # Find matching row
                val = next((d.value for d in data if str(d.date) == date and d.category == cat), 0)
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


def apply_filters(query, model, request_args, is_history=False):
    # Filtre par comptoir
    counter_filter = request_args.getlist('counter_filter')
    if counter_filter:
        query = query.filter(model.counter_id.in_([int(c) for c in counter_filter]))

    # Filtre par activité
    activity_filter = request_args.getlist('activity_filter')
    if activity_filter:
        query = query.filter(model.activity_id.in_([int(a) for a in activity_filter]))

    # Filtre par langue
    language_filter = request_args.getlist('language_filter')
    if language_filter:
        query = query.filter(model.language_id.in_([int(l) for l in language_filter]))

    if is_history:
        day_of_week_filter = request_args.getlist('day_of_week_filter')
        if day_of_week_filter:
            days = [int(d) for d in day_of_week_filter]
            query = query.filter(func.dayofweek(model.timestamp).in_([d + 1 if d < 7 else 1 for d in days]))

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
