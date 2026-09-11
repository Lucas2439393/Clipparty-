from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path
import shutil
import uuid
import os

from app import job_manager


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="ClipParty API",
    version="1.0.0",
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DIRECTORIES
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# MODELS
# ============================================================

class CampaignSettings(BaseModel):
    min_clips: int = 1
    max_clips: int = 5
    max_duration: int = 60

    preferred_min_duration: int = 15
    preferred_max_duration: int = 35

    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"

    subtitles: bool = True
    hooks: bool = True
    face_priority: bool = True
    avoid_audio_only: bool = True

    quality: str = "highest"


class CampaignRequest(BaseModel):
    campaign_file: str
    videos: list[str]
    settings: CampaignSettings


class YouTubeRequest(BaseModel):
    url: str


# ============================================================
# HEALTH
# ============================================================

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "ClipParty API"
    }


@app.get("/")
def root():
    return {
        "status": "ok",
        "service": "ClipParty API",
        "message": "ClipParty backend is running."
    }


# ============================================================
# UPLOAD
# ============================================================

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="Geen bestand ontvangen."
        )

    extension = Path(file.filename).suffix.lower()

    allowed_extensions = {
        ".pdf",
        ".doc",
        ".docx",
        ".txt",
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",
        ".m4v",
    }

    if extension not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Bestandstype {extension} wordt niet ondersteund."
        )

    file_id = uuid.uuid4().hex

    safe_name = (
        file_id + extension
    )

    destination = UPLOAD_DIR / safe_name

    try:
        with destination.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Upload mislukt: {str(exc)}"
        )

    return {
        "id": file_id,
        "file_id": file_id,
        "filename": file.filename,
        "path": str(destination),
        "size": destination.stat().st_size,
    }


# ============================================================
# START CAMPAIGN
# ============================================================

@app.post("/api/campaign/start")
async def start_campaign(request: CampaignRequest):

    if not request.campaign_file:
        raise HTTPException(
            status_code=400,
            detail="Geen campagnebriefing opgegeven."
        )

    if not request.videos:
        raise HTTPException(
            status_code=400,
            detail="Geen bronvideo opgegeven."
        )

    try:

        # job_manager is verantwoordelijk voor
        # het daadwerkelijk starten van de clipping-job.
        #
        # We proberen eerst de bestaande functie te gebruiken.

        if hasattr(job_manager, "create_job"):
            result = job_manager.create_job(
                campaign_file=request.campaign_file,
                videos=request.videos,
                settings=request.settings.model_dump(),
            )

        elif hasattr(job_manager, "start_job"):
            result = job_manager.start_job(
                campaign_file=request.campaign_file,
                videos=request.videos,
                settings=request.settings.model_dump(),
            )

        elif hasattr(job_manager, "create_campaign_job"):
            result = job_manager.create_campaign_job(
                campaign_file=request.campaign_file,
                videos=request.videos,
                settings=request.settings.model_dump(),
            )

        else:
            raise RuntimeError(
                "Geen geldige job-start functie gevonden in job_manager.py"
            )

        # Ondersteun zowel een dict als alleen een job_id.
        if isinstance(result, dict):
            if "job_id" in result:
                return result

            if "id" in result:
                return {
                    "job_id": result["id"]
                }

        if isinstance(result, str):
            return {
                "job_id": result
            }

        raise RuntimeError(
            "De job-manager gaf geen geldige job_id terug."
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Clipping-job kon niet worden gestart: {str(exc)}"
        )


# ============================================================
# JOB STATUS
# ============================================================

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):

    try:

        if hasattr(job_manager, "get_job"):
            result = job_manager.get_job(job_id)

        elif hasattr(job_manager, "get_job_status"):
            result = job_manager.get_job_status(job_id)

        else:
            raise RuntimeError(
                "Geen get_job functie gevonden in job_manager.py"
            )

        if result is None:
            raise HTTPException(
                status_code=404,
                detail="Job niet gevonden."
            )

        return result

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Jobstatus kon niet worden opgehaald: {str(exc)}"
        )


# ============================================================
# JOB CLIPS
# ============================================================

@app.get("/api/jobs/{job_id}/clips")
async def get_clips(job_id: str):

    try:

        if hasattr(job_manager, "get_clips"):
            result = job_manager.get_clips(job_id)

        elif hasattr(job_manager, "get_job_clips"):
            result = job_manager.get_job_clips(job_id)

        else:
            raise RuntimeError(
                "Geen get_clips functie gevonden in job_manager.py"
            )

        if result is None:
            return []

        return result

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Clips konden niet worden opgehaald: {str(exc)}"
        )


# ============================================================
# JOB REPORT
# ============================================================

@app.get("/api/jobs/{job_id}/report")
async def get_report(job_id: str):

    try:

        if hasattr(job_manager, "get_report"):
            result = job_manager.get_report(job_id)

        elif hasattr(job_manager, "get_job_report"):
            result = job_manager.get_job_report(job_id)

        else:
            return {
                "job_id": job_id,
                "status": "completed"
            }

        return result

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Rapport kon niet worden opgehaald: {str(exc)}"
        )


# ============================================================
# FILES
# ============================================================

@app.get("/api/files/{file_id}")
async def get_file(file_id: str):

    # Zoek eerst in outputs
    for directory in [OUTPUT_DIR, UPLOAD_DIR]:

        for path in directory.glob("*"):

            if path.is_file() and path.name.startswith(file_id):
                return FileResponse(
                    path=str(path),
                    filename=path.name
                )

    raise HTTPException(
        status_code=404,
        detail="Bestand niet gevonden."
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/api/dashboard")
async def dashboard():

    return {
        "status": "ok",
        "service": "ClipParty",
        "jobs": []
    }


# ============================================================
# ACCOUNT
# ============================================================

@app.post("/api/account/connect")
async def connect_account(data: dict):

    return {
        "status": "connected",
        "data": data
    }


@app.post("/api/account/earnings")
async def add_account_earning(data: dict):

    return {
        "status": "ok",
        "data": data
    }


@app.get("/api/account")
async def account():

    return {
        "status": "ok",
        "earnings": 0
    }


# ============================================================
# YOUTUBE
# ============================================================

@app.post("/api/youtube/resolve")
async def resolve_youtube(request: YouTubeRequest):

    url = request.url.strip()

    if not url:
        raise HTTPException(
            status_code=400,
            detail="Geen YouTube URL opgegeven."
        )

    return {
        "url": url,
        "status": "accepted"
    }
