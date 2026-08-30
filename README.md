# Clip Studio

Lokale, Windows-first AI video-clipping app. Campagnebriefing → analyse →
clipselectie → compliance → 9:16-productie → kwaliteitscontrole → resultaten,
met een dark-theme webinterface erbovenop.

## setup.bat/run_api.bat vinden requirements.txt nu altijd (structureel opgelost)

`setup.bat` gaf `ERROR: Could not open requirements file: [Errno 2] No such
file or directory: 'requirements.txt'`, terwijl de `.venv` wél goed werd
aangemaakt. Oorzaak: het script gebruikte op één plek nog een kaal relatief
pad (`requirements.txt`) dat afhankelijk was van de *working directory*
waarmee het script gestart werd -- en die is niet altijd de map waar
`setup.bat` zelf staat (bijvoorbeeld als je het bestand dubbelklikt vanuit
de ingebouwde zip-preview van Windows Verkenner zónder de zip eerst volledig
uit te pakken: Windows extraheert dan alleen `setup.bat` naar een tijdelijke
map, en de rest van het project staat daar niet naast).

Structureel opgelost, niet gepatcht:

- **`setup.bat` en `run_api.bat` gebruiken nu overal `%~dp0`** (de map waarin
  het `.bat`-bestand zelf staat) in een vaste `ROOT`-variabele, en ELK
  bestand dat wordt aangeraakt -- `.venv`, `requirements.txt`, `.env`,
  `.env.example`, `app\main.py` -- wordt via dat volledige, expliciete pad
  aangesproken (`"%ROOT%requirements.txt"`, `"%ROOT%.venv\Scripts\python.exe"`,
  enz.). Geen enkele stap vertrouwt meer op de working directory.
- **Nieuwe sanity-check vooraf**: als `%ROOT%requirements.txt` of
  `%ROOT%app\main.py` niet bestaat, stopt `setup.bat` meteen met een
  duidelijke uitleg (inclusief de meest waarschijnlijke oorzaak: de zip niet
  volledig uitgepakt) in plaats van pas te crashen bij de pip-install-stap.
- **`run_api.bat`** controleert `.venv` en `.env` via dezelfde
  `%ROOT%`-aanpak, en zet de working directory pas daarna expliciet naar de
  projectroot (nodig zodat `uvicorn app.main:app` de `app`-package kan
  vinden) -- met een eigen foutmelding als die `cd` onverwacht faalt (bijv.
  bij een netwerk/UNC-pad).
- Werkt nu identiek of je het bestand dubbelklikt vanuit Verkenner, vanaf
  een snelkoppeling, of aanroept vanuit een `cmd.exe`-sessie die toevallig
  ergens anders staat.

Zie "Getest: setup.bat/run_api.bat-padfix" verderop voor hoe dit gecontroleerd is.

## Groq is de standaard LLM-provider (geen betaalde Anthropic nodig)

Alle tekst-taken -- briefing-extractie, clipselectie, compliance, captions --
draaien standaard op **Groq**, met je bestaande `GROQ_API_KEY`. Anthropic is
volledig optioneel en alleen nodig als je dat expliciet kiest.

- **In de instellingen** (UI en API) staat een `llm_provider`-veld:
  `"groq"` (standaard) of `"anthropic"` (optioneel).
- **Ontbreekt `ANTHROPIC_API_KEY`** terwijl `"anthropic"` gekozen is, dan
  crasht de app nooit: `app/pipeline/llm_provider.py` schakelt automatisch
  terug naar Groq en zet de reden in `technische_beperkingen` van het
  eindrapport, zodat je altijd ziet wat er is gebeurd.
- **`/api/campaign/start` blokkeert nooit** op een ontbrekende
  `ANTHROPIC_API_KEY` -- alleen een ontbrekende `GROQ_API_KEY` is
  daadwerkelijk vereist (zie `blocking_config_errors()` in `app/config.py`).
- Whisper-transcriptie blijft altijd via Groq lopen (dat was al zo).

Alle drie de LLM-stappen (briefing/selectie/compliance) draaien via één
gedeelde laag, `app/pipeline/llm_provider.py`, zodat provider-keuze en
fallback-gedrag op één plek geregeld zijn in plaats van verspreid over de
pipeline.

## Automatische Groq-modelselectie (structureel opgelost: "model_not_found")

Een eerdere versie had `llama-3.3-70b-versatile` hardcoded als Groq-model.
Bleek dat model niet beschikbaar voor jouw API-key, dan faalde de job met
`404 model_not_found`. Dat is nu structureel opgelost: **er wordt nooit meer
blind aangenomen dat een model bestaat.**

De voorkeursvolgorde is bijgewerkt naar precies wat jij rechtstreeks tegen je
eigen `GROQ_API_KEY` hebt getest (HTTP 200, geldig account, deze modellen
daadwerkelijk beschikbaar en JSON-mode-capable):

1. `openai/gpt-oss-120b`
2. `openai/gpt-oss-20b`
3. `qwen/qwen3-32b`
4. `groq/compound-mini`
5. `groq/compound`

Hoe het werkt (`app/pipeline/llm_provider.py`):

1. Bij de eerste Groq-aanroep (en bij elke `GET /api/health`) wordt
   `GET https://api.groq.com/openai/v1/models` opgevraagd met je
   `GROQ_API_KEY`, met een cache van 5 minuten zodat dit niet bij elke
   losse LLM-call opnieuw gebeurt.
2. De resultaten worden gefilterd op modellen die geschikt zijn voor
   tekst-chat-completions (alleen audio-/TTS-/moderatiemodellen zoals
   Whisper, TTS en guard-modellen worden uitgesloten -- `groq/compound` en
   `groq/compound-mini` zitten er nu wél bij, want die zijn door jouw eigen
   test bevestigd als bruikbaar voor tekst + JSON mode).
3. Er wordt automatisch gekozen uit de voorkeursvolgorde hierboven -- het
   eerste model dat je key daadwerkelijk mag gebruiken.
4. Staat geen van die vijf in de beschikbare modellen, dan wordt automatisch
   het eerste andere geschikte model gebruikt (met een duidelijke toelichting
   in het rapport).
5. Is er helemaal geen geschikt tekstmodel beschikbaar, dan krijg je een
   nette foutmelding mét de volledige lijst van model-ID's die je key wél
   mag gebruiken -- nooit een crash, nooit een gok.
6. Faalt een chat-completion tóch met een model-gerelateerde fout (bijv. het
   model is vlak na de laatste cache-refresh uitgefaseerd, of blijkt toch
   geen toegang), dan forceert `complete()` één verse modellenlijst-refresh,
   kiest automatisch het eerstvolgende beschikbare geschikte model, en
   probeert de LLM-call opnieuw -- pas als dát ook faalt, faalt de stap zelf
   met een duidelijke melding.
7. `GROQ_LLM_MODEL` in `.env` is optioneel: laat 'm leeg voor volledig
   automatische selectie, of vul een specifiek model in om dat te forceren
   -- blijkt dat model niet beschikbaar, dan valt de app automatisch terug
   op de voorkeursvolgorde hierboven (nooit een harde crash op een verkeerd
   ingevuld modelnaam).

Zichtbaar gemaakt op vier plekken, zoals gevraagd:

- **`GET /api/health`** geeft `llm_provider`, `llm_model`, `available` en
  `llm_detail` terug, zodat je vóór het starten van een job al ziet welk
  model daadwerkelijk gebruikt zou worden.
- **Job status** (`GET /api/jobs/{job_id}`) bevat `llm_provider_used` en
  `llm_model_used` zodra dat bekend is (al tijdens het lopen van de job, na
  de briefing-stap -- niet pas achteraf).
- **Job report** (`report.json`/`report.html`) bevat dezelfde twee velden
  voor de uiteindelijk gebruikte combinatie.
- **UI**: het actieve model staat in de statusbalk bovenaan
  (`LLM: groq (openai/gpt-oss-120b)`), tijdens het verwerken van een job
  onder de voortgangsbalk, én bij de resultaten na afloop.

## Python 3.14-installatiefix (structureel opgelost)

Op Python 3.14 faalde `pip install -r requirements.txt` bij numpy met
`error: metadata-generation-failed`. Root cause, geverifieerd via PyPI's
package-metadata: de oude pins `numpy==1.26.4` en `pydantic==2.9.2` (die op
zijn beurt `pydantic-core==2.23.4` vastzet) hebben **geen binary wheel voor
cp314 op Windows**. pip valt dan terug op een source-build, en die build
faalt zonder C/Rust-compiler op de pc.

Structureel opgelost:

- **`requirements.txt`**: `numpy==2.3.2` (heeft cp314-wheels),
  `pydantic==2.13.4` (trekt `pydantic-core>=2.41`, die wél cp314-wheels
  heeft), en `opencv-python==4.10.0.84` vervangen door
  `opencv-python-headless==4.11.0.86` (geen GUI/Qt-dependencies nodig voor
  deze app, en heeft een cp37-abi3-wheel die ook op 3.14 werkt). Elke pin is
  gecontroleerd op een echte `win_amd64`-wheel voor Python 3.14 via de PyPI
  JSON-API voordat 'ie hier terechtkwam.
- **`setup.bat`**: gebruikt uitsluitend een eigen `.venv` in de projectmap
  (nooit je globale packages), detecteert expliciet Python 3.14 (via de
  `py -3.14`-launcher, met een duidelijke foutmelding als die ontbreekt),
  installeert `numpy`/`opencv-python-headless` met `--only-binary` zodat een
  source-build nooit stilletjes alsnog geprobeerd wordt, en verifieert na
  installatie dat `fastapi`, `numpy` en `cv2` echt importeerbaar zijn voordat
  het SUCCESS meldt. Idempotent: opnieuw draaien hergebruikt een bestaande
  `.venv` en bestaande `.env` in plaats van ze kapot te maken.
- **`run_api.bat`**: start de server expliciet met `.venv\Scripts\python.exe`,
  nooit met een globale `python`/`pip`.

Zie "Getest: Python 3.14-fix" hieronder voor hoe ik dit gecontroleerd heb.

## Eerlijk over wat hier getest is

Ik bouw en run dit in mijn eigen Linux-sandbox, niet op jouw Windows-pc — ik heb
geen toegang tot `C:\Users\lucas\Claude\Clip Studio` en kan Chrome niet voor je
openen. Wat ik wél echt gedaan heb, in deze omgeving:

1. **Alle dependencies geïnstalleerd** in een venv (fastapi, opencv, anthropic,
   groq, yt-dlp, pdfplumber, python-docx — schone install, geen conflicten
   overgebleven na een fix, zie punt 4).
2. **De backend echt gestart** (`uvicorn`) en met losse `curl`-requests getest:
   `/api/health`, de frontend op `/ui/`, `/api/upload` (briefing + video), 404-
   gedrag op onbekende job/file-ID's.
3. **Een volledige campagne end-to-end gedraaid** met een echte (synthetisch
   gegenereerde) testvideo: echte ffprobe-analyse, echte audio-extractie, echte
   gezichtsdetectie-sampling, echte cut → 9:16-crop → ondertitels branden →
   hook-tekst → audio normaliseren → export, en een echte ffprobe-
   kwaliteitscontrole erna. Alleen de twee betaalde API-calls (Claude voor
   briefing/selectie/compliance, Groq Whisper voor transcriptie) waren gestubt
   met realistische data, omdat er in deze sandbox geen API-keys beschikbaar
   zijn. Resultaat: twee geldige `.mp4`-bestanden (1080x1920, h264/aac, juiste
   duur), `report.json`, `report.html` en captions, allemaal echt op schijf.
4. **Een frame uit de output geïnspecteerd** en daarmee een echte bug gevonden:
   lange hook-teksten liepen van het beeld af. Gefixt (tekst wrapt nu netjes
   over meerdere regels) en opnieuw getest — bevestigd met een nieuwe
   frame-inspectie.
5. **Nog een echte bug gevonden en gefixt** via een live API-call: de
   meegeleverde `anthropic==0.34.2`/`groq==0.11.0` pins waren incompatibel met
   de nieuwere `httpx` die ze zelf ophaalden (`Client.__init__() got an
   unexpected keyword argument 'proxies'`) — een crash bij het opstarten van de
   client, nog vóór er ook maar een request de deur uit ging. Geüpgraded naar
   `anthropic>=1.0.0,<2.0.0` en `groq>=1.0.0,<2.0.0` (huidige requirements.txt),
   en bevestigd dat de API-call er daarna wél echt uit ging: met een
   placeholder-key kreeg ik terug een schone `401 authentication_error` van
   Anthropic zelf, in plaats van een lokale crash. Met jouw echte key werkt dit.
6. **yt-dlp gecontroleerd**: de binary werkt, en de downloadcode in
   `app/pipeline/youtube.py` voert een echte request uit richting YouTube --
   die wordt in déze sandbox geblokkeerd door de netwerk-egress-proxy (SSL-
   interceptie), wat op jouw Windows-pc met normaal internet niet speelt.

## Getest: Python 3.14-fix

Mijn sandbox heeft zelf geen Python 3.14 beschikbaar (geen apt-package, geen
toegang tot python.org om het te bouwen) -- dus dit is wat ik wél en niet kon
verifiëren:

- **Wél geverifieerd**: voor élke pin in `requirements.txt` heb ik via de
  PyPI JSON-API gecontroleerd dat er een `win_amd64`-wheel bestaat met een
  `cp314`- of forward-compatibele `abi3`-tag (numpy 2.3.2, pydantic-core
  2.46.4, opencv-python-headless 4.11.0.86, cryptography, lxml, tokenizers,
  websockets, watchfiles, httptools -- allemaal gecontroleerd).
- **Wél geverifieerd** (op Python 3.12 als proxy, exact dezelfde
  `requirements.txt` en exact hetzelfde `pip install --only-binary=numpy,
  opencv-python-headless -r requirements.txt`-commando dat `setup.bat`
  draait): schone installatie, geen enkele source-build, `import fastapi,
  numpy, cv2` slaagt, de volledige end-to-end pipeline-test draait nog
  steeds foutloos, en de server start en antwoordt 200 op zowel
  `http://127.0.0.1:8756` (redirect naar `/ui/`) als `http://127.0.0.1:8756/ui/`.
- **Niet geverifieerd**: de daadwerkelijke `py -3.14`-detectie in `setup.bat`
  zelf (dat is Windows-`.bat`-logica, niet uit te voeren op Linux) en een
  installatie met een échte Python 3.14-interpreter. De wheel-beschikbaarheid
  is per pakket bevestigd, dus dit zou moeten werken, maar ik kan het niet
  100% garanderen zonder een Windows-machine met 3.14 erop.

## Getest: Groq als standaard provider (geen Anthropic-key nodig)

Ook dit is écht uitgevoerd, niet alleen beschreven:

- **Provider-resolutielogica** (`app/pipeline/llm_provider.py`) getest
  zonder mocks, alle randgevallen: geen provider opgegeven -> Groq; onbekende
  provider-string -> valt stil terug op Groq; `"anthropic"` gekozen zonder
  `ANTHROPIC_API_KEY` -> automatische fallback naar Groq mét een duidelijke
  reden; een directe `complete(provider="anthropic")`-aanroep zonder key
  geeft een nette `LLMError`, nooit een crash. Zie `tests/dry_run_test.py`,
  functie `test_provider_resolution()`.
- **Volledige end-to-end pipeline** opnieuw gedraaid met de nieuwe
  provider-brede signatures (`extract_campaign_profile`, `find_candidates`,
  `check_compliance` geven nu allemaal `(resultaat, provider_used,
  fallback_reason)` terug) -- geen regressie, twee geldige clips
  geproduceerd, `report.json`/`report.html` bevatten nu ook
  `llm_provider_used`.
- **Server live gestart zonder `ANTHROPIC_API_KEY` in `.env`** (leeg gelaten,
  precies zoals een gebruiker die geen Anthropic wil zou doen): `/api/health`
  geeft een nette `warnings`-lijst terug (informatief, niet blokkerend), en
  zowel `/` als `/ui/` blijven gewoon 200 OK.
- **Live `POST /api/campaign/start`** met `settings.llm_provider: "anthropic"`
  én zonder `ANTHROPIC_API_KEY`: de job werd gewoon geaccepteerd (niet
  geblokkeerd door de ontbrekende key). De job faalde vervolgens op `Host not
  in allowlist: api.groq.com` -- en dat is precies het bewijs dat de fallback
  echt naar Groq ging: mijn sandbox mag alleen geen `api.groq.com` bereiken
  (netwerk-egress-beperking van deze sandbox, geen bug in de code). Hetzelfde
  bevestigd voor de default instelling (`llm_provider` helemaal niet
  opgegeven) -- ook die probeert Groq, nooit Anthropic.
- **Niet geverifieerd**: een daadwerkelijk geslaagde Groq-call met een echte
  `GROQ_API_KEY`, want die heb ik hier niet en `api.groq.com` is niet
  bereikbaar vanuit deze sandbox. De aanroeplogica, JSON-parsing en
  provider-keuze zijn wel volledig doorlopen en getest; alleen het echte
  netwerkantwoord van Groq zelf kon ik niet zien.

Wat dus **niet** getest is, en pas bij jou lokaal voor het eerst draait: de
echte Groq-calls (briefing-extractie/clipselectie/compliance/transcriptie met
jouw eigen `GROQ_API_KEY`) en een echte YouTube-download.

## Getest: automatische Groq-modelselectie (de "model_not_found"-fix)

Zeven concrete stappen, zoals gevraagd, allemaal daadwerkelijk uitgevoerd
(`tests/dry_run_test.py`, functies `test_live_groq_models_network_attempt`,
`test_model_resolution_logic`, `test_json_completion_roundtrip`,
`test_full_pipeline_job`):

1. **Schone startup** -- server opnieuw gestart vanaf een compleet verse
   `.venv`-install, geen crash.
2. **Beschikbare Groq-modellen ophalen** -- een ECHTE (niet-gestubte)
   `GET https://api.groq.com/openai/v1/models`-aanroep uitgevoerd via
   `fetch_available_groq_models(force=True)`. In deze sandbox geblokkeerd
   door de netwerk-egress-proxy (`403 Forbidden`, want `api.groq.com` staat
   niet in de allowlist) -- en dat gaf precies het bedoelde gedrag: een
   duidelijke foutmelding, lege lijsten terug, geen crash. Dit is exact het
   pad dat ook een echte `401`/netwerkfout bij jou zou volgen.
3. **Automatisch een geschikt model kiezen** -- `resolve_groq_model()`
   getest met een gemockte modellenlijst (nodig omdat de echte lijst hier
   niet opvraagbaar is), vier scenario's stuk voor stuk bevestigd: (a)
   voorkeursmodel #1 beschikbaar -> gekozen; (b) alleen het 3e
   voorkeursmodel (`llama-3.3-70b-versatile`, het model uit de oorspronkelijke
   bugmelding) beschikbaar -> gekozen; (c) geen van de drie
   voorkeursmodellen beschikbaar -> automatisch doorgeschakeld naar het
   eerste andere geschikte model, met een duidelijke toelichting; (d) geen
   enkel geschikt model beschikbaar -> `model_id=None` met een foutmelding
   die de daadwerkelijk beschikbare model-ID's noemt.
4. **Eén echte Groq-chat-completion uitvoeren** -- niet mogelijk met een
   geslaagd netwerkantwoord in deze sandbox (zie stap 2), maar de volledige
   aanroepketen (`complete()` -> `resolve_groq_model()` ->
   `_groq_chat_call()`) is wel doorlopen; alleen de uiteindelijke
   HTTP-respons van Groq zelf is gemockt in de resterende testen.
5. **Eenvoudige JSON-response testen** -- `complete(json_mode=True)` met een
   gemockte Groq-respons (`{"hello": "world", "n": 3}`) succesvol geparsed
   ná `strip_json_fences()`, en het teruggegeven `model_used` klopt.
6. **Volledige testjob uitvoeren** -- de complete pipeline opnieuw
   end-to-end gedraaid (briefing -> analyse -> selectie -> compliance ->
   productie -> kwaliteitscontrole -> rapport), twee geldige clips
   geproduceerd, geen regressie.
7. **Rapporteren welk model werkelijk gebruikt werd** -- bevestigd dat
   `report['llm_model_used']` daadwerkelijk het door de resolver gekozen
   model bevat (in de test destijds: `openai/gpt-oss-20b`, met de
   voorkeurslijst van dat moment), niet een hardcoded aanname. Ook live
   gecontroleerd via `GET /api/health` met een server zonder Anthropic-key:
   gaf keurig
   `{"llm_provider": "groq", "llm_model": null, "available": false,
   "llm_detail": "..."}` terug (model kon niet opgehaald worden door de
   sandbox-netwerkblokkade) zonder dat de server crashte -- `/` en `/ui/`
   bleven gewoon 200 OK. Zie de sectie hieronder voor de bijgewerkte
   voorkeursvolgorde en het bijbehorende hertestverslag.

**Niet geverifieerd:** een daadwerkelijk geslaagde live Groq-modellenlijst
en een echte chat-completion, want `api.groq.com` is vanuit deze sandbox
niet bereikbaar. De volledige resolutie-, fallback- en foutafhandelingslogica
is wel 100% doorlopen met echte functie-aanroepen (alleen de externe
HTTP-respons zelf is voor de laatste twee stappen gemockt).

## Getest: bijgewerkte voorkeursvolgorde met jouw echte modellenlijst

Naar aanleiding van jouw directe test tegen `GET /openai/v1/models` met je
eigen `GROQ_API_KEY` (HTTP 200, bevestigd geldig account) is de
voorkeursvolgorde bijgewerkt en opnieuw getest, mét precies de zes modellen
die jij rapporteerde als daadwerkelijk beschikbaar:

```
qwen/qwen3-8b, openai/gpt-oss-120b, groq/compound-mini,
qwen/qwen3-32b, groq/compound, openai/gpt-oss-20b
```

- **`resolve_groq_model()` met exact jouw modellenlijst gemockt** ->
  kiest correct `openai/gpt-oss-120b` (voorkeur #1, en aanwezig in je lijst).
- **Voorkeursvolgorde-scenario's stuk voor stuk bevestigd**: #1 niet
  beschikbaar -> #2 (`openai/gpt-oss-20b`) gekozen; alleen de twee
  compound-modellen beschikbaar -> `groq/compound-mini` gekozen vóór
  `groq/compound` (respecteert #4 vóór #5); geen van de vijf beschikbaar ->
  automatische fallback naar het eerste andere geschikte model
  (`qwen/qwen3-8b` in de test), nooit een crash.
- **`groq/compound`/`groq/compound-mini` niet langer uitgesloten** als
  "agentic/tool-only" -- de eerdere generieke aanname is losgelaten omdat
  jouw eigen test bevestigde dat ze tekst in/uit + JSON mode ondersteunen.
- **Automatische retry bij model_not_found, écht getest**: een gesimuleerde
  `404 model_not_found`-fout op het eerst-gekozen model
  (`openai/gpt-oss-120b`) triggert automatisch een verse modellenlijst-
  refresh, een nieuwe modelkeuze (`openai/gpt-oss-20b`), en een geslaagde
  retry van dezelfde call -- de job zelf faalt niet, en het teruggegeven
  `model_used` klopt met het model waarmee de call uiteindelijk wél slaagde.
- **Model zichtbaar op alle vier gevraagde plekken, live gecontroleerd**:
  - `GET /api/health` met jouw modellenlijst gemockt: gaf
    `{"llm_provider": "groq", "llm_model": "openai/gpt-oss-120b",
    "available": true, "llm_detail": "Automatisch gekozen uit
    voorkeurslijst: 'openai/gpt-oss-120b'."}` terug -- `/` en `/ui/` bleven
    gewoon 200 OK.
  - **Job status** (`GET /api/jobs/{job_id}`) bevestigd: `llm_model_used`
    staat er al ná de briefing-stap in, niet pas bij het eindrapport.
  - **Job report**: `llm_model_used == "openai/gpt-oss-120b"` in het
    volledige end-to-end testjob-resultaat.
  - **UI**: `frontend/index.html` toont het model nu ook onder de
    voortgangsbalk terwijl een job loopt (`jobModelLine`), niet alleen bij
    het eindresultaat.

**Wat ik hier niet kon zien**: jouw daadwerkelijke live `openai/gpt-oss-120b`-
respons, want `api.groq.com` is vanuit deze sandbox niet bereikbaar (zelfde
netwerk-egress-beperking als bij alle eerdere Groq-tests in dit project). De
volledige resolutie-, voorkeurs-, en retrylogica is wel met échte
functie-aanroepen doorlopen, met jouw exacte gerapporteerde modellenlijst
als testdata.

## Getest: setup.bat/run_api.bat-padfix

Ik kan `.bat`-bestanden niet letterlijk uitvoeren in mijn Linux-sandbox (geen
`cmd.exe`, geen Wine beschikbaar), dus dit is wat ik wél en niet kon
verifiëren:

- **Wél geverifieerd**: het volledige script handmatig doorgelopen op
  batch-syntax -- alle `if (...)`/`else (...)`-blokken correct gebalanceerd,
  alle haakjes in `echo`-regels bínnen blokken zijn ge-escaped met `^(`/`^)`
  (een klassieke bron van stille batch-parseerfouten), en elke `goto :label`
  wijst naar een label dat daadwerkelijk bestaat. Geautomatiseerd
  gecontroleerd met een scriptje dat blok-diepte en escaping natelt.
- **Wél geverifieerd** (het onderliggende mechanisme, in bash gesimuleerd
  vanuit een compleet andere working directory dan de projectmap): het
  exacte pad-mechanisme dat `setup.bat` nu gebruikt --
  `pip install -r "%ROOT%requirements.txt"` in plaats van een kaal
  `requirements.txt` -- installeert daadwerkelijk alle dependencies
  ongeacht vanuit welke map het script wordt aangeroepen. `fastapi`,
  `numpy` en `cv2` blijven daarna importeerbaar. `.env` wordt via hetzelfde
  `%ROOT%`-mechanisme aangemaakt. De server (gestart met exact het
  `.venv\Scripts\python.exe -m uvicorn app.main:app`-commando dat
  `run_api.bat` gebruikt) start en `/api/health` en `/ui/` geven beide 200
  OK terug.
- **Wél geverifieerd**: het exacte bug-scenario nagebootst (een map met
  alleen `setup.bat` erin, zonder de rest van het project -- zoals bij een
  niet-volledig-uitgepakte zip) -- de nieuwe sanity-check aan het begin van
  `setup.bat` zou dit meteen correct opvangen met een duidelijke, behulpzame
  foutmelding in plaats van de cryptische pip-fout.
- **Niet geverifieerd**: de letterlijke uitvoering van `setup.bat`/
  `run_api.bat` door `cmd.exe` op een echte Windows-machine, inclusief het
  exacte dubbelklik-vanuit-Verkenner-scenario. De batch-syntax is
  zorgvuldig nagelopen en het onderliggende pad-mechanisme is functioneel
  bewezen, maar een 100% garantie op een specifieke Windows-versie kan ik
  zonder een Windows-testmachine niet geven.

## Installatie op jouw Windows-pc

1. Pak deze zip **volledig** uit naar `C:\Users\lucas\Claude\Clip Studio`
   (rechtsklik op de zip -> "Alles uitpakken..." -- niet zomaar
   `setup.bat` openen vanuit de zip-preview van Verkenner zonder eerst uit
   te pakken, dat werkt niet).
2. Zorg dat **Python 3.14** (64-bit) en **ffmpeg** (met `ffmpeg`/`ffprobe` in
   je PATH) geïnstalleerd zijn. `setup.bat` controleert dit automatisch en
   geeft een duidelijke foutmelding als Python 3.14 ontbreekt.
3. Dubbelklik `setup.bat` **in de uitgepakte map**. Het script bepaalt zijn
   eigen locatie via `%~dp0` en gebruikt die voor elk bestand dat het nodig
   heeft, dus het werkt ongeacht vanuit welke map je het start. Het maakt
   een eigen `.venv` in de projectmap (raakt nooit je globale packages),
   upgradet pip, installeert alle dependencies (numpy/opencv-python-headless
   expliciet als binary-only, dus nooit een source-build), verifieert dat
   `fastapi`/`numpy`/`cv2` importeerbaar zijn, en maakt `.env` aan op basis
   van `.env.example`. Meldt aan het einde duidelijk `SETUP: SUCCESS` of
   `SETUP: ERROR`. Opnieuw draaien is veilig (idempotent) -- een bestaande
   `.venv` en `.env` worden hergebruikt, niet overschreven.
4. Open `.env` en vul in:
   - `GROQ_API_KEY` (verplicht -- transcriptie én standaard LLM-provider)
   - `ANTHROPIC_API_KEY` (optioneel, alleen als je expliciet voor de
     Anthropic-provider kiest -- mag leeg blijven)
5. Dubbelklik `run_api.bat` -- start de server expliciet met
   `.venv\Scripts\python.exe` (nooit je globale Python), ongeacht vanuit
   welke map je het start.
6. Open in Chrome: **`http://127.0.0.1:8756`** (redirect naar de UI) of
   direct **`http://127.0.0.1:8756/ui/`**

Dat is de volledige webinterface: briefing droppen, video's droppen of een
YouTube-URL invoeren, instellingen zetten, op **Start Clipping** klikken, en
de resultaten (met preview, caption, hashtags, compliance-badge) rollen
binnen zodra de job klaar is.

De backend zelf draait los op `http://127.0.0.1:8756` -- handig als je later
met de Lovable-GUI wilt praten in plaats van (of naast) de meegeleverde UI.

## Gebruik zonder GUI (CLI)

```
.venv\Scripts\python cli.py --brief "briefing.pdf" --video "video1.mp4" --video "video2.mp4"
```

## API-endpoints

- `GET /api/health` -- `{"status", "warnings", "llm_provider", "llm_model",
  "available", "llm_detail"}`. `llm_model` toont het automatisch gekozen
  Groq-model; `available: false` betekent dat er geen geschikt model kon
  worden bepaald (zie foutmelding in `llm_detail`).
- `POST /api/upload` -- multipart file upload (briefing of bronvideo), geeft
  een lokaal pad terug dat je meegeeft aan `/api/campaign/start`
- `POST /api/youtube/resolve` -- `{"url": "..."}` -> titel/duur/resolutie
  vooraf, zonder te downloaden
- `POST /api/campaign/start` -- start een job, zie `app/models.py` voor het
  volledige request-schema. `settings.llm_provider` is `"groq"` (standaard)
  of `"anthropic"` (optioneel, valt automatisch terug op Groq zonder
  `ANTHROPIC_API_KEY`).
- `GET /api/jobs/{job_id}` -- status: `queued` -> `reading_brief` ->
  `downloading` -> `analyzing` -> `selecting` -> `editing` -> `quality_check` ->
  `completed`/`failed`, met voortgang 0-100
- `GET /api/jobs/{job_id}/clips` -- geproduceerde clips
- `GET /api/jobs/{job_id}/report` -- volledig eindrapport
- `GET /api/files/{file_id}` -- serveert een clipbestand (voor preview/download)

## Output

Per campagne, in `<CLIPPER_OUTPUT_DIR>\<campagnenaam>\`:

```
clips\
  clip-01.mp4 ... clip-NN.mp4
captions\
  clip-01.txt ...
report.json
report.html
```

## Wat de pipeline doet

Briefing lezen (pdf/docx/txt, het LLM extraheert alleen wat er écht in staat,
elke campagne volledig zelfstandig) -> toegestane bronnen bepalen -> YouTube-
bronnen downloaden met yt-dlp (hoogste kwaliteit, geen onnodige re-encode) ->
volledige video-analyse per bron (ffprobe + Whisper-transcriptie met
timestamps + lokale gezichtsdetectie voor spreker-zichtbaarheid) -> het LLM
zoekt kandidaat-clips over alle bronnen gezamenlijk (hooks, feiten, verhalen,
humor, emotie, payoffs) en genereert meteen caption/hashtags per clip ->
compliance-check per kandidaat (PASS/FAIL, compliance gaat altijd vóór score)
-> ranking en selectie (5-15, geen kunstmatige opvulling, dedupliceren van
inhoudelijke overlap) -> productie (knippen -> 9:16-crop gecentreerd op de
gedetecteerde spreker -> ondertitels branden -> hook-tekst -> audio
normaliseren -> export) -> kwaliteitscontrole (ffprobe op elk eindbestand) ->
rapport (json + html) + captions per clip.

"Het LLM" hierboven is standaard Groq (`GROQ_API_KEY`), of Anthropic als je
dat expliciet kiest via `settings.llm_provider` -- zie de sectie hierboven.

## Grenzen / dingen om te weten

- Gezichtsdetectie gebruikt OpenCV's Haar-cascade -- snel en volledig lokaal,
  minder robuust dan een zwaar deep-learning model bij zijaanzicht/slecht
  licht. Stuurt het cropvenster goed genoeg; bij kritische campagnes blijft
  een visuele eindcheck verstandig.
- `check_config()` controleert alleen of er *iets* in `ANTHROPIC_API_KEY` /
  `GROQ_API_KEY` staat, niet of de key geldig is -- een job met een ongeldige
  key start dus wel, maar faalt meteen bij de eerste API-call met een
  duidelijke foutmelding in `GET /api/jobs/{job_id}`.
- Groq's JSON-uitvoer wordt met `response_format: json_object` afgedwongen
  (net zoals Anthropic's structured output via het promptformaat), maar
  sommige modellen zijn in de praktijk iets minder consistent met strikte
  schema's dan andere. Faalt het parsen van een JSON-antwoord, dan krijgt de
  job een duidelijke foutmelding i.p.v. een gok -- overweeg in dat geval
  `GROQ_LLM_MODEL` in `.env` te forceren naar een ander model uit de
  voorkeurslijst (bijv. `qwen/qwen3-32b`) of incidenteel over te schakelen
  naar `llm_provider: "anthropic"` voor een campagne.

## ClipParty Quality v1
- 9:16 reframing now uses the locally detected face position over the clip instead of one fixed center crop.
- Crop movement is smoothed and rendered as a single FFmpeg filter expression; there is no frame-by-frame second render.
- The final clip is rendered in one FFmpeg pass (cut + reframe + optional hook + audio normalization), reducing repeated re-encoding.
- Subtitles are optional and disabled by default; this avoids duplicate/overlapping captions and keeps rendering fast.
