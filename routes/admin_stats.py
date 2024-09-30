import random
from flask import Blueprint, render_template, request, jsonify, current_app as app
from sqlalchemy import func, text
from datetime import datetime, timedelta
from models import DashboardCard, Activity, Language, Patient, db

admin_stats_bp = Blueprint('admin_stats', __name__)

@admin_stats_bp.route('/admin/stats')
def admin_stats():
    return render_template('admin/stats.html')


@admin_stats_bp.route('/admin/stats/chart')
def get_chart_data():
    chart_type = request.args.get('chart_type', 'languages')
    chart_style = request.args.get('chart_style', 'pie')
    time_granularity = request.args.get('time_granularity', 'day')
    
    if chart_style == 'line':
        data = get_temporal_data(chart_type, time_granularity)
        title = f'Évolution du nombre de patients par {chart_type}'
    else:
        if chart_type == 'languages':
            data = get_language_data()
            title = 'Distribution des langues des patients'
        elif chart_type == 'activities':
            data = get_activity_data()
            title = 'Distribution des activités des patients'
        elif chart_type == 'waiting_times':
            data = get_waiting_times_data()
            title = 'Temps d\'attente des patients'
        elif chart_type == 'counter_times':
            data = get_counter_times_data()
            title = 'Temps au comptoir des patients'
        elif chart_type == 'total_times':
            data = get_total_times_data()
            title = 'Temps total des patients'
        elif chart_type == 'waiting_times_by_activity':
            data = get_waiting_times_by_activity()
            title = 'Temps d\'attente moyen par activité'
        elif chart_type == 'counter_times_by_activity':
            data = get_counter_times_by_activity()
            title = 'Temps au comptoir moyen par activité'
        elif chart_type == 'total_times_by_activity':
            data = get_total_times_by_activity()
            title = 'Temps total moyen par activité'
        else:
            return jsonify({'error': 'Type de graphique non valide'}), 400

        if chart_style == 'bar':
            data['datasets'][0]['backgroundColor'] = data['datasets'][0]['backgroundColor'][:len(data['labels'])]

    data['title'] = title
    
    return jsonify(data)

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

def get_temporal_data(chart_type, time_granularity):
    end_date = datetime.now()
    if time_granularity == 'hour':
        start_date = end_date - timedelta(days=2)
        date_func = func.date_format(Patient.timestamp, '%Y-%m-%d %H:00:00')
    else:
        start_date = end_date - timedelta(days=30)
        date_func = func.date(Patient.timestamp)

    if chart_type in ['activities', 'languages']:
        if chart_type == 'activities':
            query = db.session.query(
                date_func.label('date'),
                Activity.name.label('category'),
                func.count(Patient.id).label('count')
            ).join(Activity).filter(
                Patient.timestamp.between(start_date, end_date)
            ).group_by(
                date_func,
                Activity.name
            ).order_by(
                date_func
            ).all()
        elif chart_type == 'languages':
            query = db.session.query(
                date_func.label('date'),
                Language.name.label('category'),
                func.count(Patient.id).label('count')
            ).join(Language).filter(
                Patient.timestamp.between(start_date, end_date)
            ).group_by(
                date_func,
                Language.name
            ).order_by(
                date_func
            ).all()
        else:
            raise ValueError(f"Type de graphique non valide: {chart_type}")
        
    elif chart_type == 'waiting_times':
        query = db.session.query(
            date_func.label('date'),
            func.avg(text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_counter)")).label('avg_waiting_time')
        ).filter(
            Patient.timestamp.between(start_date, end_date),
            Patient.timestamp_counter.isnot(None)
        ).group_by(
            date_func
        ).order_by(
            date_func
        ).all()
    elif chart_type == 'counter_times':
        query = db.session.query(
            date_func.label('date'),
            func.avg(text("TIMESTAMPDIFF(SECOND, timestamp_counter, timestamp_end)")).label('avg_counter_time')
        ).filter(
            Patient.timestamp.between(start_date, end_date),
            Patient.timestamp_counter.isnot(None),
            Patient.timestamp_end.isnot(None)
        ).group_by(
            date_func
        ).order_by(
            date_func
        ).all()
    elif chart_type == 'total_times':
        query = db.session.query(
            date_func.label('date'),
            func.avg(text("TIMESTAMPDIFF(SECOND, timestamp, timestamp_end)")).label('avg_total_time')
        ).filter(
            Patient.timestamp.between(start_date, end_date),
            Patient.timestamp_end.isnot(None)
        ).group_by(
            date_func
        ).order_by(
            date_func
        ).all()
    else:
        raise ValueError(f"Type de graphique non valide: {chart_type}")
    
    all_dates = []
    current = start_date
    while current <= end_date:
        if time_granularity == 'hour':
            all_dates.append(current.strftime('%Y-%m-%d %H:00:00'))
            current += timedelta(hours=1)
        else:
            all_dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

    datasets = [{
        'label': f'Moyenne {chart_type}',
        'data': [],
        'fill': False,
        'borderColor': get_random_color(),
        'tension': 0.1
    }]

    for date in all_dates:
        value = next((row[1].total_seconds() / 60 if row[1] else 0 for row in query if row[0] == date), 0)
        datasets[0]['data'].append({'x': date, 'y': value})

    return {
        'datasets': datasets,
        'title': f'Évolution du {chart_type}'
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