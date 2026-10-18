"""Fiabilite du temps reel pendant un test de charge (noyau pur).

Le bilan des donnees (``stress_reconciliation``) verifie que les ecritures
confirmees sont en base. Ce module verifie que les *evenements temps reel*
correspondants arrivent bien aux clients connectes, et repond a la question
« le relais RabbitMQ est-il utile ? ».

Pendant un test en preproduction, deux sources emettent des sondes sur le
namespace dedie ``/socket_stress`` par le meme chemin que les vraies mises a
jour (``communication_websocket``) :

* le **serveur web**, a chaque action de parcours confirmee (creation, appel,
  prise en charge, fin). L'emission precede la reponse HTTP : toute action
  acquittee par le serveur doit donc produire une sonde ;
* le **planificateur** (processus ``scheduler``), une sonde numerotee par
  seconde tant que le test tourne. Il compte ses emissions en base. Sans
  relais (RabbitMQ), un planificateur dans un processus separe ne peut joindre
  aucun client : ses sondes sont perdues a 100 %, quelle que soit la charge.

Quelques ecouteurs Socket.IO du runner restent connectes pendant tout le test
et notent ce qu'ils recoivent (``ListenerRecord``). ``analyze_realtime``
compare ensuite attendu et recu, ecouteur par ecouteur.

Les delais sont calcules entre l'horloge du serveur (``emitted_at``) et celle
du runner : fiables quand les deux tournent sur le meme hote (Docker), a lire
comme un ordre de grandeur sinon.
"""
from __future__ import annotations

import math
import time

PROBE_NAMESPACE = '/socket_stress'
PROBE_STREAM = PROBE_NAMESPACE.lstrip('/')
PROBE_EVENT = 'probe'
# Scenarios dont le runner ecoute le temps reel. « realtime » n'a pas de
# parcours : seules les sondes du planificateur y sont mesurees.
PROBE_SCENARIOS = frozenset({'kiosk', 'queue_counter', 'mixed', 'realtime'})
JOURNEY_ACTIONS = frozenset({'create', 'call', 'serve', 'complete'})
ORIGINS = frozenset({'web', 'scheduler'})


def build_probe(run_uuid, origin, *, patient_id=None, action=None, seq=None,
                now=None):
    """Charge utile d'une sonde : identifiants techniques uniquement."""
    probe = {'run': run_uuid, 'origin': origin,
             'emitted_at': time.time() if now is None else now}
    if origin == 'web':
        probe.update(patient_id=patient_id, action=action)
    else:
        probe['seq'] = seq
    return probe


class ListenerRecord:
    """Ce qu'un ecouteur a recu pendant le test (rempli par ses callbacks)."""

    def __init__(self, index=0):
        self.index = index
        self.connected = False
        self.connect_error = None
        self.disconnects = 0
        self.closing = False
        self.web: dict[tuple[int, str], int] = {}
        self.scheduler: dict[int, int] = {}
        self.latencies_ms: list[float] = []

    def on_connect(self):
        self.connected = True

    def on_disconnect(self, *_reason):
        # La fermeture volontaire en fin de test n'est pas une coupure. Les
        # versions recentes de python-socketio passent un motif : ignore.
        if not self.closing:
            self.disconnects += 1

    def on_probe(self, payload, run_uuid, received_at=None):
        """Enregistre une sonde ; ignore silencieusement tout le reste.

        ``payload`` est l'enveloppe ``{flag, data, revision}`` diffusee par
        ``communication_websocket``.
        """
        probe = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(probe, dict) or probe.get('run') != run_uuid:
            return False
        origin = probe.get('origin')
        try:
            if origin == 'web':
                if probe.get('action') not in JOURNEY_ACTIONS:
                    return False
                key = (int(probe['patient_id']), probe['action'])
                self.web[key] = self.web.get(key, 0) + 1
            elif origin == 'scheduler':
                seq = int(probe['seq'])
                self.scheduler[seq] = self.scheduler.get(seq, 0) + 1
            else:
                return False
        except (KeyError, TypeError, ValueError):
            return False
        emitted_at = probe.get('emitted_at')
        if isinstance(emitted_at, (int, float)):
            received_at = time.time() if received_at is None else received_at
            self.latencies_ms.append(max(0.0, (received_at - emitted_at) * 1000))
        return True


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return round(ordered[index], 1)


def _scheduler_status(sent, received_counts):
    if sent is None:
        return 'not_measured'
    if sent == 0:
        return 'silent'
    if received_counts and all(count == 0 for count in received_counts):
        return 'unreachable'
    if any(count < sent for count in received_counts):
        return 'partial'
    return 'ok'


def analyze_realtime(expected_web, listeners, *, scheduler_sent=None,
                     relay=None):
    """Compare les sondes attendues a celles recues par chaque ecouteur.

    ``expected_web`` : ensemble ``{(patient_id, action)}`` des actions
    confirmees par le serveur. ``scheduler_sent`` : nombre de sondes que le
    planificateur declare avoir emises (``None`` si non mesure) ; seules les
    sondes ``seq <= scheduler_sent`` sont comptees. ``relay`` : relais
    Socket.IO du serveur web (``rabbitmq`` / ``none``), pour le rapport.
    """
    expected_web = {(int(pid), action) for pid, action in (expected_web or ())}
    connected = [record for record in listeners if record.connected]
    web_lost = duplicates = unexpected = worst = 0
    scheduler_counts, latencies = [], []
    for record in connected:
        received = set(record.web)
        missed = len(expected_web - received)
        web_lost += missed
        worst = max(worst, missed)
        duplicates += sum(count - 1 for count in record.web.values() if count > 1)
        unexpected += len(received - expected_web)
        if scheduler_sent is not None:
            scheduler_counts.append(sum(
                1 for seq in record.scheduler if 1 <= seq <= scheduler_sent))
        latencies.extend(record.latencies_ms)

    deliveries = len(expected_web) * len(connected)
    scheduler = {
        'sent': scheduler_sent,
        'status': _scheduler_status(scheduler_sent, scheduler_counts),
        'received_min': min(scheduler_counts) if scheduler_counts else None,
        'received_max': max(scheduler_counts) if scheduler_counts else None,
        'lost': (sum(scheduler_sent - count for count in scheduler_counts)
                 if scheduler_sent else 0),
    }
    if not connected:
        status = 'unavailable'
    elif web_lost or scheduler['status'] in {'unreachable', 'partial'}:
        status = 'lost'
    elif scheduler['status'] == 'silent':
        status = 'warning'
    else:
        status = 'ok'
    return {
        'status': status,
        'relay': relay,
        'listeners': len(listeners),
        'listeners_connected': len(connected),
        'connect_errors': [record.connect_error for record in listeners
                           if record.connect_error][:3],
        'disconnects': sum(record.disconnects for record in connected),
        'web': {
            'expected': len(expected_web),
            'deliveries_expected': deliveries,
            'lost': web_lost,
            'loss_percent': (web_lost / deliveries * 100) if deliveries else 0,
            'worst_listener_lost': worst,
            'duplicates': duplicates,
            'unexpected': unexpected,
        },
        'scheduler': scheduler,
        'latency_ms': {
            'p50': _percentile(latencies, .50),
            'p95': _percentile(latencies, .95),
            'max': round(max(latencies), 1) if latencies else None,
            'samples': len(latencies),
        },
    }


def _fr(value, digits=1):
    return f'{value:.{digits}f}'.replace('.', ',')


def realtime_message(result):
    """Phrase affichable et ton Bootstrap (success/warning/danger)."""
    if not result:
        return None, None
    status = result.get('status')
    if status == 'unavailable':
        return 'warning', (
            "Le temps réel n'a pas pu être écouté : aucun écouteur ne s'est "
            'connecté au serveur pendant le test.')
    web = result.get('web') or {}
    scheduler = result.get('scheduler') or {}
    relay = result.get('relay')
    listeners = result.get('listeners_connected') or 0
    parts = []
    if web.get('lost'):
        parts.append(
            f"{web['lost']} réception(s) temps réel manquée(s) sur "
            f"{web.get('deliveries_expected', 0)} attendue(s) "
            f"({_fr(web.get('loss_percent') or 0)} %), "
            f"{result.get('disconnects', 0)} coupure(s) d'écouteur.")
    if scheduler.get('status') == 'unreachable':
        if relay == 'rabbitmq':
            parts.append(
                f"Les {scheduler['sent']} événements du planificateur ne sont "
                'jamais arrivés malgré RabbitMQ : vérifiez que le planificateur '
                "utilise le même relais (redémarrage requis après l'activation).")
        else:
            parts.append(
                f"Les {scheduler['sent']} événements du planificateur ne sont "
                'jamais arrivés : sans relais RabbitMQ, ce processus séparé ne '
                'peut joindre ni les écrans ni les comptoirs. Activez RabbitMQ.')
    elif scheduler.get('status') == 'partial':
        parts.append(
            f"{scheduler.get('lost', 0)} événement(s) du planificateur perdu(s) "
            f"sur {scheduler['sent']} émis par écouteur.")
    if parts:
        return 'danger', ' '.join(parts)

    latency = (result.get('latency_ms') or {}).get('p95')
    delay = f' ; délai p95 {_fr(latency, 0)} ms' if latency is not None else ''
    sources = []
    if web.get('expected'):
        sources.append(f"{web['expected']} du serveur web")
    if scheduler.get('sent'):
        sources.append(f"{scheduler['sent']} du planificateur")
    detail = f" ({', '.join(sources)})" if sources else ''
    text = (f'Tous les événements temps réel{detail} sont arrivés aux '
            f'{listeners} écouteurs{delay}.')
    if scheduler.get('status') == 'silent':
        return 'warning', (
            f"{text} Le planificateur n'a émis aucune sonde : processus "
            'absent ou arrêté, son relais n’a pas pu être vérifié.')
    return 'success', text
