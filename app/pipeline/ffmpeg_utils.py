"""
Dunne, betrouwbare wrappers rond de lokale ffmpeg/ffprobe binaries.
Geen cloud-encoding, geen externe render-services -- alles draait op de
Windows-pc van de gebruiker via de binaries die al werken voor ffmpeg-clipper.
"""
import json
import subprocess
from pathlib import Path
from typing import Optional

from app.config import FFMPEG_PATH, FFPROBE_PATH

# ClipParty ships its own font so FFmpeg never depends on the user's
# Windows Fontconfig installation. This is important for drawtext on Windows.
BUNDLED_FONT = Path(__file__).resolve().parents[2] / "fonts" / "DejaVuSans.ttf"

def _filter_path(path: str | Path) -> str:
    """Escape a filesystem path for use inside an FFmpeg filter argument."""
    return str(path).replace("\\", "/").replace(":", "\\:")

def _drawtext_fontfile() -> str:
    if not BUNDLED_FONT.exists():
        raise FFmpegError(f"ClipParty font ontbreekt: {BUNDLED_FONT}")
    return _filter_path(BUNDLED_FONT)


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"Command failed ({' '.join(cmd)}):\n{proc.stderr}")
    return proc


def probe(path: str) -> dict:
    """Volledige ffprobe-info als dict. Gooit FFmpegError als het bestand niet leesbaar is."""
    cmd = [
        FFPROBE_PATH, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = _run(cmd)
    return json.loads(proc.stdout)


def video_summary(path: str) -> dict:
    """Compacte samenvatting: duration, resolution, fps, codec, has_audio."""
    info = probe(path)
    v_stream = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if not v_stream:
        raise FFmpegError(f"Geen videostream gevonden in {path}")

    duration = float(info.get("format", {}).get("duration") or v_stream.get("duration") or 0)
    width = int(v_stream.get("width", 0))
    height = int(v_stream.get("height", 0))

    fps = 0.0
    rate = v_stream.get("r_frame_rate", "0/1")
    try:
        num, den = rate.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 0.0
    except Exception:
        pass

    return {
        "duration": duration,
        "resolution": f"{width}x{height}",
        "width": width,
        "height": height,
        "fps": round(fps, 2),
        "codec": v_stream.get("codec_name", "unknown"),
        "has_audio": a_stream is not None,
    }


def extract_audio(video_path: str, out_wav: str) -> str:
    """Extraheert audio als mono 16kHz wav (ideaal voor Whisper)."""
    Path(out_wav).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out_wav),
    ]
    _run(cmd)
    return out_wav


def extract_audio_chunks(video_path: str, out_dir: str | Path,
                         chunk_seconds: int = 240,
                         max_upload_bytes: int = 8_000_000) -> list[tuple[str, float]]:
    """Extract speech audio once, then split locally into small WAV chunks.

    This deliberately avoids FFmpeg's segment muxer writing FLAC files directly.
    That combination can fail on some Windows FFmpeg builds/paths. One sequential
    decode is also faster than repeatedly seeking into a long source video.
    """
    import wave

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    duration = video_summary(video_path)["duration"]
    if duration <= 0:
        return []

    full_wav = out / "_full_audio_16k_mono.wav"
    cmd = [
        FFMPEG_PATH, "-y", "-i", str(video_path),
        "-vn", "-map", "0:a:0?", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(full_wav),
    ]
    _run(cmd)

    chunks: list[tuple[str, float]] = []
    with wave.open(str(full_wav), "rb") as src_wav:
        rate = src_wav.getframerate()
        channels = src_wav.getnchannels()
        width = src_wav.getsampwidth()
        total_frames = src_wav.getnframes()
        frames_per_chunk = max(1, int(chunk_seconds * rate))
        idx = 1
        frame_pos = 0

        while frame_pos < total_frames:
            src_wav.setpos(frame_pos)
            frames = src_wav.readframes(min(frames_per_chunk, total_frames - frame_pos))
            if not frames:
                break

            chunk = out / f"chunk-{idx:03d}.wav"
            with wave.open(str(chunk), "wb") as dst:
                dst.setnchannels(channels)
                dst.setsampwidth(width)
                dst.setframerate(rate)
                dst.writeframes(frames)

            # WAV is uncompressed, so use a conservative duration that stays
            # below the upload ceiling for speech.
            size = chunk.stat().st_size
            if size > max_upload_bytes:
                chunk.unlink(missing_ok=True)
                # Re-split this interval into two smaller chunks.
                sub_seconds = max(30, chunk_seconds // 2)
                sub_frames = max(1, int(sub_seconds * rate))
                sub_start = frame_pos
                sub_idx = idx
                while sub_start < min(frame_pos + frames_per_chunk, total_frames):
                    src_wav.setpos(sub_start)
                    sub = src_wav.readframes(min(sub_frames, total_frames - sub_start))
                    if not sub:
                        break
                    sub_path = out / f"chunk-{sub_idx:03d}.wav"
                    with wave.open(str(sub_path), "wb") as dst:
                        dst.setnchannels(channels)
                        dst.setsampwidth(width)
                        dst.setframerate(rate)
                        dst.writeframes(sub)
                    chunks.append((str(sub_path), sub_start / rate))
                    sub_idx += 1
                    sub_start += sub_frames
                idx = sub_idx
            else:
                chunks.append((str(chunk), frame_pos / rate))
                idx += 1

            frame_pos += frames_per_chunk

    full_wav.unlink(missing_ok=True)
    return chunks

def extract_frame(video_path: str, timestamp: float, out_jpg: str) -> str:
    """Pakt één frame op een timestamp (seconden) als jpg."""
    Path(out_jpg).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-ss", str(timestamp), "-i", str(video_path),
        "-frames:v", "1", "-q:v", "2", str(out_jpg),
    ]
    _run(cmd)
    return out_jpg


def cut_clip(video_path: str, start: float, end: float, out_path: str, reencode: bool = True) -> str:
    """Knipt een subclip. reencode=True gebruikt een precieze her-encode (nodig voor
    frame-accurate cuts); reencode=False gebruikt stream copy (sneller, minder precies)."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end - start)
    if reencode:
        cmd = [
            FFMPEG_PATH, "-y", "-ss", str(start), "-i", str(video_path), "-t", str(duration),
            "-c:v", "libx264", "-crf", "16", "-preset", "slow",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ]
    else:
        cmd = [
            FFMPEG_PATH, "-y", "-ss", str(start), "-i", str(video_path), "-t", str(duration),
            "-c", "copy", str(out_path),
        ]
    _run(cmd)
    return out_path


def _crop_x_expression(points: list[tuple[float, float]], src_w: int, src_h: int,
                        target_w: int, target_h: int) -> str:
    """Build a bounded, piecewise-linear FFmpeg crop-x expression."""
    crop_w = src_h * target_w / target_h
    if not points:
        center = src_w / 2
        x = max(0.0, min(src_w - crop_w, center - crop_w / 2))
        return f"{x:.3f}"

    centers = [max(0.0, min(1.0, p[1])) * src_w for p in points]
    xs = [max(0.0, min(src_w - crop_w, c - crop_w / 2)) for c in centers]
    if len(xs) == 1:
        return f"{xs[0]:.3f}"

    # Nested if() keeps the crop inside the source. Commas are escaped for
    # FFmpeg's filter parser because they are expression separators here.
    expr = f"{xs[-1]:.3f}"
    for i in range(len(xs) - 2, -1, -1):
        t0 = points[i][0]
        t1 = points[i + 1][0]
        if t1 <= t0 + 0.001:
            expr = f"{xs[i]:.3f}"
            continue
        lerp = f"({xs[i]:.3f}+({xs[i+1]:.3f}-{xs[i]:.3f})*(t-{t0:.3f})/{(t1-t0):.3f})"
        expr = f"if(lt(t\\,{t1:.3f})\\,{lerp}\\,{expr})"
    expr = f"if(lt(t\\,{points[0][0]:.3f})\\,{xs[0]:.3f}\\,{expr})"
    return expr


def render_clip(
    in_path: str,
    out_path: str,
    start: float,
    duration: float,
    target_w: int = 1080,
    target_h: int = 1920,
    reframe_points: Optional[list[tuple[float, float]]] = None,
    hook_text: Optional[str] = None,
    normalize: bool = True,
    blurred_background: bool = True,
) -> str:
    """Render a polished 9:16 clip with an optional blurred same-video background."""
    info = video_summary(in_path)
    src_w, src_h = info["width"], info["height"]
    src_ratio = src_w / src_h if src_h else target_w / target_h
    target_ratio = target_w / target_h

    filters: list[str] = []
    if blurred_background:
        # Use the SAME source twice: a blurred cover layer and a sharp,
        # aspect-ratio-preserving foreground. This avoids black bars and avoids
        # cropping important content from vertical source videos.
        if src_ratio > target_ratio:
            crop_w = int(src_h * target_ratio)
            xexpr = _crop_x_expression(reframe_points or [], src_w, src_h, target_w, target_h)
            bg = (
                f"[0:v]split=2[bg0][fg0];"
                f"[bg0]crop={crop_w}:{src_h}:{xexpr}:0,"
                f"scale={target_w}:{target_h}:flags=lanczos,"
                f"boxblur=luma_radius=28:luma_power=2,"
                f"eq=brightness=-0.10:saturation=0.80[bg];"
            )
        elif src_ratio < target_ratio:
            crop_h = int(src_w / target_ratio)
            y = max(0, (src_h - crop_h) // 2)
            bg = (
                f"[0:v]split=2[bg0][fg0];"
                f"[bg0]crop={src_w}:{crop_h}:0:{y},"
                f"scale={target_w}:{target_h}:flags=lanczos,"
                f"boxblur=luma_radius=28:luma_power=2,"
                f"eq=brightness=-0.10:saturation=0.80[bg];"
            )
        else:
            bg = (
                f"[0:v]split=2[bg0][fg0];"
                f"[bg0]scale={target_w}:{target_h}:flags=lanczos,"
                f"boxblur=luma_radius=28:luma_power=2,"
                f"eq=brightness=-0.10:saturation=0.80[bg];"
            )

        # Social layout: for landscape sources the sharp video must touch the
        # left/right edges. The blurred layer is therefore visible only above
        # and below the sharp video. For portrait sources we preserve the full
        # frame and use the same-video blur only when aspect ratios differ.
        if src_ratio > target_ratio:
            fg_w = target_w
            fg_h = max(2, round(target_w / src_ratio))
        elif src_ratio < target_ratio:
            fg_h = target_h
            fg_w = max(2, round(target_h * src_ratio))
        else:
            fg_w, fg_h = target_w, target_h

        bg += (
            f"[fg0]scale={fg_w}:{fg_h}:flags=lanczos[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto[base]"
        )
        filters = [bg]
    else:
        if abs(src_ratio - target_ratio) < 0.01:
            filters.append(f"scale={target_w}:{target_h}:flags=lanczos")
        elif src_ratio > target_ratio:
            crop_w = int(src_h * target_ratio)
            xexpr = _crop_x_expression(reframe_points or [], src_w, src_h, target_w, target_h)
            filters.append(f"crop={crop_w}:{src_h}:{xexpr}:0")
            filters.append(f"scale={target_w}:{target_h}:flags=lanczos")
        else:
            crop_h = int(src_w / target_ratio)
            y = max(0, (src_h - crop_h) // 2)
            filters.append(f"crop={src_w}:{crop_h}:0:{y}")
            filters.append(f"scale={target_w}:{target_h}:flags=lanczos")

    if hook_text:
        def esc(t: str) -> str:
            return t.replace("\\", "").replace(":", "\\:").replace("'", "").replace(",", "\\,")
        safe = esc(hook_text[:90])
        hook = (
            f"drawtext=fontfile='{_drawtext_fontfile()}':text='{safe}':"
            "fontcolor=white:fontsize=48:borderw=3:bordercolor=black:"
            "x=(w-text_w)/2:y=h*0.07:box=1:boxcolor=black@0.35:boxborderw=14"
        )
        if blurred_background:
            filters[0] += f";[base]{hook}[v]"
        else:
            filters.append(hook)

    vf = ",".join(filters) if not blurred_background else None

    af = "loudnorm=I=-16:TP=-1.5:LRA=11" if normalize and info.get("has_audio") else None
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-ss", f"{start:.3f}", "-i", str(in_path),
        "-t", f"{duration:.3f}",
        "-filter_complex" if blurred_background else "-vf",
        (filters[0] if blurred_background else vf),
        "-map", "[v]" if blurred_background and hook_text else ("[base]" if blurred_background else "0:v"),
        "-map", "0:a?",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
    ]
    if af:
        cmd += ["-af", af]
    cmd.append(str(out_path))
    try:
        _run(cmd)
    except FFmpegError:
        # Hardware encoders can be compiled into FFmpeg but unavailable on a
        # particular machine/driver. Retry once with a universally available
        # CPU encoder instead of making the whole job fail.
        if cmd[cmd.index("-c:v") + 1] != "libx264":
            cpu_cmd = list(cmd)
            pos = cpu_cmd.index("-c:v")
            cpu_cmd[pos:pos + 2] = ["-c:v", "libx264"]
            # Remove hardware-specific quality/preset flags.
            for flag in ["-preset", "-cq", "-global_quality", "-quality", "-qp_i", "-qp_p", "-crf"]:
                while flag in cpu_cmd:
                    i = cpu_cmd.index(flag)
                    del cpu_cmd[i:i+2]
            # CPU speed-first fallback.
            insert = cpu_cmd.index("-pix_fmt")
            cpu_cmd[insert:insert] = ["-preset", "ultrafast", "-crf", "20"]
            _run(cpu_cmd)
        else:
            raise
    return out_path


def convert_vertical(
    in_path: str,
    out_path: str,
    target_w: int = 1080,
    target_h: int = 1920,
    crop_center_x_ratio: Optional[float] = None,
) -> str:
    """Backward-compatible single-position vertical conversion."""
    points = [(0.0, crop_center_x_ratio)] if crop_center_x_ratio is not None else []
    info = video_summary(in_path)
    return render_clip(in_path, out_path, 0.0, info["duration"], target_w, target_h, points, None, False)

def burn_subtitles(in_path: str, srt_path: str, out_path: str) -> str:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    srt_escaped = _filter_path(srt_path)
    fonts_dir = _filter_path(BUNDLED_FONT.parent)
    vf = (
        f"subtitles='{srt_escaped}':fontsdir='{fonts_dir}':"
        "force_style='FontName=DejaVu Sans,FontSize=16,Outline=2,Bold=1'"
    )
    cmd = [
        FFMPEG_PATH, "-y", "-i", str(in_path), "-vf", vf,
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-c:a", "copy",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def _wrap_text(text: str, max_chars_per_line: int = 24) -> list[str]:
    """Woord-voor-woord regelafbreking zodat lange hooks niet buiten beeld vallen."""
    words = text.split()
    lines, current = [], ""
    for w in words:
        candidate = f"{current} {w}".strip()
        if len(candidate) > max_chars_per_line and current:
            lines.append(current)
            current = w
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def add_text_overlay(in_path: str, out_path: str, text: str, y_ratio: float = 0.08, fontsize: int = 50) -> str:
    """
    Hook-tekst bovenin beeld, netjes uitgelijnd binnen de beeldbreedte.
    Lange hooks worden automatisch over meerdere regels verdeeld i.p.v. van de
    rand af te lopen.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    def esc(t: str) -> str:
        return t.replace("\\", "").replace(":", "\\:").replace("'", "").replace(",", "\\,")

    lines = _wrap_text(text, max_chars_per_line=22)
    line_height = fontsize + 16
    drawtext_filters = []
    for i, line in enumerate(lines):
        y = f"h*{y_ratio}+{i*line_height}"
        drawtext_filters.append(
            f"drawtext=fontfile='{_drawtext_fontfile()}':text='{esc(line)}':"
            f"fontcolor=white:fontsize={fontsize}:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y={y}"
        )
    vf = ",".join(drawtext_filters)

    cmd = [
        FFMPEG_PATH, "-y", "-i", str(in_path), "-vf", vf,
        "-c:v", "libx264", "-crf", "16", "-preset", "slow",
        "-c:a", "copy",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def normalize_audio(in_path: str, out_path: str) -> str:
    """EBU R128 loudness normalisatie, streaming-standaard."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-i", str(in_path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "copy",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def export_mp4(in_path: str, out_path: str) -> str:
    """Laatste stap: garandeert een schone, compatibele mp4 (faststart voor social)."""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG_PATH, "-y", "-i", str(in_path),
        "-c", "copy", "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        _run(cmd)
    except FFmpegError:
        # Stream copy kan falen bij incompatibele containers -- val terug op re-encode.
        cmd = [
            FFMPEG_PATH, "-y", "-i", str(in_path),
            "-c:v", "libx264", "-crf", "16", "-preset", "slow",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ]
        _run(cmd)
    return out_path
