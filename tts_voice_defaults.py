"""Choix automatique d'une voix Google Cloud pour une langue (noyau pur).

Sert au bouton « Utiliser Google pour toutes les langues » de l'onglet Audio :
une langue sans voix Google choisie en reçoit une par défaut, pour que le
passage à Google ne demande pas de régler chaque langue à la main.

Aucune dépendance Flask/Google : ``voices`` est la liste de dictionnaires
produite par ``routes.admin_announce._fetch_google_voices``.
"""

from __future__ import annotations

# Ordre de préférence : les voix les plus naturelles d'abord. Les familles
# « premium » (Studio, Journey, Chirp…) sont volontairement exclues : quota
# gratuit plus faible et tarif plus élevé — l'utilisateur peut toujours les
# choisir à la main.
PREFERRED_VOICE_TYPES = ("Neural2", "Wavenet", "Standard")

# Code « canonique » quand la région ne se déduit pas du code langue
# (fr → fr-FR fonctionne, mais en → en-EN n'existe pas).
_CANONICAL_REGIONS = {
    "en": "en-GB",
    "ar": "ar-XA",
    "zh": "cmn-CN",
    "pt": "pt-PT",
    "uk": "uk-UA",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "hi": "hi-IN",
    "vi": "vi-VN",
    "sv": "sv-SE",
    "da": "da-DK",
    "cs": "cs-CZ",
    "el": "el-GR",
    "he": "he-IL",
    "fa": "fa-IR",
    "no": "nb-NO",
    "nb": "nb-NO",
}

# Codes Google dont le préfixe diffère du code ISO de la langue.
_GOOGLE_PREFIX_ALIASES = {
    "zh": ("cmn", "yue", "zh"),
    "no": ("nb", "no"),
    "tl": ("fil", "tl"),
    "he": ("he", "iw"),
}


def voice_type(name: str) -> str | None:
    """Famille de la voix d'après son nom (« fr-FR-Neural2-A » → « Neural2 »)."""
    lowered = (name or "").lower()
    for kind in PREFERRED_VOICE_TYPES:
        if f"-{kind.lower()}-" in lowered:
            return kind
    return None


def _prefixes(language_code: str) -> tuple[str, ...]:
    code = (language_code or "").lower()
    return _GOOGLE_PREFIX_ALIASES.get(code, (code,))


def _matching_region(voice: dict, prefixes: tuple[str, ...]) -> str | None:
    for region in voice.get("language_codes") or ():
        head = region.lower().split("-", 1)[0]
        if head in prefixes:
            return region
    return None


def pick_default_google_voice(voices, language_code):
    """Retourne ``(nom, région)`` de la voix conseillée, ou ``None``.

    Critères, dans l'ordre : famille préférée (Neural2 > Wavenet > Standard),
    région canonique de la langue (fr-FR plutôt que fr-CA), puis nom trié —
    le choix est stable d'un appel à l'autre.
    """
    code = (language_code or "").lower()
    if not code:
        return None
    prefixes = _prefixes(code)
    canonical = _CANONICAL_REGIONS.get(code, f"{code}-{code.upper()}").lower()

    candidates = []
    for voice in voices or ():
        name = voice.get("name") or ""
        kind = voice_type(name)
        if kind is None:
            continue
        region = _matching_region(voice, prefixes)
        if region is None:
            continue
        candidates.append((
            PREFERRED_VOICE_TYPES.index(kind),
            0 if region.lower() == canonical else 1,
            name,
            region,
        ))

    if not candidates:
        return None
    _, _, name, region = min(candidates)
    return name, region
