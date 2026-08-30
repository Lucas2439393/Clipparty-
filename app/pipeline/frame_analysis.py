"""Lichte lokale video-analyse voor smart 9:16 reframing.

De analyzer gebruikt OpenCV's ingebouwde Haar face detector. We slaan naast de
beste face ook alle gevonden faces op, zodat de renderer een stabiele crop-
trajectorie kan bouwen in plaats van één statische center-crop te gebruiken.
"""
import cv2
import numpy as np

_face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


def sample_face_positions(video_path: str, interval_sec: float = 2.0,
                         start_sec: float = 0.0, end_sec: float | None = None) -> list[dict]:
    """Fast face sampling by sequential decoding.

    For normal use we sample only the selected clip range. Sequential decoding
    avoids expensive random seeking (cap.set) on long MP4/GOP sources.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if fps else 0.0
    start_sec = max(0.0, float(start_sec))
    end_sec = duration if end_sec is None else min(duration, float(end_sec))
    if end_sec <= start_sec:
        cap.release()
        return []

    # Seek once to the clip start, then decode sequentially.
    cap.set(cv2.CAP_PROP_POS_MSEC, start_sec * 1000)
    next_sample = start_sec
    previous_center = None
    results = []
    frame_idx = max(0, int(start_sec * fps))

    while next_sample <= end_sec + 0.001:
        target_idx = max(frame_idx, int(round(next_sample * fps)))
        while frame_idx <= target_idx:
            ok, frame = cap.read()
            if not ok:
                cap.release()
                return results
            frame_idx += 1
        t = (frame_idx - 1) / fps
        if t > end_sec + 0.5:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = _face_cascade.detectMultiScale(
            gray, scaleFactor=1.12, minNeighbors=5,
            minSize=(max(48, width // 40), max(48, height // 40))
        )

        normalized = []
        for x, y, w, h in faces:
            cx = (x + w / 2) / max(1, width)
            cy = (y + h / 2) / max(1, height)
            area = (w * h) / max(1, width * height)
            normalized.append({
                "x": float(x / max(1, width)),
                "y": float(y / max(1, height)),
                "w": float(w / max(1, width)),
                "h": float(h / max(1, height)),
                "center_x_ratio": float(cx),
                "center_y_ratio": float(cy),
                "area": float(area),
            })

        chosen = None
        if normalized:
            if previous_center is None:
                chosen = max(normalized, key=lambda f: f["area"])
            else:
                chosen = min(
                    normalized,
                    key=lambda f: abs(f["center_x_ratio"] - previous_center) - 0.12 * f["area"],
                )
            previous_center = chosen["center_x_ratio"]

        results.append({
            "time": float(t),
            "face_found": chosen is not None,
            "center_x_ratio": chosen["center_x_ratio"] if chosen else None,
            "faces": normalized,
        })
        next_sample += max(0.5, interval_sec)

    cap.release()
    return results


def face_visibility_ratio(samples: list[dict]) -> float:
    if not samples:
        return 0.0
    found = sum(1 for s in samples if s.get("face_found"))
    return found / len(samples)


def median_center_x_ratio(samples: list[dict], start: float, end: float) -> float | None:
    vals = [
        s["center_x_ratio"] for s in samples
        if s.get("face_found") and s.get("center_x_ratio") is not None and start <= s["time"] <= end
    ]
    if not vals:
        return None
    return float(np.median(vals))


def reframe_points(
    samples: list[dict], start: float, end: float,
    min_change: float = 0.025,
    smoothing: float = 0.35,
) -> list[tuple[float, float]]:
    """Maak een klein, stabiel crop-traject voor één clip.

    We gebruiken alleen waargenomen gezichtsposities. Kleine bewegingen worden
    genegeerd; grotere bewegingen worden EMA-gesmoothd. De renderer kan deze
    punten als één FFmpeg crop-expressie uitvoeren, dus er is geen tweede
    frame-voor-frame videorender nodig.
    """
    raw = [
        (max(0.0, s["time"] - start), float(s["center_x_ratio"]))
        for s in samples
        if start <= s["time"] <= end and s.get("face_found") and s.get("center_x_ratio") is not None
    ]
    if not raw:
        return []
    if len(raw) == 1:
        return raw

    smoothed: list[tuple[float, float]] = []
    current = raw[0][1]
    last_output = current
    smoothed.append((raw[0][0], current))
    for t, target in raw[1:]:
        if abs(target - current) < min_change:
            target = current
        current = current + smoothing * (target - current)
        # Only keep meaningful movement; this makes the camera feel calmer.
        if abs(current - last_output) >= min_change * 0.45:
            smoothed.append((t, current))
            last_output = current

    if smoothed[-1][0] != raw[-1][0]:
        smoothed.append((raw[-1][0], current))
    return smoothed
