import random
from flask import Blueprint, render_template, request, jsonify, current_app as app
from sqlalchemy import func, text
from datetime import datetime, timedelta
from models import DashboardCard, Activity, Language, Counter, Patient, PatientHistory, db
import pytz

admin_stats_bp = Blueprint('admin_stats', __name__)

time_tz = pytz.timezone('Europe/Paris')

@admin_stats_bp.route('/admin/stats')
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
    print("GETCHARTDATA", request.values)
    chart_type = request.args.get('chart_type', 'languages')
    chart_style = request.args.get('chart_style', 'pie')
    time_granularity = request.args.get('time_granularity', 'day')
    date_type = request.args.get('date_type', 'current')
    
    # Gestion des dates pour l'historique
    if date_type == 'history':
        period_type = request.args.get('period_type', '7')
        model = PatientHistory
        join_models = False
        is_history = True
        
        if period_type == 'custom':
            start_date = request.args.get('start_date')
            end_date = request.args.get('end_date')
            if not start_date or not end_date:
                return jsonify({'error': 'Dates manquantes'}), 400
            
            start_date = datetime.strptime(start_date, '%Y-%m-%d')
            end_date = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        else:
            days = int(period_type)
            end_date = datetime.now(time_tz)
            start_date = end_date - timedelta(days=days)
    else:
        today = datetime.now(time_tz).date()
        start_date = datetime.combine(today, datetime.min.time())
        end_date = datetime.combine(today, datetime.max.time())
        model = Patient
        join_models = True
        is_history = False

    # Création de la requête de base avec le filtre de dates
    base_query = db.session.query(model).filter(
        model.timestamp.between(start_date, end_date)
    )

    # Application des filtres
    base_query = apply_filters(base_query, model, request.args, is_history)

    if chart_style == 'line':
        data = get_temporal_data(
            model=model,
            chart_type=chart_type,
            time_granularity=time_granularity,
            join_models=join_models,
            start_date=start_date,
            end_date=end_date,
            base_query=base_query
        )
    else:
        data = get_aggregated_data(
            model=model,
            chart_type=chart_type,
            join_models=join_models,
            base_query=base_query
        )

    return jsonify(data)


def apply_filters(query, model, request_args, is_history=False):
    """
    Applique les filtres à la requête en fonction des paramètres reçus
    """
    # Filtre par comptoir
    counter_filter = request_args.getlist('counter_filter')
    if counter_filter:
        counter_ids = [int(c_id) for c_id in counter_filter]
        query = query.filter(model.counter_id.in_(counter_ids))

    # Filtre par activité
    activity_filter = request_args.getlist('activity_filter')
    if activity_filter:
        activity_ids = [int(a_id) for a_id in activity_filter]
        query = query.filter(model.activity_id.in_(activity_ids))

    # Filtre par langue
    language_filter = request_args.getlist('language_filter')
    if language_filter:
        language_ids = [int(l_id) for l_id in language_filter]
        query = query.filter(model.language_id.in_(language_ids))

    # Filtre par jour de la semaine (uniquement pour l'historique)
    if is_history:
        day_of_week_filter = request_args.getlist('day_of_week_filter')
        if day_of_week_filter:
            days = [int(d) for d in day_of_week_filter]
            # Pour MySQL: DAYOFWEEK() retourne 1 pour Dimanche, 2 pour Lundi, etc.
            # Nous ajustons pour correspondre à notre interface (1 pour Lundi)
            query = query.filter(
                func.DAYOFWEEK(model.timestamp).in_([
                    d + 1 if d < 7 else 1 for d in days
                ])
            )

    return query

def get_aggregated_data(model, chart_type, join_models, base_query):
    """
    Obtient les données agrégées avec le bon formatage
    """
    is_time = '_times' in chart_type
    
    # Construction de la requête selon le type de modèle et de données
    if join_models:  # Pour Patient (données actuelles)
        if chart_type == 'languages':
            query = (
                base_query.with_entities(
                    Language.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Language)
                .group_by(Language.name, Language.id)
            )
        elif chart_type == 'activities':
            query = (
                base_query.with_entities(
                    Activity.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Activity)
                .group_by(Activity.name, Activity.id)
            )
        elif chart_type == 'counters':
            query = (
                base_query.with_entities(
                    Counter.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Counter)
                .group_by(Counter.name, Counter.id)
            )
        elif is_time:
            if 'waiting_times' in chart_type:
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)")
                filters = [model.timestamp_counter.isnot(None)]
            elif 'counter_times' in chart_type:
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)")
                filters = [model.timestamp_counter.isnot(None), model.timestamp_end.isnot(None)]
            else:  # total_times
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)")
                filters = [model.timestamp_end.isnot(None)]

            base_query = base_query.filter(*filters)

            if '_by_activity' in chart_type:
                query = (
                    base_query.with_entities(
                        Activity.name.label('category'),
                        func.avg(time_diff).label('value')
                    )
                    .join(Activity)
                    .group_by(Activity.name, Activity.id)
                )
            else:
                query = base_query.with_entities(
                    func.avg(time_diff).label('value')
                )

    else:  # Pour PatientHistory
        if chart_type == 'languages':
            query = (
                base_query.with_entities(
                    Language.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Language, Language.id == model.language_id)
                .group_by(Language.name, Language.id)
            )
        elif chart_type == 'activities':
            query = (
                base_query.with_entities(
                    Activity.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Activity, Activity.id == model.activity_id)
                .group_by(Activity.name, Activity.id)
            )
        elif chart_type == 'counters':
            query = (
                base_query.with_entities(
                    Counter.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Counter, Counter.id == model.activity_id)
                .group_by(Counter.name, Counter.id)
            )
        elif is_time:
            if 'waiting_times' in chart_type:
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)")
                filters = [model.timestamp_counter.isnot(None)]
            elif 'counter_times' in chart_type:
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)")
                filters = [model.timestamp_counter.isnot(None), model.timestamp_end.isnot(None)]
            else:  # total_times
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)")
                filters = [model.timestamp_end.isnot(None)]

            base_query = base_query.filter(*filters)

            if '_by_activity' in chart_type:
                query = (
                    base_query.with_entities(
                        Activity.name.label('category'),
                        func.avg(time_diff).label('value')
                    )
                    .join(Activity, Activity.id == model.activity_id)
                    .group_by(Activity.name, Activity.id)
                )
            else:
                query = base_query.with_entities(
                    func.avg(time_diff).label('value')
                )

    results = query.all()

    # Formatage des résultats
    if chart_type in ['languages', 'activities', 'counters']:
        labels = [row.category for row in results]
        values = [row.count for row in results]
    elif is_time:
        if '_by_activity' in chart_type:
            labels = [row.category for row in results]
            values = [float(row.value) / 60 if row.value else 0 for row in results]
        else:
            labels = [get_chart_label(chart_type)]
            values = [float(results[0].value) / 60 if results and results[0].value else 0]
    else:
        labels = ['Total']
        values = [results[0].count if results else 0]

    return {
        'labels': labels,
        'datasets': [{
            'data': values,
            'backgroundColor': generate_colors(len(labels))
        }],
        'title': get_chart_title(chart_type),
        'isTime': is_time
    }
    


def get_time_statistics(model, time_type):
    if time_type == 'waiting_times':
        time_diff = "TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)"
        title = "Temps d'attente"
    elif time_type == 'counter_times':
        time_diff = "TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)"
        title = "Temps au comptoir"
    else:
        time_diff = "TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)"
        title = "Temps total"

    # Calcul par activité
    if '_by_activity' in time_type:
        if isinstance(model, Patient):
            stats = db.session.query(
                Activity.name,
                func.avg(text(time_diff)).label('avg_time')
            ).join(
                model.activity
            ).filter(
                get_time_filters(model, time_type)
            ).group_by(
                Activity.name
            ).all()
        else:
            stats = db.session.query(
                Activity.name,
                func.avg(text(time_diff)).label('avg_time')
            ).join(
                Activity,
                model.activity_id == Activity.id
            ).filter(
                get_time_filters(model, time_type)
            ).group_by(
                Activity.name
            ).all()
    else:
        # Calcul global
        avg_time = db.session.query(
            func.avg(text(time_diff))
        ).filter(
            get_time_filters(model, time_type)
        ).scalar()
        stats = [(title, avg_time)]

    return {
        'labels': [stat[0] for stat in stats],
        'datasets': [{
            'data': [float(stat[1]) / 60 if stat[1] else 0 for stat in stats],
            'backgroundColor': generate_colors(len(stats))
        }]
    }

def get_temporal_data(model, chart_type, time_granularity, join_models, start_date, end_date, base_query):
    """
    Obtient les données temporelles avec une courbe par catégorie
    """
    # Configuration de la fonction de date selon la granularité
    if time_granularity == 'hour':
        date_format = '%Y-%m-%d %H:00:00'
    else:
        date_format = '%Y-%m-%d'

    # Utiliser date_format directement dans la requête SQL
    date_func = func.date_format(model.timestamp, date_format).label('date')

    # Construction de la requête
    if join_models:
        if chart_type == 'languages':
            query = (
                base_query.with_entities(
                    date_func,
                    Language.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Language)
                .group_by(text('date'), Language.name, Language.id)
            )
            categories = db.session.query(Language.name).all()
            
        elif chart_type == 'activities':
            query = (
                base_query.with_entities(
                    date_func,
                    Activity.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Activity)
                .group_by(text('date'), Activity.name, Activity.id)
            )
            categories = db.session.query(Activity.name).all()

        elif chart_type == 'counters':
            query = (
                base_query.with_entities(
                    date_func,
                    Counter.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Counter)
                .group_by(text('date'), Counter.name, Counter.id)
            )
            categories = db.session.query(Counter.name).all()
            
        elif '_times' in chart_type:
            if 'waiting_times' in chart_type:
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)")
                filters = [model.timestamp_counter.isnot(None)]
            elif 'counter_times' in chart_type:
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)")
                filters = [model.timestamp_counter.isnot(None), model.timestamp_end.isnot(None)]
            else:
                time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)")
                filters = [model.timestamp_end.isnot(None)]
            
            base_query = base_query.filter(*filters)
            
            if '_by_activity' in chart_type:
                query = (
                    base_query.with_entities(
                        date_func,
                        Activity.name.label('category'),
                        func.avg(time_diff).label('value')
                    )
                    .join(Activity)
                    .group_by(text('date'), Activity.name, Activity.id)
                )
                categories = db.session.query(Activity.name).all()
            else:
                query = (
                    base_query.with_entities(
                        date_func,
                        func.avg(time_diff).label('value')
                    )
                    .group_by(text('date'))
                )
                categories = [('Total',)]
        else:
            query = (
                base_query.with_entities(
                    date_func,
                    func.count(model.id).label('count')
                )
                .group_by(text('date'))
            )
            categories = [('Total',)]
    else:
        # PatientHistory queries
        if chart_type == 'languages':
            query = (
                base_query.with_entities(
                    date_func,
                    Language.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Language, model.language_id == Language.id)
                .group_by(text('date'), Language.name, Language.id)
            )
            categories = db.session.query(Language.name).all()
        elif chart_type == 'activities':
            query = (
                base_query.with_entities(
                    date_func,
                    Activity.name.label('category'),
                    func.count(model.id).label('count')
                )
                .join(Activity, model.activity_id == Activity.id)
                .group_by(text('date'), Activity.name, Activity.id)
            )
            categories = db.session.query(Activity.name).all()
        elif '_times' in chart_type:
            # ... Similar structure for time metrics ...
            pass

    # Exécuter la requête
    results = query.all()

    # Création de toutes les dates possibles
    all_dates = []
    current = start_date
    increment = timedelta(hours=1) if time_granularity == 'hour' else timedelta(days=1)
    while current <= end_date:
        all_dates.append(current.strftime(date_format))
        current += increment

    # Création des datasets
    datasets = []
    is_time = '_times' in chart_type
    
    for category in categories:
        category_name = category[0]
        category_data = []
        
        for date in all_dates:
            if is_time:
                value = next(
                    (float(r.value) / 60 if r.value else 0 
                     for r in results 
                     if r.date == date and getattr(r, 'category', 'Total') == category_name),
                    0
                )
            else:
                value = next(
                    (r.count for r in results 
                     if r.date == date and getattr(r, 'category', 'Total') == category_name),
                    0
                )
            
            category_data.append({
                'x': date,
                'y': value
            })
        
        color = get_random_color()
        datasets.append({
            'label': category_name,
            'data': category_data,
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

def format_query_results(results, chart_type):
    """
    Formate les résultats de requête pour Chart.js selon le type de graphique
    """
    formatted_data = []
    
    for row in results:
        if '_times' in chart_type:
            # Pour les données temporelles (temps d'attente, etc.)
            value = float(row.value) / 60 if row.value is not None else 0
        else:
            # Pour les comptages (patients par langue, etc.)
            value = row.count if hasattr(row, 'count') else row.value

        # Format pour graphique temporel
        formatted_data.append({
            'x': row.date,
            'y': value
        })

    return formatted_data

# Fonctions utilitaires
def generate_colors(count):
    colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40']
    return colors[:count] if count <= len(colors) else [
        f'hsl({h}, 70%, 50%)'
        for h in [int(360 * i / count) for i in range(count)]
    ]

def get_time_filters(model, time_type):
    filters = []
    if 'waiting_' in time_type:
        filters.append(model.timestamp_counter.isnot(None))
    if 'counter_' in time_type or 'total_' in time_type:
        filters.append(model.timestamp_end.isnot(None))
    return filters

def get_language_data():
    language_counts = db.session.query(
        Language.name, 
        func.count(Patient.id)
    ).join(
        Patient, 
        Patient.language_id == Language.id
    ).group_by(
        Language.name
    ).all()

    return {
        'labels': [lang for lang, _ in language_counts],
        'datasets': [{
            'data': [count for _, count in language_counts],
            'backgroundColor': [
                '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40'
            ]
        }]
    }

def get_activity_data():
    activity_counts = db.session.query(
        Activity.name, 
        func.count(Patient.id)
    ).join(
        Patient, 
        Patient.activity_id == Activity.id
    ).group_by(
        Activity.name
    ).all()

    return {
        'labels': [act for act, _ in activity_counts],
        'datasets': [{
            'data': [count for _, count in activity_counts],
            'backgroundColor': [
                '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF', '#FF9F40'
            ]
        }]
    }


def get_waiting_times_data():
    waiting_times = db.session.query(
        func.avg(text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)")).label('avg_waiting_time')
    ).filter(
        Patient.timestamp_counter.isnot(None)
    ).scalar()

    return {
        'labels': ['Temps d\'attente moyen'],
        'datasets': [{
            'data': [float(waiting_times) / 60 if waiting_times is not None else 0],  # Convert to minutes
            'backgroundColor': ['#FF6384']
        }]
    }

def get_counter_times_data():
    counter_times = db.session.query(
        func.avg(text("TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)")).label('avg_counter_time')
    ).filter(
        Patient.timestamp_counter.isnot(None),
        Patient.timestamp_end.isnot(None)
    ).scalar()

    return {
        'labels': ['Temps au comptoir moyen'],
        'datasets': [{
            'data': [float(counter_times) / 60 if counter_times is not None else 0],  # Convert to minutes
            'backgroundColor': ['#36A2EB']
        }]
    }

def get_total_times_data():
    total_times = db.session.query(
        func.avg(text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)")).label('avg_total_time')
    ).filter(
        Patient.timestamp_end.isnot(None)
    ).scalar()

    return {
        'labels': ['Temps total moyen'],
        'datasets': [{
            'data': [float(total_times) / 60 if total_times is not None else 0],  # Convert to minutes
            'backgroundColor': ['#FFCE56']
        }]
    }

def get_waiting_times_by_activity():
    waiting_times = db.session.query(
        Activity.name,
        func.avg(text("TIMESTAMPDIFF(SECOND, Patient.timestamp, Patient.timestamp_counter)")).label('avg_waiting_time')
    ).join(Patient).filter(
        Patient.timestamp_counter.isnot(None)
    ).group_by(Activity.name).all()

    return {
        'labels': [activity for activity, _ in waiting_times],
        'datasets': [{
            'data': [float(time) / 60 if time is not None else 0 for _, time in waiting_times],  # Convert to minutes
            'backgroundColor': [get_random_color() for _ in waiting_times]
        }]
    }

def get_counter_times_by_activity():
    counter_times = db.session.query(
        Activity.name,
        func.avg(text("TIMESTAMPDIFF(SECOND, Patient.timestamp_counter, Patient.timestamp_end)")).label('avg_counter_time')
    ).join(Patient).filter(
        Patient.timestamp_counter.isnot(None),
        Patient.timestamp_end.isnot(None)
    ).group_by(Activity.name).all()

    return {
        'labels': [activity for activity, _ in counter_times],
        'datasets': [{
            'data': [float(time) / 60 if time is not None else 0 for _, time in counter_times],  # Convert to minutes
            'backgroundColor': [get_random_color() for _ in counter_times]
        }]
    }

def get_total_times_by_activity():
    total_times = db.session.query(
        Activity.name,
        func.avg(text("TIMESTAMPDIFF(SECOND, Patient.timestamp, Patient.timestamp_end)")).label('avg_total_time')
    ).join(Patient).filter(
        Patient.timestamp_end.isnot(None)
    ).group_by(Activity.name).all()

    return {
        'labels': [activity for activity, _ in total_times],
        'datasets': [{
            'data': [float(time) / 60 if time is not None else 0 for _, time in total_times],  # Convert to minutes
            'backgroundColor': [get_random_color() for _ in total_times]
        }]
    }

def get_random_color():
    return f'rgba({random.randint(0,255)}, {random.randint(0,255)}, {random.randint(0,255)}, 1)'

def format_temporal_data(results, start_date, end_date, time_granularity):
    """
    Formate les données temporelles pour Chart.js
    """
    dates = []
    current = start_date
    increment = timedelta(hours=1) if time_granularity == 'hour' else timedelta(days=1)
    
    # Création de toutes les dates/heures possibles
    while current <= end_date:
        if time_granularity == 'hour':
            dates.append(current.strftime('%Y-%m-%d %H:00:00'))
        else:
            dates.append(current.strftime('%Y-%m-%d'))
        current += increment

    # Construction du dataset
    datasets = [{
        'label': 'Nombre de patients',
        'data': [],
        'fill': False,
        'borderColor': '#36A2EB',
        'tension': 0.1
    }]

    # Remplissage avec les données ou 0 si pas de données
    for date in dates:
        value = next((row[1] for row in results if row[0] == date), 0)
        datasets[0]['data'].append({
            'x': date,
            'y': value
        })

    return {
        'datasets': datasets,
        'title': 'Évolution temporelle'
    }

def create_history_query(model, chart_type, date_func):
    """
    Crée la requête appropriée pour les données historiques
    """
    if chart_type == 'languages':
        return (
            db.session.query(
                date_func.label('date'),
                Language.name.label('category'),
                func.count(model.id).label('count')
            )
            .join(Language, model.language_id == Language.id)
            .group_by(date_func, Language.name, Language.id)  # Ajout de l'ID
        )
    elif chart_type == 'activities':
        return (
            db.session.query(
                date_func.label('date'),
                Activity.name.label('category'),
                func.count(model.id).label('count')
            )
            .join(Activity, model.activity_id == Activity.id)
            .group_by(date_func, Activity.name, Activity.id)  # Ajout de l'ID
        )
    elif '_times' in chart_type:
        if 'waiting_times' in chart_type:
            time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)")
        elif 'counter_times' in chart_type:
            time_diff = text("TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)")
        else:
            time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)")
            
        if '_by_activity' in chart_type:
            return (
                db.session.query(
                    date_func.label('date'),
                    Activity.name.label('category'),
                    func.avg(time_diff).label('value')
                )
                .join(Activity, model.activity_id == Activity.id)
                .group_by(date_func, Activity.name, Activity.id)  # Ajout de l'ID
            )
        else:
            return (
                db.session.query(
                    date_func.label('date'),
                    func.avg(time_diff).label('value')
                )
                .group_by(date_func)
            )
    
    return db.session.query(
        date_func.label('date'),
        func.count(model.id).label('count')
    ).group_by(date_func)


def add_category_metrics(query, model, chart_type):
    """
    Ajoute les métriques de catégorie (langues ou activités) à la requête
    """
    if chart_type == 'languages':
        return (
            query.with_entities(
                Language.name.label('category'),
                func.count(model.id).label('count')
            )
            .join(Language)
            .group_by(Language.name, Language.id)  
        )
    elif chart_type == 'activities':
        return (
            query.with_entities(
                Activity.name.label('category'),
                func.count(model.id).label('count')
            )
            .join(Activity)
            .group_by(Activity.name, Activity.id) 
        )
    elif chart_type == 'counters':
        return (
            query.with_entities(
                Counter.name.label('category'),
                func.count(model.id).label('count')
            )
            .join(Counter)
            .group_by(Counter.name, Counter.id) 
        )
    return query

def add_time_metrics(query, model, chart_type):
    """
    Ajoute les métriques de temps à la requête
    """
    if 'waiting_times' in chart_type:
        time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)")
        filters = [model.timestamp_counter.isnot(None)]
    elif 'counter_times' in chart_type:
        time_diff = text("TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)")
        filters = [model.timestamp_counter.isnot(None), model.timestamp_end.isnot(None)]
    else:  # total_times
        time_diff = text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)")
        filters = [model.timestamp_end.isnot(None)]

    base = query.filter(*filters)
    
    if '_by_activity' in chart_type:
        return (
            base.with_entities(
                Activity.name.label('category'),
                func.avg(time_diff).label('value')
            )
            .join(Activity)
            .group_by(Activity.name, Activity.id)  # Ajout de l'ID dans le GROUP BY
        )
    
    return base.with_entities(
        func.avg(time_diff).label('value')
    )



def add_metrics_to_query(base_query, model, chart_type):
    """
    Ajoute les métriques appropriées à la requête en fonction du type de graphique
    """
    if chart_type in ['languages', 'activities', "counters"]:
        return add_category_metrics(base_query, model, chart_type)
    elif '_times' in chart_type:
        return add_time_metrics(base_query, model, chart_type)
    else:
        # Cas par défaut : compte simple
        return base_query.with_entities(
            func.count(model.id).label('count')
        )
    

def get_chart_label(chart_type):
    """
    Retourne le label approprié pour le type de graphique
    """
    labels = {
        'languages': 'Patients par langue',
        'activities': 'Patients par activité',
        'counters': "Patients par comptoir",
        'waiting_times': 'Temps d\'attente (minutes)',
        'counter_times': 'Temps au comptoir (minutes)',
        'total_times': 'Temps total (minutes)',
        'waiting_times_by_activity': 'Temps d\'attente par activité (minutes)',
        'counter_times_by_activity': 'Temps au comptoir par activité (minutes)',
        'total_times_by_activity': 'Temps total par activité (minutes)'
    }
    return labels.get(chart_type, 'Nombre de patients')


def get_chart_title(chart_type):
    """
    Retourne le titre approprié pour le type de graphique
    """
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