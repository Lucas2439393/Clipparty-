"""
Transcriptie via Groq Whisper met lokaal gecomprimeerde FLAC-chunks, zodat multipart-requests klein blijven.
"""
from pathlib import Path
from groq import Groq

from app.config import GROQ_API_KEY, GROQ_WHISPER_MODEL
from app.models import TranscriptSegment


def transcribe_audio(wav_path: str, offset: float = 0.0) -> list[TranscriptSegment]:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY ontbreekt -- kan niet transcriberen.")

    client = Groq(api_key=GROQ_API_KEY)
    with open(wav_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(Path(wav_path).name, f.read()),
            model=GROQ_WHISPER_MODEL,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    segments = []
    raw_segments = getattr(result, "segments", None) or result.get("segments", [])
    for seg in raw_segments:
        # groq client returns dict-like segments
        start = seg["start"] if isinstance(seg, dict) else seg.start
        end = seg["end"] if isinstance(seg, dict) else seg.end
        text = seg["text"] if isinstance(seg, dict) else seg.text
        segments.append(TranscriptSegment(start=float(start) + offset, end=float(end) + offset, text=text.strip()))
    return segments


def segments_to_srt(segments: list[TranscriptSegment], offset: float = 0.0) -> str:
    """Zet transcript-segmenten om naar een .srt string, met tijden relatief aan `offset`
    (gebruikt bij het genereren van ondertitels voor een geknipte subclip)."""
    def fmt(t: float) -> str:
        t = max(0.0, t)
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t - int(t)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    idx = 1
    for seg in segments:
        start = seg.start - offset
        end = seg.end - offset
        if end <= 0:
            continue
        start = max(0.0, start)
        lines.append(str(idx))
        lines.append(f"{fmt(start)} --> {fmt(end)}")
        lines.append(seg.text)
        lines.append("")
        idx += 1
    return "\n".join(lines)
