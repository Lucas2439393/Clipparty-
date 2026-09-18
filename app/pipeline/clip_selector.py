"""
De 'creatieve/redactionele' kern van de pipeline: vindt kandidaat-clips over alle
toegestane bronvideo's heen, scoort ze, genereert captions, en beoordeelt
compliance. Gebruikt de volledige getimede transcripties als basis (nooit alleen
een fragment). Werkt met elke LLM-provider via app.pipeline.llm_provider --
Groq is de standaard, Anthropic is optioneel.
"""
import json
from typing import List

from app.models import CampaignProfile, VideoAnalysis, ClipCandidate, CampaignSettings
from app.pipeline import llm_provider

# Let op: de LLM-output is bewust een top-level JSON-OBJECT (niet een array) --
# Groq's json_object response-mode vereist dat. De array zit in een vaste key.

SELECTION_SYSTEM_PROMPT = """Je bent een ervaren short-form video editor/producer die \
kandidaat-clips selecteert uit lange bronvideo's voor TikTok/Reels/Shorts. Je bepaalt \
ook meteen een passende social caption en hashtags per clip, gebaseerd op de actuele \
campagnebriefing.

Je krijgt: een campagneprofiel (regels van DEZE campagne, gebruik nooit regels van \
andere campagnes) en volledige getimede transcripties van één of meerdere \
toegestane bronvideo's.

Zoek naar sterke hooks, verrassende uitspraken, duidelijke uitleg, interessante \
feiten, cijfers, verhalen, humor, emotionele momenten, discussiemomenten, sterke \
inzichten, en duidelijke payoffs. Beoordeel NOOIT alleen op geïsoleerde tekst -- \
gebruik de context vóór en na een fragment om te bepalen of het zelfstandig \
begrijpelijk is.

Regels voor clipduur:
- absoluut maximum: {max_duration} seconden
- voorkeur: {pref_min}-{pref_max} seconden
- 35-45s alleen als de context dit noodzakelijk maakt
- 45-60s alleen wanneer inhoudelijk noodzakelijk
- verleng NOOIT kunstmatig; als een gedachte in 24s compleet is, gebruik ~24s

Zoek zoveel echt sterke, unieke kandidaten als de bron toelaat (streef naar \
{min_clips}-{max_clips}, maar verzin geen zwakke clips alleen om aan het minimum \
te komen -- lever minder op als dat eerlijker is).

Gebruik ALLEEN starts/eindes die je kunt onderbouwen met de gegeven transcript-\
timestamps. Rond nooit ruw af -- kies het exacte moment waar de gedachte begint \
en eindigt.

Caption/hashtags: baseer de toon op de campagnebriefing (doelgroep, hookregels, \
disclaimers zoals "#ad" indien van toepassing). Geen misleidende clickbait.

BELANGRIJK -- houd de JSON compact, je hebt een beperkt tokenbudget:
- hook, payoff, reden_selectie: elk maximaal 12 woorden.
- caption: maximaal 15 woorden.
- hashtags: maximaal 5 stuks, zonder "#"-teken.
- geen uitleg, geen markdown-fences, geen witruimte/opmaak buiten wat nodig is
  voor geldige JSON.

Antwoord ALLEEN met geldig JSON, exact deze vorm (sleutels in "scores" zijn \
bewust kort gehouden -- gebruik ze exact zo):
{{
  "candidates": [
    {{
      "source_video": string,
      "start": number,
      "end": number,
      "topic": string,
      "hook": string,
      "payoff": string,
      "reden_selectie": string,
      "scores": {{"hook": number, "inhoud": number, "ent": number, "zbg": number,
                   "ctx": number, "payoff": number, "soc": number}},
      "caption": string,
      "hashtags": string[]
    }}
  ]
}}
"""

COMPLIANCE_SYSTEM_PROMPT = """Je controleert kandidaat-clips op compliance met het \
campagneprofiel. Voor elke kandidaat: bepaal of de bron toegestaan is, de inhoud \
toegestaan is, er geen verboden onderwerpen/claims in zitten, de duur binnen de \
regels valt, en of platformregels/disclaimers worden gerespecteerd.

Compliance heeft ALTIJD voorrang boven creatieve score. Een FAIL mag nooit als \
definitieve clip geproduceerd worden.

Antwoord ALLEEN met geldig JSON (geen uitleg, geen markdown-fences), exact deze vorm:
{{
  "results": [
    {{"status": "PASS"|"FAIL", "reason": string}}
  ]
}}
Dezelfde volgorde als de input-kandidaten, één resultaat per kandidaat.
"""


def _transcript_block(analysis: VideoAnalysis) -> str:
    lines = [f"BRONVIDEO: {analysis.source_path} (duur: {analysis.duration:.1f}s)"]
    for seg in analysis.transcript:
        lines.append(f"[{seg.start:.1f}-{seg.end:.1f}] {seg.text}")
    return "\n".join(lines)


def find_candidates(
    profile: CampaignProfile,
    analyses: List[VideoAnalysis],
    settings: CampaignSettings,
    provider: str = "groq",
) -> tuple[List[ClipCandidate], str, str | None, str | None]:
    """Retourneert (candidates, provider_used, fallback_reason, model_used)."""
    provider_used, fallback_reason = llm_provider.resolve_provider(provider)

    max_duration = min(settings.max_duration, profile.maximumduur or settings.max_duration, 60)
    system = SELECTION_SYSTEM_PROMPT.format(
        max_duration=max_duration,
        pref_min=profile.minimumduur or settings.preferred_min_duration,
        pref_max=settings.preferred_max_duration,
        min_clips=settings.min_clips,
        max_clips=settings.max_clips,
    )

    profile_block = json.dumps(profile.model_dump(), ensure_ascii=False, indent=2)
    transcripts_block = "\n\n".join(_transcript_block(a) for a in analyses)

    user_content = (
        f"CAMPAGNEPROFIEL:\n{profile_block}\n\n"
        f"TRANSCRIPTIES VAN ALLE TOEGESTANE BRONVIDEO'S:\n{transcripts_block}"
    )

    text, model_used = llm_provider.complete(
        system=system, user=user_content, provider=provider_used,
        max_tokens=8192, json_mode=True,
    )
    text = llm_provider.strip_json_fences(text)

    try:
        parsed = json.loads(text)
        raw_candidates = parsed["candidates"] if isinstance(parsed, dict) else parsed
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise RuntimeError(f"Kon clipselectie niet parsen als JSON: {e}\nOutput: {text[:800]}")

    candidates = []
    for rc in raw_candidates:
        start, end = float(rc["start"]), float(rc["end"])
        if end <= start:
            continue
        duration = end - start
        if duration > max_duration + 2:  # kleine marge voor afronding
            continue
        candidates.append(ClipCandidate(
            source_video=rc["source_video"],
            start=start,
            end=end,
            duration=duration,
            topic=rc.get("topic"),
            hook=rc.get("hook"),
            payoff=rc.get("payoff"),
            reden_selectie=rc.get("reden_selectie"),
            scores=rc.get("scores", {}),
            caption=rc.get("caption"),
            hashtags=rc.get("hashtags", []),
        ))
    return candidates, provider_used, fallback_reason, model_used


def check_compliance(
    profile: CampaignProfile,
    candidates: List[ClipCandidate],
    provider: str = "groq",
) -> tuple[List[ClipCandidate], str, str | None, str | None]:
    """Retourneert (candidates, provider_used, fallback_reason, model_used)."""
    provider_used, fallback_reason = llm_provider.resolve_provider(provider)

    if not candidates:
        return candidates, provider_used, fallback_reason, None

    profile_block = json.dumps(profile.model_dump(), ensure_ascii=False, indent=2)
    cand_block = json.dumps(
        [{"source_video": c.source_video, "start": c.start, "end": c.end,
          "topic": c.topic, "hook": c.hook, "payoff": c.payoff} for c in candidates],
        ensure_ascii=False, indent=2,
    )

    text, model_used = llm_provider.complete(
        system=COMPLIANCE_SYSTEM_PROMPT,
        user=f"CAMPAGNEPROFIEL:\n{profile_block}\n\nKANDIDATEN:\n{cand_block}",
        provider=provider_used,
        max_tokens=4000,
        json_mode=True,
    )
    text = llm_provider.strip_json_fences(text)

    try:
        parsed = json.loads(text)
        results = parsed["results"] if isinstance(parsed, dict) else parsed
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise RuntimeError(f"Kon compliance-check niet parsen als JSON: {e}\nOutput: {text[:800]}")

    for cand, res in zip(candidates, results):
        cand.compliance_status = res.get("status", "FAIL")
        cand.compliance_reason = res.get("reason")
    return candidates, provider_used, fallback_reason, model_used


def rank_and_select(
    candidates: List[ClipCandidate],
    settings: CampaignSettings,
) -> List[ClipCandidate]:
    """Filtert op PASS, rangschikt op totaalscore, dedupliceert inhoudelijke overlap,
    en beperkt tot max_clips (nooit meer dan werkelijk sterke, unieke kandidaten)."""
    passed = [c for c in candidates if c.compliance_status == "PASS"]

    def total_score(c: ClipCandidate) -> float:
        return sum(c.scores.values()) if c.scores else 0.0

    passed.sort(key=total_score, reverse=True)

    selected: List[ClipCandidate] = []
    for c in passed:
        # dedupliceer: sla over als er al een geselecteerde clip is die grotendeels
        # hetzelfde tijdsegment in dezelfde bronvideo beslaat (inhoudelijke overlap)
        overlap = False
        for s in selected:
            if s.source_video != c.source_video:
                continue
            latest_start = max(s.start, c.start)
            earliest_end = min(s.end, c.end)
            overlap_len = max(0.0, earliest_end - latest_start)
            if overlap_len > 0.5 * min(s.duration, c.duration):
                overlap = True
                break
        if not overlap:
            selected.append(c)
        if len(selected) >= settings.max_clips:
            break

    return selected
