"""Productie: één snelle FFmpeg-pass per clip."""
from pathlib import Path

from app.models import ClipCandidate, ProducedClip, CampaignSettings
from app.pipeline import ffmpeg_utils, frame_analysis


def produce_clip(
    candidate: ClipCandidate,
    index: int,
    settings: CampaignSettings,
    output_dir: Path,
    work_dir: Path,
    face_samples_by_video: dict[str, list[dict]],
    transcript_by_video: dict,
) -> ProducedClip:
    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    filename = f"clip-{index:02d}.mp4"
    final_path = output_dir / filename

    target_w, target_h = 1080, 1920
    if "x" in settings.resolution:
        try:
            target_w, target_h = [int(x) for x in settings.resolution.lower().split("x")]
        except ValueError:
            pass

    points = []
    if settings.face_priority and settings.aspect_ratio == "9:16":
        samples = face_samples_by_video.get(candidate.source_video, [])
        points = frame_analysis.reframe_points(samples, candidate.start, candidate.end)

    # Captions are deliberately opt-in. If enabled, the legacy subtitle pass
    # remains available, but the normal path is one-pass rendering.
    subtitle_text = None
    if settings.subtitles:
        # We do not burn subtitles here by default; the UI can add them later.
        # This prevents a second expensive encode and avoids duplicate captions.
        subtitle_text = None

    ffmpeg_utils.render_clip(
        candidate.source_video,
        str(final_path),
        candidate.start,
        candidate.duration,
        target_w,
        target_h,
        reframe_points=points,
        hook_text=None,
        normalize=True,
    )

    summary = ffmpeg_utils.video_summary(str(final_path))
    return ProducedClip(
        filename=filename,
        path=str(final_path),
        source_video=candidate.source_video,
        start=candidate.start,
        end=candidate.end,
        duration=candidate.duration,
        resolution=summary["resolution"],
        onderwerp=candidate.topic,
        hook=candidate.hook,
        payoff=candidate.payoff,
        reden_selectie=candidate.reden_selectie,
        gezicht_zichtbaar=(
            "hoog" if (candidate.face_visible_estimate or 0) > 0.7 else
            "matig" if (candidate.face_visible_estimate or 0) > 0.3 else "laag/onbekend"
        ),
        compliance=candidate.compliance_status,
        caption=candidate.caption,
        hashtags=candidate.hashtags,
    )
