from __future__ import annotations

from pathlib import Path
from typing import Optional
import shutil
import uuid

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import (
    check_config,
    blocking_config_errors,
    API_PORT,
    MAX_VIDEO_DURATION_SECONDS,
)
from app.models import CampaignStartRequest, JobStatus
from app import job_manager
from app.pipeline import youtube, llm_provider


# ============================================================
# CLIPPARTY API
# ============================================================

app = FastAPI(
    title="ClipParty",
    version="1.0.0",
)


# ============================================================
# FRONTEND
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"

if FRONTEND_DIR.exists():
    app.mount(
        "/ui",
        StaticFiles(
            directory=str(FRONTEND_DIR),
            html=True,
        ),
        name="ui",
    )


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# UPLOADS
# ============================================================

UPLOADS_DIR = PROJECT_ROOT / "uploads"
UPLOADS_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# HOME
# ============================================================

@app.get("/")
def root():
    """
    Open de ClipParty-website wanneer iemand de API-root bezoekt.
    """

    index_file = FRONTEND_DIR / "index.html"

    if index_file.exists():
        return FileResponse(
            str(index_file),
            media_type="text/html",
        )

    return RedirectResponse(
        url="/ui/"
    )


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():
    """
    Controleert of de ClipParty API actief is.
    """

    try:
        groq_info = llm_provider.groq_health()
    except Exception as exc:
        groq_info = {
            "llm_provider": "groq",
            "llm_model": None,
            "available": False,
            "detail": str(exc),
        }

    return {
        "status": "ok",
        "warnings": check_config(),
        "llm_provider": groq_info.get(
            "llm_provider",
            "groq",
        ),
        "llm_model": groq_info.get(
            "llm_model"
        ),
        "available": groq_info.get(
            "available",
            False,
        ),
        "llm_detail": groq_info.get(
            "detail"
        ),
        "clip_party_version": "2026-simple-mvp-v1",
        "project_root": str(PROJECT_ROOT),
    }


# ============================================================
# FILE UPLOAD
# ============================================================

@app.post("/api/upload")
def upload_file(
    file: UploadFile = File(...)
):
    """
    Upload een briefing of bronvideo.

    Ondersteunde videobestanden:
    mp4, mov, mkv, webm, m4v, avi
    """

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Geen bestand ontvangen.",
        )

    original_name = Path(file.filename).name

    if not original_name:
        raise HTTPException(
            status_code=400,
            detail="Ongeldige bestandsnaam.",
        )

    safe_name = (
        f"{uuid.uuid4().hex[:12]}_"
        f"{original_name}"
    )

    destination = UPLOADS_DIR / safe_name

    try:
        with destination.open("wb") as output:
            shutil.copyfileobj(
                file.file,
                output,
            )
    except Exception as exc:
        destination.unlink(
            missing_ok=True
        )

        raise HTTPException(
            status_code=500,
            detail=f"Bestand kon niet worden opgeslagen: {exc}",
        )

    # --------------------------------------------------------
    # VIDEO VALIDATIE
    # --------------------------------------------------------

    video_extensions = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".m4v",
        ".avi",
    }

    suffix = Path(original_name).suffix.lower()

    if suffix in video_extensions:
        try:
            from app.pipeline.ffmpeg_utils import video_summary

            summary = video_summary(
                str(destination)
            )

            duration = float(
                summary.get(
                    "duration",
                    0,
                )
            )

            if (
                duration
                > MAX_VIDEO_DURATION_SECONDS + 0.01
            ):
                destination.unlink(
                    missing_ok=True
                )

                minutes = duration / 60

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Video is te lang. "
                        "Maximaal 60 minuten toegestaan. "
                        f"Aangeleverd: {minutes:.1f} minuten."
                    ),
                )

        except HTTPException:
            raise

        except Exception as exc:
            destination.unlink(
                missing_ok=True
            )

            raise HTTPException(
                status_code=400,
                detail=(
                    "Video kon niet worden gecontroleerd: "
                    f"{exc}"
                ),
            )

    return {
        "success": True,
        "path": str(destination),
        "filename": original_name,
        "size": destination.stat().st_size,
    }


# ============================================================
# START CLIPPING JOB
# ============================================================

@app.post("/api/campaign/start")
def start_campaign(
    req: CampaignStartRequest
):
    """
    Start een nieuwe ClipParty clipping-job.
    """

    errors = blocking_config_errors()

    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Configuratie incompleet",
                "errors": errors,
            },
        )

    if not req.videos:
        raise HTTPException(
            status_code=400,
            detail="Upload minimaal één bronvideo.",
        )

    try:
        job_id = job_manager.create_job(
            req
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Clip-job kon niet worden gestart: {exc}",
        )

    return {
        "success": True,
        "job_id": job_id,
    }


# ============================================================
# JOB STATUS
# ============================================================

@app.get(
    "/api/jobs/{job_id}",
    response_model=JobStatus,
)
def get_job(
    job_id: str
):
    """
    Geeft de huidige status van een clipping-job terug.
    """

    status = job_manager.get_job_status(
        job_id
    )

    if not status:
        raise HTTPException(
            status_code=404,
            detail="Job niet gevonden.",
        )

    return status


# ============================================================
# JOB CLIPS
# ============================================================

@app.get("/api/jobs/{job_id}/clips")
def get_clips(
    job_id: str
):
    """
    Geeft alle geproduceerde clips van een job terug.
    """

    clips = job_manager.get_job_clips(
        job_id
    )

    if clips is None:
        raise HTTPException(
            status_code=404,
            detail="Job niet gevonden.",
        )

    return {
        "clips": clips
    }


# ============================================================
# JOB REPORT
# ============================================================

@app.get("/api/jobs/{job_id}/report")
def get_report(
    job_id: str
):
    """
    Geeft het eindrapport van een clipping-job terug.
    """

    report = job_manager.get_job_report(
        job_id
    )

    if report is None:
        status = job_manager.get_job_status(
            job_id
        )

        if not status:
            raise HTTPException(
                status_code=404,
                detail="Job niet gevonden.",
            )

        raise HTTPException(
            status_code=409,
            detail=(
                "Rapport is nog niet klaar. "
                f"Status: {status.status}"
            ),
        )

    return report


# ============================================================
# GENERATED FILES
# ============================================================

@app.get("/api/files/{file_id}")
def get_file(
    file_id: str
):
    """
    Geeft een geproduceerde clip terug.
    """

    path = job_manager.resolve_file(
        file_id
    )

    if (
        not path
        or not Path(path).exists()
    ):
        raise HTTPException(
            status_code=404,
            detail="Bestand niet gevonden.",
        )

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=Path(path).name,
    )


# ============================================================
# YOUTUBE RESOLVE
# ============================================================

class YoutubeResolveRequest(BaseModel):
    url: str


@app.post("/api/youtube/resolve")
def resolve_youtube(
    req: YoutubeResolveRequest
):
    """
    Haalt metadata op van een YouTube-video.
    """

    if not youtube.is_youtube_url(
        req.url
    ):
        raise HTTPException(
            status_code=400,
            detail="Dit is geen geldige YouTube-URL.",
        )

    try:
        return youtube.resolve_metadata(
            req.url
        )

    except youtube.YtDlpError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=(
                "YouTube-video kon niet worden opgehaald: "
                f"{exc}"
            ),
        )


# ============================================================
# SIMPLE FRONTEND ROUTES
# ============================================================

@app.get("/clip")
def clip_page():
    """
    Open de ClipParty clippagina.
    """

    clip_file = FRONTEND_DIR / "clip.html"

    if clip_file.exists():
        return FileResponse(
            str(clip_file),
            media_type="text/html",
        )

    # Ondersteunt ook de bestaande structuur
    # frontend/ui/clip.html
    old_clip_file = (
        FRONTEND_DIR
        / "ui"
        / "clip.html"
    )

    if old_clip_file.exists():
        return FileResponse(
            str(old_clip_file),
            media_type="text/html",
        )

    raise HTTPException(
        status_code=404,
        detail="Clippagina niet gevonden.",
    )


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=API_PORT,
        reload=False,
    )
