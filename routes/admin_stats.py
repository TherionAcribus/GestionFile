import random
from flask import Blueprint, render_template, request, jsonify, current_app as app
from sqlalchemy import func
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
        start_date = end_date - timedelta(days=2)  # 48 heures de données pour la vue par heure
        date_func = func.date_format(Patient.timestamp, '%Y-%m-%d %H:00:00')
    else:
        start_date = end_date - timedelta(days=30)  # 30 jours de données pour la vue par jour
        date_func = func.date(Patient.timestamp)

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

    categories = set(row.category for row in query)
    
    # Générer toutes les dates/heures possibles
    all_dates = []
    current = start_date
    while current <= end_date:
        if time_granularity == 'hour':
            all_dates.append(current.strftime('%Y-%m-%d %H:00:00'))
            current += timedelta(hours=1)
        else:
            all_dates.append(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)

    datasets = []
    for category in categories:
        data = []
        for date in all_dates:
            count = next((row.count for row in query if row.date == date and row.category == category), 0)
            data.append({'x': date, 'y': count})
        
        datasets.append({
            'label': category,
            'data': data,
            'fill': False,
            'borderColor': get_random_color(),
            'tension': 0.1
        })
    
    return {
        'datasets': datasets,
        'title': f'Évolution du nombre de patients par {chart_type}'
    }

def get_random_color():
    return f'rgba({random.randint(0,255)}, {random.randint(0,255)}, {random.randint(0,255)}, 1)'