"""
Uniforme LLM-tekstlaag voor briefing-extractie, clipselectie, compliance en
captions. Groq is de standaard provider (gebruikt de bestaande GROQ_API_KEY,
geen betaalde Anthropic API nodig). Anthropic is optioneel en kan expliciet
gekozen worden via settings.llm_provider == "anthropic".

Belangrijk: als ANTHROPIC_API_KEY ontbreekt terwijl "anthropic" gekozen is,
crasht de app NOOIT. resolve_provider() schakelt dan automatisch terug naar
Groq en geeft een duidelijke reden terug die job_manager in de technische
beperkingen van het eindrapport zet.

GROQ-MODEL-RESOLUTIE (structurele fix voor "model_not_found"):
Er wordt NOOIT blind aangenomen dat een hardcoded model bestaat of
toegankelijk is met deze API-key. In plaats daarvan wordt bij de eerste
Groq-aanroep (en optioneel bij startup, zie /api/health) GET
https://api.groq.com/openai/v1/models opgevraagd, en wordt automatisch het
beste beschikbare model gekozen uit een voorkeurslijst. Is geen enkel
voorkeursmodel beschikbaar, dan wordt het eerste geschikte model uit de
volledige lijst gebruikt. Is er geen enkel geschikt model beschikbaar, dan
komt er een duidelijke foutmelding met alle model-ID's die de key wél mag
gebruiken -- de app crasht hier nooit hard op.
"""
import threading
import time
from typing import Optional

import requests

from app.config import ANTHROPIC_API_KEY, GROQ_API_KEY, CLAUDE_MODEL, GROQ_LLM_MODEL

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"

# Voorkeursvolgorde voor tekst-taken (briefing-extractie, clipselectie,
# compliance, captions) -- deze lijst is gebaseerd op een echte test tegen
# GET /openai/v1/models met de GROQ_API_KEY van de gebruiker (HTTP 200,
# bevestigd geldig account), niet blind aangenomen. Elk model hieronder
# ondersteunt tekst chat-completions + JSON mode.
PREFERRED_GROQ_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3-32b",
    "groq/compound-mini",
    "groq/compound",
]

# Modellen die wel in de lijst kunnen staan maar NIET geschikt zijn voor onze
# chat-completion/JSON-taken (audio-transcriptie, TTS, moderatie/guardrails).
# "compound"/"compound-mini" staan hier NIET meer bij: die zijn expliciet
# door de gebruiker geverifieerd als bruikbaar (tekst in/uit + JSON mode).
_UNSUITABLE_SUBSTRINGS = (
    "whisper", "tts", "guard", "moderation", "playai", "distil-whisper",
)


class LLMError(RuntimeError):
    pass


def _is_suitable_text_model(model_id: str) -> bool:
    low = model_id.lower()
    return not any(bad in low for bad in _UNSUITABLE_SUBSTRINGS)


# ---------------------------------------------------------------------------
# Model-lijst ophalen en cachen (proces-breed, met korte TTL zodat een
# herstart van de app of een lange sessie automatisch opnieuw controleert).
# ---------------------------------------------------------------------------
_models_cache_lock = threading.Lock()
_models_cache: dict = {"fetched_at": 0.0, "raw_ids": None, "suitable_ids": None, "error": None}
_MODELS_CACHE_TTL_SECONDS = 300  # 5 minuten

_resolved_model_lock = threading.Lock()
_resolved_model_cache: dict = {"model_id": None, "note": None, "fetched_at": 0.0}


def fetch_available_groq_models(force: bool = False) -> tuple[list[str], list[str], Optional[str]]:
    """
    Haalt de beschikbare Groq-modellen op via GET /openai/v1/models.
    Retourneert (alle_model_ids, geschikte_model_ids, foutmelding_of_None).
    Gecachet (TTL 5 min) zodat niet elke LLM-call opnieuw dit endpoint raakt.
    Faalt de aanroep (netwerk, ongeldige key, etc.), dan wordt dat als
    foutmelding teruggegeven -- er wordt hier NOOIT gecrasht.
    """
    with _models_cache_lock:
        age = time.time() - _models_cache["fetched_at"]
        if not force and _models_cache["raw_ids"] is not None and age < _MODELS_CACHE_TTL_SECONDS:
            return _models_cache["raw_ids"], _models_cache["suitable_ids"], _models_cache["error"]

    if not GROQ_API_KEY:
        err = "GROQ_API_KEY ontbreekt -- kan de beschikbare modellen niet ophalen."
        with _models_cache_lock:
            _models_cache.update(fetched_at=time.time(), raw_ids=[], suitable_ids=[], error=err)
        return [], [], err

    try:
        resp = requests.get(
            GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("data", [])
        # "active" staat niet in elke API-versie -- als het veld ontbreekt,
        # gaan we ervan uit dat het model actief is (geen valse negatives).
        raw_ids = [e["id"] for e in entries if e.get("active", True)]
        suitable_ids = [m for m in raw_ids if _is_suitable_text_model(m)]
        with _models_cache_lock:
            _models_cache.update(fetched_at=time.time(), raw_ids=raw_ids, suitable_ids=suitable_ids, error=None)
        return raw_ids, suitable_ids, None
    except Exception as e:
        err = f"Kon Groq-modellenlijst niet ophalen ({GROQ_MODELS_URL}): {e}"
        with _models_cache_lock:
            # Bij een falende ververs-poging de vorige geslaagde cache niet weggooien
            # als die er is -- anders staat de app plots zonder enige modelkennis.
            if _models_cache["raw_ids"] is None:
                _models_cache.update(fetched_at=time.time(), raw_ids=[], suitable_ids=[], error=err)
            else:
                _models_cache["error"] = err
        return _models_cache["raw_ids"] or [], _models_cache["suitable_ids"] or [], err


def resolve_groq_model(force: bool = False) -> tuple[Optional[str], str]:
    """
    Kiest automatisch het beste beschikbare Groq-tekstmodel.
    Retourneert (model_id_of_None, toelichting).

    Volgorde:
    1. Als GROQ_LLM_MODEL in .env is gezet EN daadwerkelijk beschikbaar is
       voor deze key -> gebruik die (expliciete overschrijving door de gebruiker).
    2. Anders: eerste beschikbare model uit PREFERRED_GROQ_MODELS.
    3. Anders: eerste andere geschikte model uit de volledige lijst.
    4. Anders: None, met een duidelijke foutmelding + alle beschikbare IDs.
    """
    with _resolved_model_lock:
        age = time.time() - _resolved_model_cache["fetched_at"]
        if not force and _resolved_model_cache["model_id"] and age < _MODELS_CACHE_TTL_SECONDS:
            return _resolved_model_cache["model_id"], _resolved_model_cache["note"]

    raw_ids, suitable_ids, fetch_error = fetch_available_groq_models(force=force)

    if fetch_error and not suitable_ids:
        note = fetch_error
        with _resolved_model_lock:
            _resolved_model_cache.update(model_id=None, note=note, fetched_at=time.time())
        return None, note

    if not suitable_ids:
        note = (
            "Geen enkel geschikt tekstmodel beschikbaar voor deze GROQ_API_KEY. "
            f"Beschikbare model-ID's: {raw_ids or '(geen)'}"
        )
        with _resolved_model_lock:
            _resolved_model_cache.update(model_id=None, note=note, fetched_at=time.time())
        return None, note

    # 1. Expliciete override via .env, maar alleen als hij echt beschikbaar is.
    if GROQ_LLM_MODEL and GROQ_LLM_MODEL in suitable_ids:
        model_id = GROQ_LLM_MODEL
        note = f"GROQ_LLM_MODEL='{model_id}' uit .env, beschikbaar bevestigd."
        with _resolved_model_lock:
            _resolved_model_cache.update(model_id=model_id, note=note, fetched_at=time.time())
        return model_id, note

    # 2. Voorkeurslijst, in volgorde.
    for candidate in PREFERRED_GROQ_MODELS:
        if candidate in suitable_ids:
            note = f"Automatisch gekozen uit voorkeurslijst: '{candidate}'."
            if GROQ_LLM_MODEL and GROQ_LLM_MODEL != candidate:
                note += (
                    f" (GROQ_LLM_MODEL='{GROQ_LLM_MODEL}' in .env was niet beschikbaar "
                    f"voor deze key, dus overgeslagen.)"
                )
            with _resolved_model_lock:
                _resolved_model_cache.update(model_id=candidate, note=note, fetched_at=time.time())
            return candidate, note

    # 3. Geen enkel voorkeursmodel beschikbaar -> eerste geschikte model uit de lijst.
    fallback = suitable_ids[0]
    note = (
        f"Geen van de voorkeursmodellen ({', '.join(PREFERRED_GROQ_MODELS)}) is beschikbaar "
        f"voor deze GROQ_API_KEY. Automatisch teruggevallen op het eerste geschikte "
        f"beschikbare model: '{fallback}'."
    )
    with _resolved_model_lock:
        _resolved_model_cache.update(model_id=fallback, note=note, fetched_at=time.time())
    return fallback, note


def groq_health() -> dict:
    """Voor GET /api/health -- toont welk model daadwerkelijk gebruikt zou worden."""
    model_id, note = resolve_groq_model()
    return {
        "llm_provider": "groq",
        "llm_model": model_id,
        "available": model_id is not None,
        "detail": note,
    }


def resolve_provider(requested: Optional[str]) -> tuple[str, Optional[str]]:
    """
    Bepaalt welke provider daadwerkelijk gebruikt wordt.
    Retourneert (provider, fallback_reason). fallback_reason is None tenzij
    er automatisch is teruggeschakeld naar Groq.
    """
    requested = (requested or "groq").strip().lower()
    if requested not in ("groq", "anthropic"):
        requested = "groq"

    if requested == "anthropic":
        if ANTHROPIC_API_KEY:
            return "anthropic", None
        return "groq", (
            "Provider 'anthropic' was gekozen in de instellingen, maar ANTHROPIC_API_KEY "
            "ontbreekt -- automatisch teruggeschakeld naar Groq (de standaard provider)."
        )

    return "groq", None


def _groq_chat_call(model_id: str, system: str, user: str, max_tokens: int, json_mode: bool):
    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    # GPT-OSS kan een aanzienlijk deel van de completion-begroting aan
    # reasoning besteden. Low reasoning + compacte JSON voorkomt dat de
    # validator wordt bereikt nadat alle completion-tokens al op zijn.
    if model_id.startswith("openai/gpt-oss-"):
        kwargs["reasoning_effort"] = "low"
    else:
        kwargs["temperature"] = 0.2
    return client.chat.completions.create(
        model=model_id,
        max_completion_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **kwargs,
    )


def complete(system: str, user: str, provider: str = "groq",
             max_tokens: int = 4000, json_mode: bool = False) -> tuple[str, Optional[str]]:
    """
    Stuurt één completion-request naar de gekozen provider en geeft
    (tekstoutput, model_id_gebruikt) terug. model_id_gebruikt is None voor
    Anthropic (dat gebruikt altijd CLAUDE_MODEL, geen resolver nodig -- de
    Anthropic-modellijst is stabiel en expliciet door de gebruiker gekozen).

    Gooit een duidelijke LLMError als de benodigde key ontbreekt of geen
    geschikt model beschikbaar is -- de aanroepende pipeline-stap vangt dit
    af, de app crasht hier nooit hard op.
    """
    provider = (provider or "groq").strip().lower()

    if provider == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise LLMError("ANTHROPIC_API_KEY ontbreekt -- kan geen Anthropic-call uitvoeren.")
        from anthropic import Anthropic
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text, CLAUDE_MODEL

    if provider == "groq":
        if not GROQ_API_KEY:
            raise LLMError("GROQ_API_KEY ontbreekt -- kan geen Groq-call uitvoeren.")

        model_id, note = resolve_groq_model()
        if not model_id:
            raise LLMError(f"Geen bruikbaar Groq-model gevonden. {note}")

        try:
            resp = _groq_chat_call(model_id, system, user, max_tokens, json_mode)
        except Exception as e:
            # Structurele bescherming tegen precies dit soort fouten: als het
            # gekozen model tóch niet blijkt te werken (bijv. net uitgefaseerd
            # ná onze laatste cache-refresh), forceer een verse modellenlijst
            # en probeer één keer opnieuw met het dan opnieuw gekozen model --
            # in plaats van hard te crashen op "model_not_found".
            err_text = str(e).lower()
            looks_like_model_issue = (
                "model_not_found" in err_text or "does not exist" in err_text
                or "404" in err_text or "decommissioned" in err_text
            )
            if not looks_like_model_issue:
                raise LLMError(f"Groq-aanroep mislukt (model '{model_id}'): {e}")

            retry_model_id, retry_note = resolve_groq_model(force=True)
            if not retry_model_id or retry_model_id == model_id:
                raise LLMError(
                    f"Groq-model '{model_id}' bleek niet bruikbaar en er is geen ander "
                    f"geschikt model beschikbaar. Oorspronkelijke fout: {e}"
                )
            try:
                resp = _groq_chat_call(retry_model_id, system, user, max_tokens, json_mode)
                model_id = retry_model_id
            except Exception as e2:
                raise LLMError(
                    f"Groq-model '{model_id}' en het teruggevallen model '{retry_model_id}' "
                    f"werkten beide niet. Laatste fout: {e2}"
                )

        text = (resp.choices[0].message.content or "").strip()
        return text, model_id

    raise LLMError(f"Onbekende LLM-provider: '{provider}' (verwacht 'groq' of 'anthropic').")


def strip_json_fences(text: str) -> str:
    """Sommige modellen (vooral via Groq zonder json_mode) verpakken JSON
    soms toch in markdown-fences -- dit maakt het parsen robuust."""
    text = text.strip().strip("`")
    if text.lower().startswith("json"):
        text = text[4:].strip()
    return text
