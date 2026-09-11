from pathlib import Path
import shutil
import uuid

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import check_config, blocking_config_errors, API_PORT, MAX_VIDEO_DURATION_SECONDS
from app.models import CampaignStartRequest, JobStatus
from app import job_manager
from app.pipeline import youtube, llm_provider

app = FastAPI(title="Clip Studio", version="1.0.0")

_project_root = Path(__file__).resolve().parent.parent
_frontend_dir = _project_root / "frontend"

if _frontend_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(_frontend_dir), html=True), name="ui")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    clip_file = _frontend_dir / "clip.html"
    if not clip_file.exists():
        raise HTTPException(status_code=404, detail="frontend/clip.html niet gevonden")
    return FileResponse(clip_file, media_type="text/html")


@app.get("/api/health")
def health():
    groq_info = llm_provider.groq_health()
    return {
        "status": "ok",
        "warnings": check_config(),
        "llm_provider": groq_info["llm_provider"],
        "llm_model": groq_info["llm_model"],
        "available": groq_info["available"],
        "llm_detail": groq_info["detail"],
        "clip_party_version": "2026-08-28-selection-fix-v1",
        "project_root": str(_project_root),
    }


UPLOADS_DIR = _project_root / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/upload")
def upload_file(file: UploadFile = File(...)):
    original_name = Path(file.filename or "upload").name
    safe_name = f"{uuid.uuid4().hex[:8]}_{original_name}"
    dest = UPLOADS_DIR / safe_name

    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload kon niet worden opgeslagen: {e}")

    suffix = Path(original_name).suffix.lower()
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}:
        try:
            from app.pipeline.ffmpeg_utils import video_summary
            summary = video_summary(str(dest))
            if summary["duration"] > MAX_VIDEO_DURATION_SECONDS + 0.01:
                dest.unlink(missing_ok=True)
                minutes = summary["duration"] / 60
                raise HTTPException(
                    status_code=400,
                    detail=f"Video is te lang: maximaal 60 minuten toegestaan (aangeleverd: {minutes:.1f} minuten).",
                )
        except HTTPException:
            raise
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Video kon niet worden gecontroleerd: {e}")

    return {"ok": True, "path": str(dest), "filename": original_name, "size": dest.stat().st_size}


@app.post("/api/campaign/start")
def start_campaign(req: CampaignStartRequest):
    errors = blocking_config_errors()
    if errors:
        raise HTTPException(status_code=400, detail={"message": "Configuratie incompleet", "errors": errors})
    job_id = job_manager.create_job(req)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    status = job_manager.get_job_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job niet gevonden")
    return status


@app.get("/api/jobs/{job_id}/clips")
def get_clips(job_id: str):
    clips = job_manager.get_job_clips(job_id)
    if clips is None:
        raise HTTPException(status_code=404, detail="Job niet gevonden")
    return {"clips": clips}


@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str):
    report = job_manager.get_job_report(job_id)
    if report is None:
        status = job_manager.get_job_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job niet gevonden")
        raise HTTPException(status_code=409, detail=f"Rapport nog niet klaar (status: {status.status})")
    return report


@app.get("/api/files/{file_id}")
def get_file(file_id: str):
    path = job_manager.resolve_file(file_id)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Bestand niet gevonden")
    return FileResponse(path, media_type="video/mp4", filename=Path(path).name)


@app.get("/api/dashboard")
def dashboard(customer_id: str = "local-customer"):
    return job_manager.get_dashboard(customer_id)


class ConnectionRequest(BaseModel):
    platform: str
    account_name: str = ""


@app.post("/api/account/connect")
def connect_account(req: ConnectionRequest):
    platform = req.platform.strip().lower()
    if platform not in {"cliparmy", "clipclub", "whop"}:
        raise HTTPException(status_code=400, detail="Onbekend platform")
    job_manager.set_connection(platform, "connected", req.account_name.strip())
    return {"ok": True, "platform": platform, "connection": job_manager.get_account_data()["connections"][platform]}


class EarningRequest(BaseModel):
    platform: str
    amount: float
    status: str = "paid"
    reference: str = ""


@app.post("/api/account/earnings")
def add_account_earning(req: EarningRequest):
    if req.amount < 0:
        raise HTTPException(status_code=400, detail="Bedrag kan niet negatief zijn")
    platform = req.platform.strip().lower()
    job_manager.add_earning(platform, req.amount, req.status.strip().lower(), "manual", req.reference.strip())
    return {"ok": True}


@app.get("/api/account")
def account():
    return job_manager.get_account_data()


class YoutubeResolveRequest(BaseModel):
    url: str


@app.post("/api/youtube/resolve")
def resolve_youtube(req: YoutubeResolveRequest):
    if not youtube.is_youtube_url(req.url):
        raise HTTPException(status_code=400, detail="Dit is geen YouTube-URL")
    try:
        return youtube.resolve_metadata(req.url)
    except youtube.YtDlpError as e:
        raise HTTPException(status_code=422, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=API_PORT, reload=False)
