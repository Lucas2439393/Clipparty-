from pathlib import Path

from app.models import VideoAnalysis
from app.config import MAX_VIDEO_DURATION_SECONDS
from app.pipeline import ffmpeg_utils, transcription, frame_analysis


def analyze_video(video_path: str, work_dir: Path) -> tuple[VideoAnalysis, list[dict]]:
    """
    Volledige analyse van één bronvideo:
    - ffprobe voor technische info
    - audio-extractie + Whisper-transcriptie (volledige transcriptie, met timestamps)
    - periodieke frame-sampling voor gezicht/spreker-zichtbaarheid

    Retourneert (VideoAnalysis, ruwe face-samples) -- de ruwe samples worden later
    hergebruikt om per kandidaat-clip het cropvenster te bepalen.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(video_path).stem

    summary = ffmpeg_utils.video_summary(video_path)
    if summary["duration"] > MAX_VIDEO_DURATION_SECONDS + 0.01:
        raise ValueError(
            f"Video is te lang: maximaal 60 minuten toegestaan (aangeleverd: "
            f"{summary['duration'] / 60:.1f} minuten)."
        )

    # Nooit een 60-minuten WAV als één Groq-upload maken. Audio blijft lokaal
    # opgesplitst; alleen de kleine chunks worden naar Whisper gestuurd.
    chunks_dir = work_dir / f"{stem}_audio_chunks"
    segments = []
    for wav_path, offset in ffmpeg_utils.extract_audio_chunks(
        video_path, chunks_dir, chunk_seconds=480
    ):
        segments.extend(transcription.transcribe_audio(wav_path, offset=offset))
    segments.sort(key=lambda s: (s.start, s.end))
    # Face analysis is intentionally deferred until after clip selection.
    # Scanning a full 60-minute source here was a major latency bottleneck.
    face_samples = []
    visibility = 0.0

    analysis = VideoAnalysis(
        source_path=str(video_path),
        duration=summary["duration"],
        resolution=summary["resolution"],
        fps=summary["fps"],
        codec=summary["codec"],
        has_audio=summary["has_audio"],
        transcript=segments,
        face_visibility_ratio=visibility,
        sampled_frame_times=[s["time"] for s in face_samples],
    )
    return analysis, face_samples
