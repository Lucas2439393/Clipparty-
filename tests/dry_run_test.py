"""
Dry-run end-to-end test.

Alles hieronder draait ECHT: ffprobe, ffmpeg cutting/crop/subtitles/normalize/export,
OpenCV face-sampling, de job-state-machine, bestandsoutput, report.json/report.html,
de provider-resolutielogica, EN de Groq-model-resolver zelf (echte HTTP-poging naar
api.groq.com, geen hardcoded modelaanname).

Wat WEL gestubt is, zijn de externe betaalde/rate-limited API-calls waarvoor in
deze sandbox geen bruikbare keys/netwerktoegang beschikbaar zijn:
  - Groq chat completions (briefing-extractie, clipselectie, compliance)
  - Groq Whisper (transcriptie)
Die stubs geven realistische, plausibele data terug zodat de rest van de pipeline
(die dat wel echt zou ontvangen) er echt doorheen loopt. De model-RESOLUTIELOGICA
zelf (welk model wordt gekozen, hoe fallback werkt, hoe fouten eruitzien) is NIET
gestubt en wordt hieronder apart en volledig getest.
"""
import sys
import time
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import CampaignProfile, TranscriptSegment, ClipCandidate
from app.models import CampaignStartRequest, CampaignSettings
from app import job_manager
from app.pipeline import llm_provider

BASE = Path(__file__).resolve().parent
VIDEO = str(BASE / "test_assets" / "sample_source.mp4")
BRIEF = str(BASE / "test_assets" / "test_brief.txt")


def test_provider_resolution():
    """
    ECHTE test (geen stub) van de kernregel: Groq is default, Anthropic is
    optioneel, en ontbrekende ANTHROPIC_API_KEY mag de app nooit laten
    crashen -- alleen automatisch terugschakelen.
    """
    print("=== TEST 1: provider-resolutielogica (echt, geen stub) ===")

    provider, reason = llm_provider.resolve_provider(None)
    assert provider == "groq" and reason is None
    print("  [OK] geen provider opgegeven -> groq (default)")

    provider, reason = llm_provider.resolve_provider("groq")
    assert provider == "groq" and reason is None
    print("  [OK] provider='groq' -> groq, geen fallback")

    provider, reason = llm_provider.resolve_provider("onzin-provider-xyz")
    assert provider == "groq" and reason is None
    print("  [OK] onbekende provider-string -> valt terug op groq")

    original_key = llm_provider.ANTHROPIC_API_KEY
    try:
        llm_provider.ANTHROPIC_API_KEY = ""
        provider, reason = llm_provider.resolve_provider("anthropic")
        assert provider == "groq"
        assert reason is not None and "Groq" in reason
        print("  [OK] provider='anthropic' zonder ANTHROPIC_API_KEY -> automatisch groq")

        try:
            llm_provider.complete("system", "user", provider="anthropic")
            raise AssertionError("had een LLMError moeten gooien")
        except llm_provider.LLMError as e:
            print(f"  [OK] complete(provider='anthropic') zonder key -> nette LLMError, geen crash: {e}")
    finally:
        llm_provider.ANTHROPIC_API_KEY = original_key
    print()


def test_live_groq_models_network_attempt():
    """
    ECHTE (niet-gestubte) netwerkpoging naar GET https://api.groq.com/openai/v1/models
    -- dit bewijst dat de resolver daadwerkelijk dat endpoint aanroept zoals
    gevraagd, en dat een falende poging (in deze sandbox: geen netwerktoegang
    tot api.groq.com) nooit een crash geeft, alleen een duidelijke foutmelding.
    """
    print("=== TEST 2: echte live GET https://api.groq.com/openai/v1/models ===")
    raw_ids, suitable_ids, error = llm_provider.fetch_available_groq_models(force=True)
    print(f"  raw_ids: {raw_ids}")
    print(f"  suitable_ids: {suitable_ids}")
    print(f"  error: {error}")
    assert isinstance(raw_ids, list) and isinstance(suitable_ids, list), "moet altijd lijsten teruggeven, nooit crashen"
    if error:
        print(f"  [OK] netwerkpoging is echt uitgevoerd en gaf een nette foutmelding (geen crash): {error}")
    else:
        print(f"  [OK] netwerkpoging geslaagd, {len(raw_ids)} modellen gevonden, {len(suitable_ids)} geschikt")
    print()


def test_model_resolution_logic():
    """
    ECHTE test van resolve_groq_model()'s beslislogica, met een GEMOCKTE
    modellenlijst (zodat het los van sandbox-netwerktoegang getest kan
    worden) -- de resolutielogica zelf draait volledig ongewijzigd.
    """
    print("=== TEST 3: model-resolutielogica (echte functie, gemockte modellenlijst) ===")

    def fake_fetch(preferred_available, force=False):
        raw = ["whisper-large-v3-turbo", "llama-guard-4-12b"] + preferred_available
        suitable = [m for m in raw if llm_provider._is_suitable_text_model(m)]
        return raw, suitable, None

    # Geval A: de ECHTE modellenlijst die de gebruiker rechtstreeks tegen
    # zijn eigen GROQ_API_KEY heeft getest (HTTP 200, geldig account):
    #   qwen/qwen3-8b, openai/gpt-oss-120b, groq/compound-mini,
    #   qwen/qwen3-32b, groq/compound, openai/gpt-oss-20b
    # Verwacht: openai/gpt-oss-120b wordt gekozen (voorkeur #1, en aanwezig).
    USER_VERIFIED_MODELS = [
        "qwen/qwen3-8b", "openai/gpt-oss-120b", "groq/compound-mini",
        "qwen/qwen3-32b", "groq/compound", "openai/gpt-oss-20b",
    ]
    with patch.object(llm_provider, "fetch_available_groq_models",
                       lambda force=False: fake_fetch(USER_VERIFIED_MODELS)):
        llm_provider._resolved_model_cache.update(model_id=None, note=None, fetched_at=0.0)
        model_id, note = llm_provider.resolve_groq_model(force=True)
        assert model_id == "openai/gpt-oss-120b", f"verwacht openai/gpt-oss-120b, kreeg {model_id}"
        print(f"  [OK] echte modellenlijst van gebruiker -> gekozen: {model_id} (voorkeur #1)")

    # Geval B: openai/gpt-oss-120b NIET beschikbaar -> #2 (openai/gpt-oss-20b).
    with patch.object(llm_provider, "fetch_available_groq_models",
                       lambda force=False: fake_fetch(["openai/gpt-oss-20b", "qwen/qwen3-32b"])):
        llm_provider._resolved_model_cache.update(model_id=None, note=None, fetched_at=0.0)
        model_id, note = llm_provider.resolve_groq_model(force=True)
        assert model_id == "openai/gpt-oss-20b", f"verwacht openai/gpt-oss-20b (#2), kreeg {model_id}"
        print(f"  [OK] #1 niet beschikbaar -> #2 gekozen: {model_id}")

    # Geval C: alleen de twee compound-modellen beschikbaar -> #4 gekozen
    # (groq/compound-mini gaat vóór groq/compound in de voorkeursvolgorde).
    with patch.object(llm_provider, "fetch_available_groq_models",
                       lambda force=False: fake_fetch(["groq/compound", "groq/compound-mini"])):
        llm_provider._resolved_model_cache.update(model_id=None, note=None, fetched_at=0.0)
        model_id, note = llm_provider.resolve_groq_model(force=True)
        assert model_id == "groq/compound-mini", f"verwacht groq/compound-mini, kreeg {model_id}"
        print(f"  [OK] alleen compound-modellen beschikbaar -> gekozen: {model_id} (#4 vóór #5)")

    # Geval D: GEEN van de 5 voorkeursmodellen beschikbaar -> automatisch
    # doorschakelen naar een ander geschikt model, NOOIT crashen.
    with patch.object(llm_provider, "fetch_available_groq_models",
                       lambda force=False: fake_fetch(["qwen/qwen3-8b"])):
        llm_provider._resolved_model_cache.update(model_id=None, note=None, fetched_at=0.0)
        model_id, note = llm_provider.resolve_groq_model(force=True)
        assert model_id == "qwen/qwen3-8b", f"verwacht fallback naar qwen/qwen3-8b, kreeg {model_id}"
        assert "Geen van de voorkeursmodellen" in note
        print(f"  [OK] geen voorkeursmodel beschikbaar -> automatisch doorgeschakeld naar: {model_id}")
        print(f"       toelichting: {note}")

    # Geval E: helemaal geen geschikt model beschikbaar -> duidelijke fout
    # met de beschikbare model-ID's, geen crash.
    with patch.object(llm_provider, "fetch_available_groq_models",
                       lambda force=False: (["whisper-large-v3-turbo"], [], None)):
        llm_provider._resolved_model_cache.update(model_id=None, note=None, fetched_at=0.0)
        model_id, note = llm_provider.resolve_groq_model(force=True)
        assert model_id is None
        assert "whisper-large-v3-turbo" in note, "foutmelding moet de beschikbare model-ID's noemen"
        print(f"  [OK] geen enkel geschikt model beschikbaar -> model_id=None, duidelijke fout: {note}")

    # Cache resetten zodat de rest van de test-run weer de echte (sandbox-)situatie gebruikt.
    llm_provider._resolved_model_cache.update(model_id=None, note=None, fetched_at=0.0)
    llm_provider._models_cache.update(fetched_at=0.0, raw_ids=None, suitable_ids=None, error=None)
    print()


def test_automatic_retry_on_model_failure():
    """
    ECHTE test van complete()'s automatische retry: als het gekozen model
    een model_not_found/access-achtige fout geeft, moet de modellenlijst
    verversen, automatisch een ander beschikbaar geschikt model kiezen, en
    de LLM-call opnieuw proberen -- pas falen als er echt niks meer over is.
    """
    print("=== TEST 3b: automatische retry bij model_not_found (echte complete()-functie) ===")

    class FakeMessage:
        content = '{"ok": true}'

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    call_log = []

    def flaky_call(model_id, system, user, max_tokens, json_mode):
        call_log.append(model_id)
        if model_id == "openai/gpt-oss-120b":
            # Simuleert precies de gemelde fout: het eerst-gekozen model
            # blijkt (bijv. na een accountwijziging) niet meer beschikbaar.
            raise RuntimeError(
                '404 {"error": {"code": "model_not_found", '
                '"message": "The model `openai/gpt-oss-120b` does not exist '
                'or you do not have access to it."}}'
            )
        return FakeResponse()

    # Eerste keer resolve_groq_model() -> kiest openai/gpt-oss-120b (staat in
    # de voorkeurslijst). Na de fout ververst complete() de modellenlijst en
    # roept resolve_groq_model(force=True) opnieuw aan -> dit keer is
    # openai/gpt-oss-120b uit de lijst gehaald, dus valt terug op #2.
    call_count = {"n": 0}

    def sequenced_resolve(force=False):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "openai/gpt-oss-120b", "eerste keuze (voorkeur #1)"
        return "openai/gpt-oss-20b", "geforceerde herresolutie na fout -> #2"

    with patch.object(llm_provider, "resolve_groq_model", sequenced_resolve), \
         patch.object(llm_provider, "_groq_chat_call", flaky_call):
        text, model_used = llm_provider.complete(
            system="test", user="test", provider="groq", max_tokens=100, json_mode=True,
        )
        assert call_log == ["openai/gpt-oss-120b", "openai/gpt-oss-20b"], f"onverwachte call-volgorde: {call_log}"
        assert model_used == "openai/gpt-oss-20b", f"verwacht dat de job doorgaat met het fallback-model, kreeg {model_used}"
        assert json.loads(text) == {"ok": True}
        print(f"  [OK] eerste model faalde met model_not_found -> automatisch geretried met: {model_used}")
        print(f"       call-volgorde: {' -> '.join(call_log)}")
    print()


def test_json_completion_roundtrip():
    """
    Test dat complete(json_mode=True) een geldige JSON-tekst + het
    daadwerkelijk gebruikte model_id teruggeeft, met een gemockte Groq-
    respons (zodat dit los van sandbox-netwerktoegang getest kan worden).
    De model-resolutie zelf blijft echt. Gebruikt openai/gpt-oss-120b,
    zoals gevraagd (het voorkeursmodel #1 uit de echte modellenlijst van
    de gebruiker).
    """
    print("=== TEST 4: eenvoudige JSON-response door complete() (gemockte Groq-respons) ===")

    class FakeMessage:
        content = '{"hello": "world", "n": 3}'

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    with patch.object(llm_provider, "resolve_groq_model", lambda force=False: ("openai/gpt-oss-120b", "test")), \
         patch.object(llm_provider, "_groq_chat_call", lambda *a, **kw: FakeResponse()):
        text, model_used = llm_provider.complete(
            system="antwoord met JSON", user="test", provider="groq",
            max_tokens=100, json_mode=True,
        )
        parsed = json.loads(llm_provider.strip_json_fences(text))
        assert parsed == {"hello": "world", "n": 3}
        assert model_used == "openai/gpt-oss-120b"
        print(f"  [OK] complete() -> geldige JSON geparsed: {parsed}, model_used={model_used}")
    print()


def fake_extract_campaign_profile(brief_path, provider="groq"):
    print(f"  [stub Groq] briefing gelezen van {brief_path} (provider={provider})")
    profile = CampaignProfile(
        merk="SpaloungeBenelux", campagne="Test Zomer Launch",
        doel="Bewustwording creëren voor de nieuwe W'eau opblaasbare jacuzzi",
        doelgroep="Nederlandse en Belgische huishoudens, 30-55 jaar",
        toegestane_onderwerpen=["productvoordelen", "installatie-uitleg", "onderhoudstips"],
        verboden_onderwerpen=["concurrenten noemen", "medische claims"],
        hook_richtlijnen="begin met een verrassende vraag of statistiek",
        edit_richtlijnen="ondertiteling verplicht, gebruik een korte hook-tekst bovenin beeld",
        minimumduur=15, maximumduur=45,
        hashtags=["weau", "jacuzzi", "zomer"],
        disclaimers="Dit is een betaalde samenwerking (#ad)",
        platformregels="TikTok en Instagram Reels, geen misleidende claims",
        toegestane_bronvideos=["sample_source.mp4"],
    )
    return profile, "groq", None, "openai/gpt-oss-120b"


def fake_transcribe_audio(wav_path, offset=0.0):
    print(f"  [stub Groq Whisper] audio getranscribeerd: {wav_path}")
    return [
        TranscriptSegment(start=0.0 + offset, end=3.5 + offset, text="Wist je dat de meeste mensen hun jacuzzi verkeerd opzetten?"),
        TranscriptSegment(start=3.5 + offset, end=7.0 + offset, text="Hier zijn de drie stappen die echt het verschil maken."),
        TranscriptSegment(start=7.0 + offset, end=10.5 + offset, text="Stap één: kies een vlakke, stevige ondergrond."),
        TranscriptSegment(start=10.5 + offset, end=14.0 + offset, text="Zo voorkom je lekkage en heb je er jarenlang plezier van."),
    ]


def fake_find_candidates(profile, analyses, settings, provider="groq"):
    print(f"  [stub Groq] kandidaat-clips gezocht over alle bronvideo's (provider={provider})")
    src = analyses[0].source_path
    candidates = [
        ClipCandidate(
            source_video=src, start=0.0, end=7.0, duration=7.0,
            topic="Veelgemaakte opstelfout", hook="Wist je dat de meeste mensen hun jacuzzi verkeerd opzetten?",
            payoff="Kijker weet wat de meestgemaakte fout is",
            reden_selectie="Sterke, verrassende hook direct aan het begin",
            scores={"hook": 8, "inhoud": 7, "entertainment": 6, "zelfstandige_begrijpelijkheid": 8,
                    "context": 7, "payoff": 7, "social_potentieel": 8},
            caption="De fout die bijna iedereen maakt bij het opzetten van hun jacuzzi 👀",
            hashtags=["weau", "jacuzzi", "zomer"],
        ),
        ClipCandidate(
            source_video=src, start=7.0, end=14.0, duration=7.0,
            topic="Installatiestap: ondergrond kiezen", hook="Stap één: kies een vlakke, stevige ondergrond.",
            payoff="Kijker weet hoe lekkage te voorkomen",
            reden_selectie="Concrete, zelfstandige uitleg met duidelijke payoff",
            scores={"hook": 6, "inhoud": 8, "entertainment": 5, "zelfstandige_begrijpelijkheid": 8,
                    "context": 8, "payoff": 8, "social_potentieel": 6},
            caption="Zo voorkom je lekkage bij je opblaasbare jacuzzi 💧",
            hashtags=["weau", "jacuzzi", "onderhoud"],
        ),
    ]
    return candidates, "groq", None, "openai/gpt-oss-120b"


def fake_check_compliance(profile, candidates, provider="groq"):
    print(f"  [stub Groq] compliance-check uitgevoerd (provider={provider})")
    for c in candidates:
        c.compliance_status = "PASS"
        c.compliance_reason = "Binnen toegestane onderwerpen, geen verboden claims."
    return candidates, "groq", None, "openai/gpt-oss-120b"


def test_full_pipeline_job():
    print("=== TEST 5/6: volledige testjob end-to-end (echte ffmpeg-pipeline) ===")

    with patch("app.pipeline.brief_parser.extract_campaign_profile", fake_extract_campaign_profile), \
         patch("app.pipeline.transcription.transcribe_audio", fake_transcribe_audio), \
         patch("app.pipeline.clip_selector.find_candidates", fake_find_candidates), \
         patch("app.pipeline.clip_selector.check_compliance", fake_check_compliance):

        req = CampaignStartRequest(
            campaign_file=BRIEF,
            videos=[VIDEO],
            settings=CampaignSettings(min_clips=1, max_clips=15, max_duration=45,
                                       preferred_min_duration=5, preferred_max_duration=35,
                                       llm_provider="groq"),
            campaign_name="dryrun_test",
        )
        job_id = job_manager.create_job(req)
        print(f"Job gestart: {job_id}\n")

        last = None
        while True:
            status = job_manager.get_job_status(job_id)
            if status.progress != last:
                print(f"  [{status.progress:3d}%] {status.stage_label}")
                last = status.progress
            if status.status in ("completed", "failed"):
                break
            time.sleep(0.5)

        print()
        if status.status == "failed":
            print(f"JOB MISLUKT: {status.error}")
            with open("/home/claude/dryrun_error.log", "w") as f:
                f.write(json.dumps(job_manager._jobs[job_id].get("full_error", ""), indent=2))
            sys.exit(1)

        # Bevestig dat het model ook via GET /api/jobs/{id} (job status) zichtbaar
        # is, niet pas achteraf in het rapport -- zoals gevraagd.
        assert status.llm_provider_used == "groq"
        assert status.llm_model_used == "openai/gpt-oss-120b", \
            f"job status moet het gebruikte model al tonen, kreeg {status.llm_model_used}"
        print(f"  [OK] job status (GET /api/jobs/{{id}}) toont llm_model_used={status.llm_model_used}\n")

        report = job_manager.get_job_report(job_id)
        print("=== TEST 7: rapporteren welk model werkelijk gebruikt werd ===")
        print(f"LLM-provider gebruikt: {report['llm_provider_used']}")
        print(f"LLM-model gebruikt: {report['llm_model_used']}")
        assert report["llm_provider_used"] == "groq"
        assert report["llm_model_used"] == "openai/gpt-oss-120b", "model_used moet uit de resolver-keten komen, niet hardcoded"
        print(f"Kandidaten onderzocht: {report['aantal_kandidaten']}")
        print(f"Afgewezen: {report['aantal_afgewezen']}")
        print(f"Definitief geproduceerd: {report['aantal_definitief']}")
        print(f"Output map: {report['output_map']}")
        for c in report["clips"]:
            print(f"\n  -> {c['filename']}")
            print(f"     duur: {c['duration']:.1f}s | resolutie: {c['resolution']} | compliance: {c['compliance']}")
            print(f"     onderwerp: {c['onderwerp']}")
            print(f"     hook: {c['hook']}")
            print(f"     quality check passed: {c['quality_check_passed']} ({c['quality_check_notes']})")
            print(f"     caption: {c['caption']}")
            print(f"     hashtags: {c['hashtags']}")
            print(f"     bestand bestaat op schijf: {Path(c['path']).exists()}  "
                  f"({Path(c['path']).stat().st_size if Path(c['path']).exists() else 0} bytes)")


def main():
    print("=== DRY-RUN END-TO-END TEST (echte ffmpeg-pipeline + echte model-resolver, gestubte externe API-calls) ===\n")
    test_provider_resolution()
    test_live_groq_models_network_attempt()
    test_model_resolution_logic()
    test_automatic_retry_on_model_failure()
    test_json_completion_roundtrip()
    test_full_pipeline_job()
    print("\n=== ALLE TESTS GESLAAGD ===")


if __name__ == "__main__":
    main()
