import base64
import io
import json
import qrcode
from flask import Blueprint, url_for, request, session, current_app as app, jsonify
from datetime import datetime, date, timedelta
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from cryptography.fernet import Fernet, InvalidToken
from google.cloud import texttospeech
from google.oauth2 import service_account
from utils import replace_balise_announces, replace_balise_phone, get_text_translation, get_activity_message_translation
from gtts import gTTS
from models import Patient, PatientHistory, Counter, AlgoRule, ConfigOption, Language, CallNumberSequence, db, get_queue_revision, DAY_ABBREVIATIONS
from communication import communikation, notify_patient_phone
from config import time_tz
from auth_utils import require_app_token_or_login, make_patient_phone_token
from call_numbering import next_category_call_number, next_simple_call_number
from announcement_audio import cached_announcement_url
from announcement_dispatcher import announcement_dispatcher
from activity_explain import is_open_at, ENGLISH_DAY_NAMES

engine_bp = Blueprint('engine', __name__)

# Timeout (secondes) sur les appels réseau vers les services de synthèse
# vocale externes (gTTS / Google Cloud TTS). Ils tournent désormais en tâche
# de fond (cf. trigger_async_audio_calling), donc ce timeout ne bloque plus
# jamais l'appel du patient suivant -- il évite juste qu'un thread de fond
# reste accroché indéfiniment si le service externe ne répond plus.
TTS_TIMEOUT_SECONDS = 8


@engine_bp.route('/call_next/<int:counter_id>', methods=['GET', 'POST'])
@require_app_token_or_login
def call_next_http(counter_id):
    if request.method == "GET":
        app.logger.warning("Deprecated GET /call_next (use POST).")

    ok, result = call_next(counter_id)
    if ok:
        return jsonify(result.to_dict()), 200

    if result in {"no_patient", "no_patient_for_counter"}:
        return "", 204

    return jsonify({"error": result}), 409


def claim_patient(patient_id, counter_id):
    """ Réclamation ATOMIQUE d'un patient par un comptoir.

    Renvoie True si CE comptoir a décroché le patient, False s'il était déjà pris.

    Auparavant on lisait next_patient.status ('standing') puis on écrivait
    'calling' dans une seconde étape : deux comptoirs pouvaient sélectionner le
    même patient avant que l'un des deux ne committe, se retrouver tous les deux
    avec le même patient, et le dernier commit écrasait le counter_id de l'autre.

    On fait un UPDATE conditionnel « ... WHERE id=? AND status='standing' » dont
    on vérifie le rowcount : sous InnoDB (MySQL 8) comme sous SQLite, un seul des
    comptoirs concurrents verra claimed == 1 ; l'autre verra 0. La transition du
    patient ET l'activation du comptoir sont validées dans la MÊME transaction
    (un seul commit).

    Fonction partagée : `call_next` (appel du suivant) et le service d'appel
    ciblé s'en servent tous les deux. Elle existait auparavant en deux copies,
    dont l'une sans réessai ni nettoyage des `calling` périmés.
    """
    try:
        claimed = (
            db.session.query(Patient)
            .filter(Patient.id == patient_id, Patient.status == "standing")
            .update(
                {"status": "calling", "counter_id": counter_id},
                synchronize_session=False,
            )
        )
        if claimed:
            db.session.query(Counter).filter(Counter.id == counter_id).update(
                {"is_active": True}, synchronize_session=False
            )
        db.session.commit()
        return bool(claimed)
    except Exception:
        db.session.rollback()
        raise


def call_next(counter_id, attempts=0):
    # pour éviter de prendre deux fois le même patient, en vérifie en l'appelant qu'il est toujours en attente sinon on rappelle un patient.
    # pour éviter des boucles infinies, on considère qu'après x (5) essais on abandonne. Peut probable que 5 comptoirs appellent en même temps des patients.
    max_attempts = 5

    # Nettoyage des patients précédents en statut 'calling' pour ce comptoir
    if attempts == 0:
        previous_patients = Patient.query.filter_by(status='calling', counter_id=counter_id).all()
        for patient in previous_patients:
            patient.status = 'done'
            # Fin horodatée : sans timestamp_end, ces clôtures ne participaient
            # à aucune statistique de durée. Le comptoir reste rattaché.
            patient.timestamp_end = datetime.now(time_tz)
            app.logger.info(f"Patient {patient.id} status updated to 'done' for counter {counter_id} (fallback)")
        db.session.commit()

    if attempts >= max_attempts:
        app.logger.warning(f"Max attempts reached for counter {counter_id}")
        return False, "max_loop"

    if Patient.query.count() == 0:
        app.logger.info("No patients available")
        return False, "no_patient"
    
    next_patient = algo_choice_next_patient(counter_id)

    if next_patient is None:
        app.logger.info(f"No next patient found for counter {counter_id}")
        return False, "no_patient_for_counter"

    try:
        claimed = claim_patient(next_patient.id, counter_id)
    except Exception as e:
        app.logger.error(f"Error claiming patient {next_patient.id}: {str(e)}")
        return call_next(counter_id, attempts=attempts+1)

    if not claimed:
        # Le patient a été réclamé par un autre comptoir entre-temps : on réessaie
        # avec le candidat suivant.
        app.logger.info(f"Patient {next_patient.id} already claimed by another counter, retrying. Attempt {attempts + 1}")
        return call_next(counter_id, attempts=attempts+1)

    # Comptabilise les patients réellement dépassés — SEULEMENT après une
    # réclamation réussie : un comptoir perdant d'une course ne doit pas
    # gonfler les compteurs alors qu'il n'a finalement appelé personne.
    mark_overtaken_patients(next_patient, counter_id)

    # Après le commit, next_patient est expiré : le prochain accès recharge l'état
    # committé (status='calling', counter_id renseigné).
    app.logger.info(f"Patient {next_patient.id} status updated to 'calling' for counter {counter_id}")

    trigger_async_audio_calling(counter_id, next_patient.id, next_patient.language.code)
    notify_patient_phone(next_patient.call_number)

    return True, next_patient


def trigger_async_audio_calling(counter_id, patient_id, language_code):
    """Planifie une annonce vocale FIFO sans retarder l'appel du patient.

    Le dispatcher conserve l'ordre de soumission, borne le nombre de travaux
    pendants et isole les appels externes TTS du traitement HTTP.
    """
    flask_app = app._get_current_object()

    def _job():
        patient = db.session.get(Patient, patient_id)
        if not patient:
            return
        audio_url = generate_audio_calling(counter_id, patient, language_code=language_code)
        if audio_url:
            communikation("update_audio", event="audio", data=audio_url)

    announcement_dispatcher.submit(flask_app, _job)


def get_applicable_algo_rules(number_of_patients, now=None):
    """Règles RÉELLEMENT applicables à l'instant donné, évaluées en liste.

    Applicable = créneau horaire + plage d'effectif de la file + jour de la
    semaine. Le jour est testé en Python : ``days_of_week`` est une chaîne CSV
    d'abréviations qu'un LIKE SQL ne peut pas tester proprement.

    L'instant est pris dans le fuseau de l'application (``config.time_tz``) :
    les créneaux horaires ne doivent pas dépendre du fuseau de la machine ou
    du conteneur. ``now.time()`` renvoie une heure naïve, comparable aux
    colonnes ``db.Time``.

    ``now`` est injectable pour les tests.
    """
    now = now or datetime.now(time_tz)
    current_time = now.time()
    day_abbr = DAY_ABBREVIATIONS[now.weekday()]
    app.logger.debug('algo rules: %s %s, %s patients en attente',
                     day_abbr, current_time, number_of_patients)

    rules = AlgoRule.query.filter(
        AlgoRule.start_time <= current_time,
        AlgoRule.end_time >= current_time,
        AlgoRule.min_patients <= number_of_patients,
        AlgoRule.max_patients >= number_of_patients,
    ).all()

    applicable = [
        rule for rule in rules
        if day_abbr in (day.strip() for day in rule.days_of_week.split(','))
    ]
    app.logger.debug('applicable_rules %s', applicable)
    return applicable


def pick_priority_patient(candidates, applicable_rules):
    """Premier patient prioritaire parmi ``candidates``, déjà triés
    ``(timestamp, id)``. Renvoie ``None`` si aucun ne satisfait sa règle —
    l'appelant retombe alors sur le premier de la liste (FIFO).

    Niveau 1 = priorité la plus haute : les niveaux sont examinés de 1 à 5 et
    le premier niveau produisant un candidat gagne. Dans un niveau, on prend
    le plus ancien patient dont le saut respecte ``max_overtaken`` (borne
    INCLUSIVE) de sa règle — calculée sur les seules règles applicables du
    niveau pour cette activité : une règle hors créneau ou hors seuil ne peut
    plus annuler une règle active.
    """
    for level in range(1, 6):
        max_overtaken_by_activity = {}
        for rule in applicable_rules:
            if rule.priority_level != level:
                continue
            current = max_overtaken_by_activity.get(rule.activity_id)
            if current is None or rule.max_overtaken < current:
                max_overtaken_by_activity[rule.activity_id] = rule.max_overtaken
        if not max_overtaken_by_activity:
            continue

        app.logger.debug('level %s -> %s', level, max_overtaken_by_activity)
        # enumerate(candidates) = nombre de patients en avance dans la file
        # fournie (déjà triée) : c'est exactement ce que le saut ferait passer
        # derrière le candidat.
        for patients_ahead, patient in enumerate(candidates):
            limit = max_overtaken_by_activity.get(patient.activity_id)
            if limit is None:
                continue
            app.logger.debug('patient %s : %s devant, max %s',
                             patient, patients_ahead, limit)
            if patients_ahead <= limit:
                return patient
    return None


def algo_choice_next_patient(counter_id):

    counter = Counter.query.get(counter_id)

    # activités possible par ce pharmacien
    staff_activities = set(activity.id for activity in counter.staff.activities)

    # patients en attente que CE comptoir sait servir, en ordre FIFO
    # ((timestamp, id) : déterministe même à timestamps égaux)
    candidates = (Patient.query
                  .filter(Patient.status == 'standing',
                          Patient.activity_id.in_(staff_activities))
                  .order_by(Patient.timestamp, Patient.id)
                  .all())

    app.logger.debug('next_possible_patient %s', candidates)
    if not candidates:
        return None

    # permet de voir si un patient s'est fait doubler plus que le nombre prévu
    # Si oui on bloque l'algo le temps de rétablir l'équilibre (frein global :
    # le seuil est lu sur TOUTE la file, pas seulement les activités servies
    # par ce comptoir).
    is_patient_waiting_too_long = Patient.query.filter(
        and_(Patient.status == 'standing',
                Patient.overtaken >= app.config["ALGO_OVERTAKEN_LIMIT"])).first()
    app.logger.debug('is_patient_waiting_too_long %s', is_patient_waiting_too_long)

    # priorité à un type d'activité si un patient répond aux critères
    if app.config['ALGO_IS_ACTIVATED'] and not is_patient_waiting_too_long:
        number_of_patients = Patient.query.filter_by(status='standing').count()
        applicable_rules = get_applicable_algo_rules(number_of_patients)
        if applicable_rules:
            priority_patient = pick_priority_patient(candidates, applicable_rules)
            if priority_patient is not None:
                app.logger.debug('next_patient (prioritaire) %s', priority_patient)
                return priority_patient

    next_patient = candidates[0]
    app.logger.debug('next_patient %s', next_patient)
    return next_patient

def get_global_patient_queue(limit=None):
    """
    Simulates the patient selection algorithm to determine the order of waiting patients.
    Returns an ordered list of patients.

    Approximation connue : la simulation ne modélise pas les compétences des
    comptoirs (le filtre activités/membre de ``algo_choice_next_patient``) —
    le prochain patient réellement appelé peut donc différer du premier
    numéro affiché quand des activités ne sont pas servies. En revanche
    règles, seuils d'effectif, jours, créneaux et frein famine suivent
    exactement le moteur réel.

    ``limit`` borne le calcul : l'écran n'affiche que quelques numéros,
    ordonner toute la file pour n'en montrer que 5 est un gaspillage.
    ``None`` = file complète.

    Le tri de base est ``(timestamp, id)`` — déterministe même à timestamps
    égaux — posé en SQL à la récupération (index ix_patient_status_timestamp)
    puis préservé : la liste de travail ne subit que des retraits, aucun
    re-tri n'est nécessaire dans la boucle.
    """
    # 1. Fetch all standing patients, already in (timestamp, id) order
    waiting_patients = Patient.query.filter_by(status='standing').order_by(
        Patient.timestamp, Patient.id).all()
    ordered_queue = []

    algo_on = app.config['ALGO_IS_ACTIVATED']
    # Même instant pour toute la simulation : la file ne « traverse » pas
    # les créneaux horaires pendant le calcul.
    now = datetime.now(time_tz)
    overtaken_limit = app.config["ALGO_OVERTAKEN_LIMIT"]
    # Compteurs de dépassements simulés : chaque appel fictif doit faire
    # évoluer « overtaken » comme mark_overtaken_patients, sinon le frein
    # famine ne peut jamais se déclencher dans la prédiction alors qu'il le
    # ferait dans la file réelle.
    simulated_overtaken = {p.id: p.overtaken for p in waiting_patients}

    # Loop until all patients are ordered (or the display limit is reached)
    while waiting_patients and (limit is None or len(ordered_queue) < limit):
        selected_patient = None

        if algo_on:
            # Règles recalculées à chaque retrait : la plage
            # min_patients/max_patients porte sur l'effectif RESTANT —
            # une règle active à 4 patients s'éteint dès qu'il n'en reste
            # que 3 (ancien défaut : les règles étaient figées à l'effectif
            # initial, l'affichage divergeait après le premier appel).
            applicable_rules = get_applicable_algo_rules(len(waiting_patients), now)
            is_patient_waiting_too_long = any(
                simulated_overtaken[p.id] >= overtaken_limit
                for p in waiting_patients
            )

            if applicable_rules and not is_patient_waiting_too_long:
                selected_patient = pick_priority_patient(
                    waiting_patients, applicable_rules)

        # Fallback: if no patient selected by rules (or algo disabled), pick
        # the oldest — waiting_patients reste triée (timestamp, id), retraits
        # uniquement.
        if not selected_patient:
            selected_patient = waiting_patients[0]

        # Add to ordered list and remove from working set
        ordered_queue.append(selected_patient)
        index = waiting_patients.index(selected_patient)
        # Tous les patients plus anciens que cet appel fictif seraient
        # dépassés : +1 sur leurs compteurs simulés (borne globale — les
        # compétences des comptoirs ne sont pas modélisées).
        for skipped in waiting_patients[:index]:
            simulated_overtaken[skipped.id] += 1
        waiting_patients.pop(index)

    return ordered_queue


# Nombre de numéros « prochains patients » affichés à l'écran : 5 bornent le
# calcul et suffisent à informer le public.
NEXT_PATIENTS_DISPLAY_LIMIT = 5


def get_next_patients_call_numbers(limit=NEXT_PATIENTS_DISPLAY_LIMIT):
    """Numéros d'appel des prochains patients pour l'écran.

    Le résultat est mémorisé par révision de file : chaque mutation de la
    file incrémente le compteur ``QueueRevision`` (``communikation
    ("update_patient")`` → ``bump_queue_revision``), donc les requêtes HTMX
    répétées entre deux mutations ne relancent pas la simulation. La clé
    inclut aussi la minute courante : les règles ALGO sont horaires
    (``start_time``/``end_time``) et les compteurs ``overtaken`` évoluent
    sans révision — une clé à la seule révision figerait l'ordre entre deux
    mutations distantes.

    Le cache vit dans ``app.extensions`` : portée application — pas de fuite
    entre les apps des tests — et un seul calcul par révision et par
    process. Les numéros (chaînes) sont stockés, pas les ORM : pas
    d'instance détachée de session.
    """
    revision = get_queue_revision()
    key = (revision, datetime.now().replace(second=0, microsecond=0))
    cache = app.extensions.setdefault('gestionfile_announce', {})
    if cache.get('next_patients_key') == key:
        return cache['next_patients']
    numbers = [p.call_number for p in get_global_patient_queue(limit=limit)]
    cache['next_patients_key'] = key
    cache['next_patients'] = numbers
    return numbers

def mark_overtaken_patients(next_patient, counter_id):
    """Incrémente ``overtaken`` des patients réellement dépassés par cet appel.

    « Réellement dépassés » = encore ``standing``, plus anciens que le patient
    appelé (ordre ``(timestamp, id)``) ET d'une activité que CE comptoir sait
    servir : un patient d'une activité incompatible n'a pas été doublé — le
    comptoir ne pouvait de toute façon pas le prendre.

    Un seul UPDATE, un seul commit (l'ancienne version committait par patient
    et comptait TOUTE la file, y compris sans règle applicable et avant même
    la réclamation).
    """
    counter = db.session.get(Counter, counter_id)
    if counter is None or counter.staff is None:
        return
    staff_activities = [a.id for a in counter.staff.activities]
    if not staff_activities:
        return

    ahead = or_(
        Patient.timestamp < next_patient.timestamp,
        and_(Patient.timestamp == next_patient.timestamp,
             Patient.id < next_patient.id),
    )
    updated = (db.session.query(Patient)
               .filter(Patient.status == 'standing',
                       Patient.activity_id.in_(staff_activities),
                       ahead)
               .update({Patient.overtaken: Patient.overtaken + 1},
                       synchronize_session=False))
    db.session.commit()
    if updated:
        app.logger.debug('%s patient(s) dépassé(s) par l\'appel de %s (comptoir %s)',
                         updated, next_patient.id, counter_id)


def add_patient(call_number, activity, status='standing', print_job_id=None,
                journey_id=None):
    """ CRéation d'un nouveau patient et ajout à la BDD.

    status='standing' : patient immédiatement dans la file (scan, création
    directe). status='pending' : inscription en attente de confirmation
    d'impression (voir register_pending_patient / confirm_print).

    journey_id : UUID du parcours borne — clé d'unicité de l'inscription."""
    language_code = session.get('language_code', 'fr')
    language = Language.query.filter_by(code=language_code).first()
    # Vérifier que la langue existe, sinon utiliser une langue par défaut
    if not language:
        language = Language.query.filter_by(code='fr').first()

    # Création d'un nouvel objet Patient
    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        activity = activity,
        timestamp=datetime.now(time_tz),
        status=status,
        language_id=language.id,
        print_job_id=print_job_id,
        journey_id=journey_id
    )
    # Ajout à la base de données
    db.session.add(new_patient)
    db.session.commit()  # Enregistrement des changements dans la base de données

    return new_patient


# Durée de vie par défaut (secondes) d'une inscription 'pending' non confirmée.
# Au-delà, elle est considérée abandonnée (borne fermée, JS en échec, patient
# parti) et purgée. Elle n'a jamais rejoint la file, donc sa suppression ne
# nécessite aucune diffusion temps réel.
PENDING_PATIENT_TTL_SECONDS = 180


def expire_stale_pending_patients(ttl_seconds=None):
    """Expire les inscriptions 'pending' jamais confirmées et trop anciennes.

    Elles passent en statut 'expired' au lieu d'être supprimées : la borne
    conserve les acquittements en file locale et les retente — un résultat
    d'impression tardif (coupure réseau longue) doit pouvoir retrouver son
    inscription pour la réconcilier (voir /patient/confirm_print), au lieu
    d'un 410 qui laissait un patient avec ticket hors de la file.

    'expired' = résultat d'impression INCONNU (distinct de 'print_failed' :
    échec ou annulation explicite). La ligne reste consultable jusqu'à la
    purge de fin de journée. ``journey_id`` est libéré : le parcours
    abandonné ne doit pas retenir l'inscription d'un nouveau scan.

    Appelée paresseusement à chaque nouvelle inscription : robuste sans
    dépendre d'un ordonnanceur (fonctionne quel que soit le rôle du process)."""
    if ttl_seconds is None:
        ttl_seconds = app.config.get("PENDING_PATIENT_TTL_SECONDS", PENDING_PATIENT_TTL_SECONDS)
    cutoff = datetime.now(time_tz) - timedelta(seconds=ttl_seconds)
    stale = Patient.query.filter(
        Patient.status == 'pending',
        Patient.timestamp < cutoff
    ).all()
    for patient in stale:
        patient.status = 'expired'
        patient.journey_id = None
    if stale:
        db.session.commit()
        app.logger.debug(f"{len(stale)} inscription(s) pending expirée(s)")
    return len(stale)


def register_pending_patient(activity, print_job_id, journey_id=None):
    """ Crée une inscription EN ATTENTE (hors file) avant impression locale.

    Contrairement à register_patient, on n'appelle NI auto_calling NI
    communikation : le patient n'entre dans la file qu'après confirmation de
    l'impression (transition conditionnelle dans /patient/confirm_print).
    Cela évite d'ajouter un patient qui ne recevra jamais de ticket."""
    expire_stale_pending_patients()
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity, status='pending',
                              print_job_id=print_job_id, journey_id=journey_id)
    return new_patient


_SIMPLE_NUMBERING_SCOPE = "simple"


def _business_day():
    """Journée métier dans le fuseau de l'application — le même que celui
    des timestamps patients (``datetime.now(time_tz)``), donc cohérent avec
    les filtres ``db.func.date(Patient.timestamp)`` de l'amorçage."""
    return datetime.now(time_tz).date()


def _call_number_scope(activity):
    """Série du compteur : ``simple`` en numérotation globale, lettre de
    l'activité en numérotation par activité (les activités partageant une
    lettre partagent la même série — comportement inchangé)."""
    if app.config.get('NUMBERING_BY_ACTIVITY', False):
        return activity.letter
    return _SIMPLE_NUMBERING_SCOPE


def _format_call_number(scope, value):
    if scope == _SIMPLE_NUMBERING_SCOPE:
        return str(value)
    return f"{scope}-{value}"


def _seed_for_scope(scope, today):
    """Valeur d'amorçage du compteur du jour : plus grand numéro déjà
    attribué aujourd'hui dans cette série — la journée peut avoir commencé
    avant l'existence de la ligne de compteur (ou avant son introduction).
    On lit patient ET patient_history : un passage déjà archivé/purgé a
    quand même consommé son numéro."""
    todays = [row[0] for row in db.session.query(Patient.call_number).filter(
        db.func.date(Patient.timestamp) == today).all()]
    todays += [row[0] for row in db.session.query(PatientHistory.call_number).filter(
        db.func.date(PatientHistory.timestamp) == today).all()]
    if scope == _SIMPLE_NUMBERING_SCOPE:
        highest = 0
        for number in todays:
            text = str(number or "").strip()
            if text.isascii() and text.isdigit():
                highest = max(highest, int(text))
        return highest
    # Réutilise le cœur pur : "A-7" déjà attribué -> amorce à 7.
    allocated = next_category_call_number(scope, todays)
    return int(allocated.rsplit("-", 1)[1]) - 1


def _sequence_row(scope, today):
    # with_for_update : verrou de ligne InnoDB jusqu'au commit — deux
    # attributions concurrentes se sérialisent sur la ligne du compteur.
    # Sans effet sur SQLite (verrou posé à l'écriture), où les tests restent
    # séquentiels.
    return CallNumberSequence.query.filter_by(
        day=today, scope=scope).with_for_update().first()


def _allocate_sequence_value(scope, today):
    """Incrémente le compteur du jour et renvoie la valeur attribuée.

    La ligne est créée si besoin : la contrainte unique (day, scope)
    départage deux créations concurrentes — le perdant relit sous verrou au
    lieu d'écraser. Le verrou est tenu jusqu'au commit de la transaction
    appelante : compteur et patient sont validés ensemble, donc une
    inscription qui échoue ne consomme pas de numéro."""
    seq = _sequence_row(scope, today)
    if seq is None:
        seq = CallNumberSequence(day=today, scope=scope,
                                 value=_seed_for_scope(scope, today))
        try:
            with db.session.begin_nested():
                db.session.add(seq)
                db.session.flush()
        except IntegrityError:
            # Créée entre-temps par une requête concurrente : on la relit.
            seq = _sequence_row(scope, today)
            if seq is None:
                raise
    seq.value += 1
    db.session.flush()
    return seq.value


def peek_next_call_number(activity):
    """Numéro PRÉVISIONNEL pour le futur patient affiché : ce que la
    prochaine attribution rendrait, SANS consommer le compteur.

    Il peut différer du numéro réellement attribué si un autre parcours
    conclut entre-temps — seul le numéro du ticket / de l'inscription fait
    foi. Ne pas utiliser pour inscrire un patient."""
    scope = _call_number_scope(activity)
    today = _business_day()
    seq = CallNumberSequence.query.filter_by(day=today, scope=scope).first()
    value = seq.value if seq is not None else _seed_for_scope(scope, today)
    return _format_call_number(scope, value + 1)


def get_next_call_number(activity):
    """Attribue le numéro d'appel suivant — via le compteur persistant.

    Garanties par rapport à l'ancienne lecture « dernier patient + 1 » :
    pas de doublon entre inscriptions concurrentes (verrou de ligne) et pas
    de réattribution après suppression d'un patient (le compteur ne
    redescend jamais dans la journée)."""
    numbering_by_activity = app.config.get('NUMBERING_BY_ACTIVITY', False)
    if numbering_by_activity:
        call_number = get_next_category_number(activity)
    else:
        call_number = get_next_call_number_simple()
    app.logger.debug('call_number %s', call_number)
    return call_number


def get_next_call_number_simple():
    scope = _SIMPLE_NUMBERING_SCOPE
    return _format_call_number(scope, _allocate_sequence_value(scope, _business_day()))


# Générer le numéro d'appel en fonction de l'activité
def get_next_category_number(activity):
    # on utilise le code prévu de l'activité. Plusieurs activités peuvent avoir la même lettre
    scope = activity.letter
    return _format_call_number(scope, _allocate_sequence_value(scope, _business_day()))

def get_futur_patient(call_number, activity):
    """ CRéation d'un nouveau patient SANS ajout à la BDD
    Permet de simuler sa création pour pouvoir générer les infos utiles dans le QR Code"""

    language_code = session.get('language_code', 'fr')
    language = Language.query.filter_by(code=language_code).first()
    # Vérifier que la langue existe, sinon utiliser une langue par défaut
    if not language:
        language = Language.query.filter_by(code='fr').first()  

    new_patient = Patient(
        call_number= call_number,  # Vous devez définir cette fonction pour générer le numéro d'appel
        activity = activity,
        timestamp=datetime.now(time_tz),
        status='standing',
        language_id=language.id
    ) 
    return new_patient


def register_patient(activity, journey_id=None):
    call_number = get_next_call_number(activity)
    new_patient = add_patient(call_number, activity, journey_id=journey_id)

    from services.calling_service import run_auto_calling
    run_auto_calling()

    communikation("update_patient")
    return new_patient


def find_patient_by_journey(journey_id):
    """Patient déjà créé pour ce parcours borne (None si aucun / pas de
    journey). ``Patient.journey_id`` est unique : la lecture suffit."""
    if not journey_id:
        return None
    return Patient.query.filter_by(journey_id=journey_id).first()


def activity_accepting_registrations(activity, now=None):
    """L'activité accepte-t-elle une inscription À CET INSTANT ?

    Garde serveur contre les écrans et QR périmés : une page borne affichée
    avant la fermeture envoie encore ``is_active=True``, et un QR imprimé ou
    affiché peut être scanné après la fin des horaires. La disponibilité est
    donc recalculée au moment de l'inscription, jamais lue du formulaire.

    Deux conditions :

    - dans ses horaires (``is_open_at`` — la même règle que la page admin et
      le scheduler, mais recalculée en direct : un job de bascule raté ou un
      redémarrage ne laisse pas la porte ouverte) ;
    - et, si l'activité possède des boutons, au moins l'un encore proposé
      (``is_active`` ET ``is_present`` — couvre la désactivation manuelle
      admin comme le masquage « hors horaires » quand
      PAGE_PATIENT_DISABLE_BUTTON est off). Une activité sans aucun bouton
      (nominative/personnel, jamais offerte sur la borne) n'a pas d'état
      « fermé » à opposer : seul le planning la borne.
    """
    if activity is None:
        return False
    now = now or datetime.now(time_tz)
    if not is_open_at(activity.schedules,
                      ENGLISH_DAY_NAMES[now.weekday()], now.time()):
        return False
    buttons = activity.buttons
    return not buttons or any(
        button.is_active and button.is_present for button in buttons)


def register_journey_patient(activity, journey_id):
    """Crée le patient du parcours ``journey_id``, ou retrouve l'existant.

    Retourne ``(patient, created)``. Déduplication en deux niveaux : la
    lecture préalable évite l'insertion quand le parcours existe déjà, et la
    contrainte unique ``Patient.journey_id`` tranche si deux requêtes
    concurrentes passent la lecture en même temps (la perdante relit après
    IntegrityError au lieu de créer un doublon)."""
    patient = find_patient_by_journey(journey_id)
    if patient is not None:
        return patient, False
    try:
        return register_patient(activity, journey_id=journey_id), True
    except IntegrityError:
        db.session.rollback()
        patient = find_patient_by_journey(journey_id)
        if patient is None:
            raise
        return patient, False


def generate_audio_calling(counter_number, next_patient, language_code="fr"):

    # Si on ne veux pas de son, on quitte
    if not app.config["ANNOUNCE_SOUND"]:
        return
    
    # Texte pour la synthèse vocale
    # patient FR ou que langue FR
    if language_code == "fr" or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        text_template = app.config["ANNOUNCE_CALL_SOUND"]
    # si pas FR
    else:
        # si langue desactivée
        if not next_patient.language.voice_is_active:
            text_template = app.config["ANNOUNCE_CALL_SOUND"]
        # si langue activée
        else :
            translated_template = get_text_translation("announce_call_sound", next_patient.language.code)
            if translated_template["error"]:
                # Traduction absente/vide : get_text_translation renvoie le texte
                # FR, la voix doit donc repasser en FR elle aussi.
                language_code = "fr"
            text_template = translated_template["translation"]

    text = replace_balise_announces(text_template, next_patient)

    return choose_voice_model(next_patient, text, language_code)
    
def choose_voice_model(next_patient, text, language_code):
    if language_code == "fr" or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        voice_model = app.config["VOICE_MODEL"]
    else:
        if not next_patient.language.voice_is_active:
            voice_model = app.config["VOICE_MODEL"]
        else:
            voice_model = next_patient.language.voice_model

    if voice_model == "google":
        try:
            return create_google_tts_sound(next_patient, text, language_code)
        except Exception as exc:
            # Clé absente/refusée, voix non choisie, réseau coupé… : l'annonce
            # ne doit pas devenir muette pour autant — repli sur gTTS, promis
            # dans l'onglet Audio.
            app.logger.warning(
                "Synthèse Google impossible (%s) : repli sur gTTS.", exc)
    # gTTS : choix explicite, repli, ou modèle jamais réglé (langue ajoutée
    # après coup, voice_model NULL) — auparavant aucune annonce n'était jouée.
    return create_tts_sound(next_patient, text, language_code)

def create_tts_sound(next_patient, text, language_code):
    app.logger.debug('create_tts_sound %s %s', text, app.config["VOICE_GTTS_NAME"])

    if language_code == "fr"or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        voice_gtts_name = app.config["VOICE_GTTS_NAME"]
    else:
        if not next_patient.language.voice_is_active:
            voice_gtts_name = app.config["VOICE_GTTS_NAME"]
        else:
            voice_gtts_name = next_patient.language.voice_gtts_name
    # Voix gTTS jamais choisie : le code langue est un code gTTS valide dans
    # la plupart des cas (fr, en, de, ar…).
    voice_gtts_name = voice_gtts_name or language_code or "fr"

    def _write_audio(path):
        gTTS(text, lang=voice_gtts_name, timeout=TTS_TIMEOUT_SECONDS).save(path)

    return cached_announcement_url(
        text=text,
        provider="gtts",
        voice=voice_gtts_name,
        language=language_code,
        writer=_write_audio,
    )


def create_google_tts_sound(next_patient, text, language_code):
    
    # Récupérer la voix sélectionnée depuis la base de données (ou config)
    if language_code == "fr" or app.config["ANNOUNCE_CALL_TRANSLATION"] == "fr":
        voice_google_name = app.config["VOICE_GOOGLE_NAME"]
        voice_google_region = app.config["VOICE_GOOGLE_REGION"]
    else:
        if not next_patient.language.voice_is_active:
            voice_google_name = app.config["VOICE_GOOGLE_NAME"]
            voice_google_region = app.config["VOICE_GOOGLE_REGION"]
        else:
            voice_google_name = next_patient.language.voice_google_name
            voice_google_region = next_patient.language.voice_google_region

    if not voice_google_name:
        # Évite un appel réseau voué à l'échec : choose_voice_model repasse
        # alors sur gTTS.
        raise RuntimeError("Aucune voix Google choisie pour cette langue.")

    def _write_audio(path):
        credentials_json = get_google_credentials()
        if not credentials_json:
            raise RuntimeError("Clé Google Cloud non configurée.")

        try:
            credentials_info = json.loads(credentials_json.decode("utf-8"))
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info
            )
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError("Clé Google Cloud invalide.") from exc

        client = texttospeech.TextToSpeechClient(credentials=credentials)
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=text),
            voice=texttospeech.VoiceSelectionParams(
                name=voice_google_name,
                language_code=voice_google_region,
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            ),
            timeout=TTS_TIMEOUT_SECONDS,
        )
        with open(path, "wb") as out:
            out.write(response.audio_content)

    return cached_announcement_url(
        text=text,
        provider="google",
        voice=voice_google_name,
        language=language_code,
        voice_region=voice_google_region,
        writer=_write_audio,
    )


def qr_code_data_uri(patient, journey_id=None):
    """QR code de la borne rendu en ``data:image/png;base64,...``.

    Le PNG est généré en mémoire et embarqué dans la page : aucun fichier
    n'est écrit dans ``static/qr_patients``, donc pas de collision de nom
    (un call_number est réutilisé d'un jour à l'autre), pas de QR périmé
    servi depuis le cache navigateur et pas de dossier à nettoyer. La CSP
    autorise déjà ``img-src data:``.
    """
    app.logger.debug("qr_code_data_uri")
    app.logger.debug('%s %s %s %s', patient, patient.id, patient.call_number, patient.activity)

    language_code = session.get('language_code', "fr")

    if app.config['PAGE_PATIENT_QRCODE_WEB_PAGE']:
        if "SERVER_URL" not in app.config:
            set_server_url(app, request)
        data = f"{app.config['SERVER_URL']}/patient/phone/{language_code}/{patient.call_number}/{patient.activity.id}"
        if patient.id:
            # QR de CONCLUSION (patient déjà enregistré) : lien de suivi signé
            # vers CE passage — le scanner ne doit pas créer d'inscription.
            data += f"?ticket={make_patient_phone_token(patient.id, patient.call_number)}"
            if journey_id:
                data += f"&journey={journey_id}"
        elif journey_id:
            # QR de validation (futur patient) : l'UUID du parcours voyage dans
            # l'URL scannée ; /patient/phone/ping le renvoie et émet
            # update_scan_phone dans la salle scan_<uuid> de la borne.
            data += f"?journey={journey_id}"
    else :
        if session.get('language_code') != "fr":
            language_code = session.get('language_code')
            template = get_text_translation("page_patient_qrcode_data", language_code)["translation"]
            if app.config["PAGE_PATIENT_QRCODE_DISPLAY_SPECIFIC_MESSAGE"]:
                template = template + "\n" + get_activity_message_translation(patient.activity, language_code)
        else:
            template = app.config['PAGE_PATIENT_QRCODE_DATA']
            if app.config["PAGE_PATIENT_QRCODE_DISPLAY_SPECIFIC_MESSAGE"]:
                template = template + "\n" + patient.activity.specific_message
        data = replace_balise_phone(template, patient)

    # Générer le QR Code en mémoire. qrcode utilise Pillow si présent
    # (save(buffer, format=...)), sinon PyPNGImage (save(buffer) — le PNG
    # est son seul format) : on tolère les deux signatures.
    img = qrcode.make(data)
    buffer = io.BytesIO()
    try:
        img.save(buffer, format='PNG')
    except TypeError:
        img.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
    return f"data:image/png;base64,{encoded}"


def set_server_url(app, request):
    # Stockage de l'adresse pour la génération du QR code
    if request.host_url == "http://127.0.0.1:5000/":
        server_url = app.config.get('NETWORK_ADRESS')
    else:
        server_url = request.host_url
    app.config['SERVER_URL'] = server_url


def get_google_credentials():
    config_option = ConfigOption.query.filter_by(config_key='voice_google_key').first()
    if not config_option or not config_option.value_json:
        return None
    base32_key = app.config.get("BASE32_KEY")
    if not base32_key:
        app.logger.warning(
            "BASE32_KEY non configurée : la clé Google stockée ne peut pas être déchiffrée.")
        return None
    try:
        cipher_suite = Fernet(base32_key)
        # Déchiffrer le contenu de la clé JSON
        return cipher_suite.decrypt(config_option.value_json.encode('utf-8'))
    except (ValueError, InvalidToken) as e:
        app.logger.error("Déchiffrement de la clé Google impossible : %s", e)
        return None


def counter_become_inactive(counter_id):
    app.logger.debug("counter_become_inactiv")
    counter = db.session.query(Counter).filter(Counter.id == counter_id).first()
    counter.is_active = False
    db.session.commit()


def counter_become_active(counter_id):
    app.logger.debug("counter_become_activ")
    counter = db.session.query(Counter).filter(Counter.id == counter_id).first()
    app.logger.debug('%s %s', counter, counter.is_active)
    if not counter.is_active:
        app.logger.debug('change')
        counter.is_active = True
        db.session.commit()
