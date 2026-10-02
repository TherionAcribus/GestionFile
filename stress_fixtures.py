"""Creation et nettoyage strictement traces des donnees de test staging."""
from __future__ import annotations

import uuid

from models import (Activity, Button, Counter, Patient, PatientHistory,
                    PatientStep, db)


PREFIX = 'STRESS-'


def prepare_fixtures(run):
    if run.mode != 'staging':
        return {}
    if run.fixture_ids:
        return run.fixture_ids
    token = run.uuid.replace('-', '')[:10].upper()
    activity = Activity(name=f'{PREFIX}{token}', letter='Z', notification=False)
    db.session.add(activity)
    db.session.flush()
    button = Button(by_user=True, code=f'stress-{token}', label=f'Test {token}',
                    is_active=True, is_present=True, shape='square',
                    activity_id=activity.id, sort_order=9999)
    counter = Counter(name=f'STRESS-{token[:7]}', is_active=True, sort_order=9999)
    counter.activities.append(activity)
    db.session.add_all([button, counter])
    db.session.flush()
    run.fixture_ids = {'activity_id': activity.id, 'button_id': button.id,
                       'counter_id': counter.id, 'patient_ids': []}
    db.session.commit()
    return run.fixture_ids


def create_synthetic_patient(run, status='standing'):
    ids = dict(run.fixture_ids or {})
    activity_id = ids.get('activity_id')
    if not activity_id:
        raise RuntimeError('Fixtures de stress absentes.')
    suffix = uuid.uuid4().hex[:12]
    patient = Patient(call_number=f'S{len(ids.get("patient_ids", [])) % 999:03d}',
                      activity_id=activity_id, status=status,
                      journey_id=f'stress:{run.uuid}:{suffix}')
    db.session.add(patient)
    db.session.flush()
    ids.setdefault('patient_ids', []).append(patient.id)
    run.fixture_ids = ids
    db.session.commit()
    return patient


def cleanup_fixtures(run):
    ids = dict(run.fixture_ids or {})
    patient_ids = [int(value) for value in ids.get('patient_ids', [])]
    activity_id = ids.get('activity_id')
    # Relecture par prefixe indispensable apres des ecritures concurrentes :
    # meme si deux mises a jour JSON se croisent, chaque patient reste marque.
    tagged_ids = [row[0] for row in db.session.query(Patient.id).filter(
        Patient.journey_id.like(f'stress:{run.uuid}:%')).all()]
    patient_ids = sorted(set(patient_ids + tagged_ids))
    if patient_ids:
        PatientStep.query.filter(PatientStep.patient_id.in_(patient_ids)).delete(
            synchronize_session=False)
        PatientHistory.query.filter(
            PatientHistory.patient_source_id.in_(patient_ids)).delete(
            synchronize_session=False)
        Patient.query.filter(Patient.id.in_(patient_ids)).delete(
            synchronize_session=False)
    if ids.get('button_id'):
        Button.query.filter_by(id=ids['button_id']).delete(synchronize_session=False)
    if ids.get('counter_id'):
        counter = db.session.get(Counter, ids['counter_id'])
        if counter:
            counter.activities = []
            db.session.delete(counter)
    if activity_id:
        # Filet de securite apres crash : seul l identifiant cree pour ce run
        # est vise, jamais une activite portant simplement un nom ressemblant.
        Activity.query.filter_by(id=activity_id).delete(synchronize_session=False)
    run.fixture_ids = {}
    db.session.commit()


def cleanup_all_incomplete_runs():
    from models import StressTestRun
    runs = StressTestRun.query.filter(StressTestRun.state.in_({
        'completed', 'failed', 'aborted', 'interrupted'})).all()
    cleaned = 0
    for run in runs:
        if run.fixture_ids:
            cleanup_fixtures(run)
            cleaned += 1
    return cleaned
