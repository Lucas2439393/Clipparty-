"""
YouTube-bronnen via yt-dlp -- de enige externe downloadstap in de pipeline.
Downloadt de hoogst beschikbare bruikbare kwaliteit, vermijdt onnodige
re-encoding tijdens download (stream download + remux, geen re-encode), en
bewaart de originele bron in de campagne-workspace zodat 'ie niet opnieuw
gedownload hoeft te worden.
"""
import json
import subprocess
from pathlib import Path

from app.config import YTDLP_PATH, DOWNLOADS_DIR


class YtDlpError(RuntimeError):
    pass


def is_youtube_url(value: str) -> bool:
    return value.startswith(("http://", "https://")) and (
        "youtube.com" in value or "youtu.be" in value
    )


def resolve_metadata(url: str) -> dict:
    """Haalt titel/duur/resolutie op zonder te downloaden -- voor POST /api/youtube/resolve."""
    cmd = [YTDLP_PATH, "--dump-json", "--no-playlist", url]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise YtDlpError(proc.stderr.strip() or "yt-dlp kon de URL niet resolven.")
    info = json.loads(proc.stdout.splitlines()[0])
    return {
        "title": info.get("title"),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "resolution": f"{info.get('width')}x{info.get('height')}" if info.get("width") else None,
        "id": info.get("id"),
        "webpage_url": info.get("webpage_url", url),
    }


def download_source(url: str, campaign_workspace: Path) -> str:
    """
    Downloadt de hoogst beschikbare bruikbare kwaliteit (video+audio gemuxed,
    geen re-encode) naar de campagne-workspace. Retourneert het lokale pad.
    Als het bestand al eerder gedownload is voor deze campagne, wordt de
    bestaande lokale kopie hergebruikt.
    """
    campaign_workspace.mkdir(parents=True, exist_ok=True)
    out_template = str(campaign_workspace / "%(id)s.%(ext)s")

    cmd = [
        YTDLP_PATH,
        "-f", "bv*+ba/b",       # beste video+audio, remux zonder re-encode waar mogelijk
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", out_template,
        "--print", "after_move:filepath",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise YtDlpError(proc.stderr.strip() or "yt-dlp download mislukt.")

    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        raise YtDlpError("yt-dlp gaf geen bestandspad terug na download.")
    return lines[-1]
