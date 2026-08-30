"""
Controleert elke geproduceerde clip: bestaat, afspeelbaar, juiste duur/resolutie/
aspect ratio/audio. Foute clips worden gemarkeerd zodat de caller opnieuw kan
produceren met andere instellingen.
"""
from pathlib import Path

from app.models import ProducedClip, CampaignSettings
from app.pipeline import ffmpeg_utils


def check_clip(clip: ProducedClip, settings: CampaignSettings) -> ProducedClip:
    notes = []
    path = Path(clip.path)

    if not path.exists() or path.stat().st_size < 1024:
        clip.quality_check_passed = False
        clip.quality_check_notes = "Bestand ontbreekt of is te klein."
        return clip

    try:
        summary = ffmpeg_utils.video_summary(str(path))
    except ffmpeg_utils.FFmpegError as e:
        clip.quality_check_passed = False
        clip.quality_check_notes = f"Niet afspeelbaar / ffprobe-fout: {e}"
        return clip

    ok = True

    if summary["duration"] <= 0 or summary["duration"] > settings.max_duration + 3:
        ok = False
        notes.append(f"Duur buiten toegestane range: {summary['duration']:.1f}s")

    if settings.aspect_ratio == "9:16":
        w, h = summary["width"], summary["height"]
        if h == 0 or abs((w / h) - 9 / 16) > 0.03:
            ok = False
            notes.append(f"Aspect ratio klopt niet: {summary['resolution']}")

    if not summary["has_audio"]:
        ok = False
        notes.append("Geen audiotrack gevonden.")

    clip.quality_check_passed = ok
    clip.quality_check_notes = "; ".join(notes) if notes else "OK"
    clip.resolution = summary["resolution"]
    return clip
