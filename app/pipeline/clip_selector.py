"""
De 'creatieve/redactionele' kern van de pipeline: vindt kandidaat-clips over alle
toegestane bronvideo's heen, scoort ze, genereert captions, en beoordeelt
compliance. Gebruikt de volledige getimede transcripties als basis (nooit alleen
een fragment). Werkt met elke LLM-provider via app.pipeline.llm_provider --
Groq is de standaard, Anthropic is optioneel.
"""
import json
import re
from pathlib import Path
from typing import List

from app.models import CampaignProfile, VideoAnalysis, ClipCandidate, CampaignSettings
from app.pipeline import llm_provider

# De selectie-output is bewust een klein JSON-object. Alle rijke metadata wordt lokaal afgeleid.

MAX_SELECTION_INPUT_CHARS = 7000
WINDOW_SECONDS = 45


def _profile_terms(profile: CampaignProfile) -> set[str]:
    values = []
    for key, value in profile.model_dump().items():
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(x) for x in value)
    stop = {
        "de", "het", "een", "van", "voor", "met", "en", "of", "op", "in",
        "te", "is", "zijn", "dat", "die", "dit", "als", "aan", "om", "naar",
        "the", "a", "and", "of", "to", "for", "with",
    }
    words = re.findall(r"[a-zA-ZÀ-ÿ0-9]{4,}", " ".join(values).lower())
    return {w for w in words if w not in stop}


def _transcript_windows(analysis: VideoAnalysis) -> list[dict]:
    """Maak kleine tekstvensters met exacte transcript-timestamps."""
    segs = analysis.transcript
    if not segs:
        return []
    windows = []
    cur = []
    window_start = segs[0].start
    for seg in segs:
        if cur and seg.start - window_start >= WINDOW_SECONDS:
            windows.append(_make_window(cur))
            cur = []
            window_start = seg.start
        cur.append(seg)
    if cur:
        windows.append(_make_window(cur))
    return windows


def _make_window(segs):
    return {
        "start": float(segs[0].start),
        "end": float(segs[-1].end),
        "text": " ".join(s.text.strip() for s in segs if s.text.strip()),
    }


def _compact_transcript(analysis: VideoAnalysis, profile: CampaignProfile) -> str:
    """Beperk de LLM-input lokaal. Korte video's gaan volledig; lange video's
    krijgen een combinatie van inhoudelijk interessante en gelijkmatig verdeelde
    vensters. Zo wordt een 60-minuten transcript nooit blind naar de cloud gestuurd."""
    full = "\n".join(
        f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in analysis.transcript
    )
    if len(full) <= MAX_SELECTION_INPUT_CHARS:
        return f"BRONVIDEO: {analysis.source_path}\n{full}"

    terms = _profile_terms(profile)
    windows = _transcript_windows(analysis)
    scored = []
    for i, w in enumerate(windows):
        low = w["text"].lower()
        words = re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", low)
        term_hits = sum(1 for word in words if word in terms)
        markers = sum(low.count(m) for m in (
            "waarom", "belangrijk", "probleem", "oplossing", "tip",
            "fout", "beste", "nooit", "altijd", "verrass", "eerlijk",
            "maar", "dus", "eigenlijk", "wist je", "hoe", "wat als",
        ))
        numbers = len(re.findall(r"\b\d+(?:[.,]\d+)?\b", low))
        questions = low.count("?")
        density = min(len(words), 120) / 120
        score = term_hits * 5 + markers * 1.5 + numbers * 0.8 + questions * 1.0 + density
        scored.append((score, i, w))

    # Top inhoudelijke vensters + uniforme dekking van de hele video.
    top_n = min(8, max(5, len(windows) // 10))
    selected = {i for _, i, _ in sorted(scored, reverse=True)[:top_n]}
    uniform_n = min(4, len(windows))
    if uniform_n:
        for i in [round(x) for x in __import__("numpy").linspace(0, len(windows)-1, uniform_n)]:
            selected.add(i)

    chosen = [windows[i] for i in sorted(selected)]
    parts = [f"BRONVIDEO: {analysis.source_path} (duur {analysis.duration:.1f}s)"]
    total = len(parts[0])
    for w in chosen:
        block = f"[{w['start']:.1f}-{w['end']:.1f}] {w['text']}"
        if total + len(block) + 1 > MAX_SELECTION_INPUT_CHARS:
            break
        parts.append(block)
        total += len(block) + 1
    return "\n".join(parts)


SELECTION_SYSTEM_PROMPT = """Je bent een professionele short-form editor.
Selecteer de sterkste zelfstandige momenten uit de aangeleverde transcriptvensters.

Harde regels:
- maximaal {max_duration} seconden per clip; voorkeur {pref_min}-{pref_max}s
- gebruik alleen exacte timestamps uit de input
- geen overlap tussen clips uit dezelfde bron
- kies complete, bruikbare momenten met een duidelijke hook, context en payoff
- geef zoveel sterke kandidaten als de input rechtvaardigt, tot maximaal {max_clips}
- probeer niet kunstmatig een minimum te halen en sluit een goede kandidaat niet uit omdat er geen minimum is
- score 0-100 voor inhoudelijke kwaliteit
- antwoord ALLEEN compact geldig JSON
- GEEN captions, hashtags, redenen, topics of lange tekst

Exact formaat:
{{"candidates":[
  {{"source_video":"string","start":0,"end":25,"score":87}}
]}}
"""


def _clip_context(analysis: VideoAnalysis, start: float, end: float) -> str:
    segs = [s for s in analysis.transcript if s.end >= start - 3 and s.start <= end + 3]
    return " ".join(s.text.strip() for s in segs if s.text.strip())


def _local_enrichment(analysis: VideoAnalysis, profile: CampaignProfile, start: float, end: float, score: float) -> dict:
    segs = [s for s in analysis.transcript if s.end >= start and s.start <= end]
    text = [s.text.strip() for s in segs if s.text.strip()]
    hook = text[0] if text else None
    payoff = text[-1] if text else None
    context = _clip_context(analysis, start, end)
    topic = " ".join(context.split()[:12]) if context else None
    reason = f"Sterk moment volgens inhoudelijke score ({round(score)}/100)."
    caption = " ".join(context.split()[:15]) if context else None
    hashtags = [str(h).lstrip("#") for h in profile.hashtags[:5]]
    # Hashtags komen alleen uit de campagnebriefing; nooit verzonnen door een extra LLM-call.
    return {
        "topic": topic,
        "hook": hook[:180] if hook else None,
        "payoff": payoff[:180] if payoff else None,
        "reden_selectie": reason,
        "caption": caption[:180] if caption else None,
        "hashtags": hashtags,
    }


def _fallback_candidates(analysis: VideoAnalysis, profile: CampaignProfile, settings: CampaignSettings) -> List[ClipCandidate]:
    """Deterministische safety-net: maak bruikbare tijdvensters als de LLM
    geen kandidaten teruggeeft. Zo kan één model-/JSON-fout nooit resulteren in
    nul video's. Compliance wordt daarna nog steeds normaal uitgevoerd."""
    max_duration = min(settings.max_duration, profile.maximumduur or settings.max_duration, 60)
    pref_min = max(8, min(settings.preferred_min_duration, max_duration))
    pref_max = max(pref_min, min(settings.preferred_max_duration, max_duration))
    candidates: List[ClipCandidate] = []
    segs = [s for s in analysis.transcript if s.text.strip() and s.end > s.start]

    if segs:
        # Start bij inhoudelijke segmenten en bouw compacte 15-35s windows.
        for seg in segs:
            if len(candidates) >= settings.max_clips:
                break
            start = max(0.0, float(seg.start))
            target_end = min(float(analysis.duration), start + pref_max)
            end = float(seg.end)
            j = segs.index(seg) + 1
            while end < start + pref_min and j < len(segs):
                end = max(end, float(segs[j].end))
                j += 1
            end = min(target_end, end)
            if end - start < min(pref_min, analysis.duration):
                continue
            context = _clip_context(analysis, start, end)
            words = len(context.split())
            score = min(82.0, 48.0 + min(34.0, words * 0.9))
            enriched = _local_enrichment(analysis, profile, start, end, score)
            candidates.append(ClipCandidate(
                source_video=analysis.source_path,
                start=start,
                end=end,
                duration=end-start,
                scores={"overall": score},
                **enriched,
            ))
        return candidates

    # Ook zonder transcript moet de gebruiker kunnen knippen.
    step = max(1.0, pref_max)
    start = 0.0
    while start < analysis.duration and len(candidates) < settings.max_clips:
        end = min(analysis.duration, start + pref_max)
        if end - start >= min(pref_min, analysis.duration) or (analysis.duration <= pref_min and start == 0):
            candidates.append(ClipCandidate(
                source_video=analysis.source_path,
                start=start,
                end=end,
                duration=end-start,
                scores={"overall": 50.0},
                topic="Bronvideo zonder bruikbare transcriptsegmenten",
                reden_selectie="Fallback-selectie zonder transcript; compliance en kwaliteitscontrole blijven actief.",
            ))
        start += step
    return candidates


def fallback_candidates(
    analysis: VideoAnalysis, profile: CampaignProfile, settings: CampaignSettings
) -> List[ClipCandidate]:
    """Public safety-net wrapper used by the job runner for a final best-effort pass."""
    return _fallback_candidates(analysis, profile, settings)


def find_candidates(
    profile: CampaignProfile,
    analyses: List[VideoAnalysis],
    settings: CampaignSettings,
    provider: str = "groq",
) -> tuple[List[ClipCandidate], str, str | None, str | None]:
    provider_used, fallback_reason = llm_provider.resolve_provider(provider)
    max_duration = min(settings.max_duration, profile.maximumduur or settings.max_duration, 60)
    system = SELECTION_SYSTEM_PROMPT.format(
        max_duration=max_duration,
        pref_min=profile.minimumduur or settings.preferred_min_duration,
        pref_max=settings.preferred_max_duration,
        min_clips=settings.min_clips,
        max_clips=settings.max_clips,
    )

    candidates: List[ClipCandidate] = []
    # Eén compacte LLM-call per bronvideo. Nooit een gigantisch gecombineerd transcript.
    for analysis in analyses:
        compact = _compact_transcript(analysis, profile)
        # Extra harde veiligheidsmarge: de volledige HTTP-body voor clipselectie
        # blijft klein genoeg voor GPT-OSS/Groq. We sturen nooit een volledig
        # transcript naar de chat-endpoint.
        if len(compact) > MAX_SELECTION_INPUT_CHARS:
            compact = compact[:MAX_SELECTION_INPUT_CHARS]
            last = compact.rfind(" ")
            if last > 1000:
                compact = compact[:last]
        profile_json = json.dumps(profile.model_dump(), ensure_ascii=False, separators=(',', ':'))
        user_content = (
            f"CAMPAGNE:\n{profile_json}\n\n"
            f"TRANSCRIPTVENSTERS:\n{compact}"
        )
        text, model_used = llm_provider.complete(
            system=system,
            user=user_content,
            provider=provider_used,
            max_tokens=450,
            json_mode=True,
        )
        text = llm_provider.strip_json_fences(text)
        try:
            parsed = json.loads(text)
            raw_candidates = parsed.get("candidates", []) if isinstance(parsed, dict) else []
        except (json.JSONDecodeError, TypeError):
            raw_candidates = []

        # Nooit stoppen met 0 clips door een lege/ongeldige LLM-response.
        # Gebruik lokale transcript-/tijdvensterselectie als safety-net.
        if not raw_candidates:
            candidates.extend(_fallback_candidates(analysis, profile, settings))
            continue

        for rc in raw_candidates[:settings.max_clips]:
            try:
                start, end = float(rc["start"]), float(rc["end"])
                score = float(rc.get("score", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            duration = end - start
            if duration > max_duration + 1 or start < 0 or end > analysis.duration + 1:
                continue
            # Koppel de kandidaat aan de echte bronvideo; het model hoeft dit niet te herhalen.
            enriched = _local_enrichment(analysis, profile, start, end, score)
            candidates.append(ClipCandidate(
                source_video=analysis.source_path,
                start=start,
                end=min(end, analysis.duration),
                duration=min(end, analysis.duration) - start,
                scores={"overall": max(0.0, min(100.0, score))},
                **enriched,
            ))
    return candidates, provider_used, fallback_reason, model_used if analyses else None


def check_compliance(
    profile: CampaignProfile,
    candidates: List[ClipCandidate],
    provider: str = "groq",
) -> tuple[List[ClipCandidate], str, str | None, str | None]:
    """Lokale compliance-check. Geen extra cloud-call: bron, duur en expliciet
    verboden onderwerp/claims worden deterministisch gecontroleerd."""
    provider_used = (provider or "groq").strip().lower()
    fallback_reason = None
    if provider_used not in ("groq", "anthropic"):
        provider_used = "groq"
    forbidden = [
        str(x).lower() for x in (profile.verboden_onderwerpen + profile.verboden_claims)
        if str(x).strip()
    ]
    # IMPORTANT: source-video restrictions are already enforced upstream in
    # job_manager when the actual uploaded/downloaded files are resolved.
    # Do NOT compare the final local path to the briefing's human-readable
    # filename here: uploads intentionally get a UUID prefix (for example
    # ``f2d158dd_video playback (2).mp4``), while a briefing usually contains
    # ``video playback (2).mp4``. The old exact/basename comparison therefore
    # marked every otherwise-valid candidate as FAIL and rank_and_select then
    # returned zero clips.
    #
    # Keeping source validation out of this stage also avoids rejecting
    # YouTube-downloaded files whose local filename differs from the URL/title.
    max_duration = profile.maximumduur or 60
    for c in candidates:
        reasons = []
        if c.duration > min(max_duration, 60) + 1:
            reasons.append("clipduur overschrijdt de campagnegrens")
        context = " ".join(filter(None, [c.topic, c.hook, c.payoff])).lower()
        hits = [term for term in forbidden if term in context]
        if hits:
            reasons.append("verboden onderwerp/claim aangetroffen")
        if reasons:
            c.compliance_status = "FAIL"
            c.compliance_reason = "; ".join(reasons)
        else:
            c.compliance_status = "PASS"
            c.compliance_reason = "Lokale compliance-check geslaagd."
    return candidates, provider_used, fallback_reason, None


def rank_and_select(
    candidates: List[ClipCandidate],
    settings: CampaignSettings,
) -> List[ClipCandidate]:
    passed = [c for c in candidates if c.compliance_status == "PASS"]

    def total_score(c: ClipCandidate) -> float:
        return sum(c.scores.values()) if c.scores else 0.0

    passed.sort(key=total_score, reverse=True)
    selected: List[ClipCandidate] = []
    for c in passed:
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
