"""
Centrale configuratie. Leest .env in en biedt sane defaults.
Alles hier is puur lokaal -- geen cloud-sandbox, geen verborgen services.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
FFPROBE_PATH = os.getenv("FFPROBE_PATH", "ffprobe")
YTDLP_PATH = os.getenv("YTDLP_PATH", "yt-dlp")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

# Groq is de standaard LLM-provider voor tekst-taken (briefing-extractie,
# clipselectie, compliance, captions) -- geen betaalde Anthropic API nodig.
# Anthropic is optioneel en kan per campagne gekozen worden, maar wordt
# automatisch vervangen door Groq als ANTHROPIC_API_KEY ontbreekt (de app
# crasht hier nooit op).
DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower() or "groq"
if DEFAULT_LLM_PROVIDER not in ("groq", "anthropic"):
    DEFAULT_LLM_PROVIDER = "groq"

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
GROQ_WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")
# Geen hardcoded default meer -- leeg betekent "automatisch bepalen via
# app.pipeline.llm_provider.resolve_groq_model()". Alleen gezet als de
# gebruiker in .env expliciet een model wil forceren.
GROQ_LLM_MODEL = os.getenv("GROQ_LLM_MODEL", "").strip()

DEFAULT_OUTPUT_DIR = os.getenv(
    "CLIPPER_OUTPUT_DIR",
    str(Path.home() / "ClipperOutput"),
)

API_PORT = int(os.getenv("API_PORT", "8756"))

# Harde grens voor bronvideo's. 60 minuten = 3600 seconden.
MAX_VIDEO_DURATION_SECONDS = 3600

# Maximum aantal clips dat gelijktijdig wordt gerenderd.
# 3 is een veilige default voor normale Windows-pc's.
CLIP_RENDER_WORKERS = max(1, int(os.getenv("CLIP_RENDER_WORKERS", "4")))


# Werkmap voor tussenbestanden (transcripties, frames, subs) per job.
WORK_DIR = Path(os.getenv("CLIPPER_WORK_DIR", str(Path.home() / "ClipperOutput" / "_work")))

# Waar gedownloade YouTube-bronnen origineel bewaard blijven (workspace per campagne).
DOWNLOADS_DIR = Path(os.getenv("CLIPPER_DOWNLOADS_DIR", str(Path.home() / "ClipperOutput" / "_downloads")))

# Job-state persistentie (zodat een herstart van de app lopende jobs niet kwijtraakt)
JOBS_DB_PATH = Path(os.getenv("CLIPPER_JOBS_DB", str(Path.home() / "ClipperOutput" / "_jobs.json")))
ACCOUNT_DB_PATH = Path(os.getenv("CLIPPER_ACCOUNT_DB", str(Path.home() / "ClipperOutput" / "_account.json")))

# Instellingen-defaults, kunnen per campagne worden overschreven via settings in de request
DEFAULT_SETTINGS = {
    "min_clips": 0,
    "max_clips": 50,
    "max_duration": 60,
    "preferred_min_duration": 15,
    "preferred_max_duration": 35,
    "aspect_ratio": "9:16",
    "resolution": "1080x1920",
    "subtitles": False,
    "hooks": True,
    "face_priority": True,
    "quality": "highest",
}

for p in (Path(DEFAULT_OUTPUT_DIR), WORK_DIR, DOWNLOADS_DIR, JOBS_DB_PATH.parent, ACCOUNT_DB_PATH.parent):
    p.mkdir(parents=True, exist_ok=True)


def check_config():
    """
    Geeft een lijst met ontbrekende/waarschuwende configuratiepunten terug.
    GROQ_API_KEY is altijd verplicht (Whisper-transcriptie + standaard LLM-
    provider). ANTHROPIC_API_KEY is optioneel: ontbreekt hij, dan wordt dat
    alleen als waarschuwing gemeld en schakelt de app zelf automatisch terug
    naar Groq voor tekst-taken -- dit blokkeert de app nooit.
    """
    warnings = []
    if not GROQ_API_KEY:
        warnings.append("GROQ_API_KEY ontbreekt -- transcriptie (Whisper) en de standaard "
                         "LLM-provider (Groq) werken niet zonder deze key.")
    if not ANTHROPIC_API_KEY:
        warnings.append("ANTHROPIC_API_KEY ontbreekt -- optioneel. Alleen nodig als je in de "
                         "instellingen expliciet voor provider 'anthropic' kiest; zonder key "
                         "schakelt de app dan automatisch terug naar Groq.")
    return warnings


def blocking_config_errors():
    """
    Subset van check_config() die een campagne écht moet blokkeren.
    ANTHROPIC_API_KEY ontbreekt is nooit blokkerend -- dat lost de app zelf
    op door terug te vallen op Groq.
    """
    return [w for w in check_config() if w.startswith("GROQ_API_KEY")]
