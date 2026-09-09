from pathlib import Path
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


app = FastAPI(
    title="ClipParty",
    version="1.0.0",
)


# ---------------------------------------------------------
# FRONTEND
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

if FRONTEND_DIR.exists():
    app.mount(
        "/ui",
        StaticFiles(
            directory=str(FRONTEND_DIR),
            html=True,
        ),
        name="ui",
    )


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# HOME
# ---------------------------------------------------------

@app.get("/")
def root():
    index_file = FRONTEND_DIR / "index.html"

    if index_file.exists():
        return FileResponse(index_file)

    return {
        "name": "ClipParty",
        "status": "online",
    }


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.get("/api/health")
def health():
    try:
        groq_info = llm_provider.groq_health()
    except Exception as e:
        groq_info = {
            "llm_provider": "groq",
            "llm_model": None,
            "available": False,
            "detail": str(e),
        }

    return {
        "status": "ok",
        "warnings": check_config(),
        "llm_provider": groq_info.get("llm_provider"),
        "llm_model": groq_info.get("llm_model"),
        "available": groq_info.get("available"),
        "llm_detail": groq_info.get("detail"),
        "clip_party_version": "simple-live-v1",
    }


# ---------------------------------------------------------
# UPLOADS
# ---------------------------------------------------------

UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/upload")
def upload_file(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Geen bestand ontvangen.",
        )

    safe_name = (
        f"{uuid.uuid4().hex[:8]}_"
        f"{Path(file.filename).name}"
    )

    destination = UPLOADS_DIR / safe_name

    try:
        with destination.open("wb") as output:
            shutil.copyfileobj(file.file, output)
    except Exception as e:
        destination.unlink(missing_ok=True)

        raise HTTPException(
            status_code=500,
            detail=f"Upload mislukt: {e}",
        )

    suffix = Path(file.filename).suffix.lower()

    video_extensions = {
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
        ".m4v",
        ".avi",
    }

    if suffix in video_extensions:
        try:
            from app.pipeline.ffmpeg_utils import video_summary

            summary = video_summary(str(destination))

            if (
                summary["duration"]
                > MAX_VIDEO_DURATION_SECONDS + 0.01
            ):
                destination.unlink(missing_ok=True)

                minutes = summary["duration"] / 60

                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Video is te lang. "
                        f"Maximaal 60 minuten toegestaan "
                        f"(aangeleverd: {minutes:.1f} minuten)."
                    ),
                )

        except HTTPException:
            raise

        except Exception as e:
            destination.unlink(missing_ok=True)

            raise HTTPException(
                status_code=400,
                detail=f"Video kon niet worden gecontroleerd: {e}",
            )

    return {
        "path": str(destination),
        "filename": file.filename,
        "size": destination.stat().st_size,
    }


# ---------------------------------------------------------
# CAMPAIGN / CLIPPING
# ---------------------------------------------------------

@app.post("/api/campaign/start")
def start_campaign(req: CampaignStartRequest):

    errors = blocking_config_errors()

    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Configuratie incompleet",
                "errors": errors,
            },
        )

    job_id = job_manager.create_job(req)

    return {
        "job_id": job_id,
    }


# ---------------------------------------------------------
# JOB STATUS
# ---------------------------------------------------------

@app.get(
    "/api/jobs/{job_id}",
    response_model=JobStatus,
)
def get_job(job_id: str):

    status = job_manager.get_job_status(job_id)

    if not status:
        raise HTTPException(
            status_code=404,
            detail="Job niet gevonden",
        )

    return status


# ---------------------------------------------------------
# CLIPS
# ---------------------------------------------------------

@app.get("/api/jobs/{job_id}/clips")
def get_clips(job_id: str):

    clips = job_manager.get_job_clips(job_id)

    if clips is None:
        raise HTTPException(
            status_code=404,
            detail="Job niet gevonden",
        )

    return {
        "clips": clips,
    }


# ---------------------------------------------------------
# REPORT
# ---------------------------------------------------------

@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str):

    report = job_manager.get_job_report(job_id)

    if report is None:

        status = job_manager.get_job_status(job_id)

        if not status:
            raise HTTPException(
                status_code=404,
                detail="Job niet gevonden",
            )

        raise HTTPException(
            status_code=409,
            detail=(
                "Rapport nog niet klaar "
                f"(status: {status.status})"
            ),
        )

    return report


# ---------------------------------------------------------
# FILES
# ---------------------------------------------------------

@app.get("/api/files/{file_id}")
def get_file(file_id: str):

    path = job_manager.resolve_file(file_id)

    if not path or not Path(path).exists():
        raise HTTPException(
            status_code=404,
            detail="Bestand niet gevonden",
        )

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=Path(path).name,
    )


# ---------------------------------------------------------
# YOUTUBE
# ---------------------------------------------------------

class YoutubeResolveRequest(BaseModel):
    url: str


@app.post("/api/youtube/resolve")
def resolve_youtube(req: YoutubeResolveRequest):

    if not youtube.is_youtube_url(req.url):
        raise HTTPException(
            status_code=400,
            detail="Dit is geen geldige YouTube-URL.",
        )

    try:
        return youtube.resolve_metadata(req.url)

    except youtube.YtDlpError as e:
        raise HTTPException(
            status_code=422,
            detail=str(e),
        )


# ---------------------------------------------------------
# START SERVER
# ---------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=API_PORT,
        reload=False,
    )
