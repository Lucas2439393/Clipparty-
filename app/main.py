from pathlib import Path
import shutil
import uuid

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
from app.auth import (
    create_user,
    authenticate,
    create_session,
    get_user_from_token,
    delete_session,
)

app = FastAPI(title="Clip Studio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"

if _frontend_dir.exists():
    app.mount(
        "/ui",
        StaticFiles(directory=str(_frontend_dir), html=True),
        name="ui",
    )

@app.get("/")
def root():
    # The API service itself is also the ClipParty frontend.
    # This avoids a separate homepage/static-site/API mismatch.
    clip_file = _frontend_dir / "clip.html"
    if not clip_file.exists():
        raise HTTPException(status_code=404, detail="frontend/clip.html niet gevonden")
    return FileResponse(clip_file, media_type="text/html")

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class LogoutRequest(BaseModel):
    token: str = ""

@app.post("/api/auth/register")
def register(data: RegisterRequest):
    try:
        user = create_user(data.name, data.email, data.password)
        token = create_session(user["id"])
        return {"ok": True, "user": user, "token": token}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/login")
def login(data: LoginRequest):
    user = authenticate(data.email, data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="E-mailadres of wachtwoord is onjuist.",
        )
    token = create_session(user["id"])
    return {"ok": True, "user": user, "token": token}

@app.get("/api/auth/me")
def auth_me(token: str = ""):
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Niet ingelogd.")
    return {"ok": True, "user": user}

@app.post("/api/auth/logout")
def logout(data: LogoutRequest):
    delete_session(data.token)
    return {"ok": True}

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
        "project_root": str(Path(__file__).resolve().parent.parent),
    }

UPLOADS_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

@app.post("/api/upload")
def upload_file(file: UploadFile = File(...)):
    safe_name = f"{uuid.uuid4().hex[:8]}_{Path(file.filename).name}"
    dest = UPLOADS_DIR / safe_name

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    suffix = Path(file.filename).suffix.lower()

    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}:
        try:
            from app.pipeline.ffmpeg_utils import video_summary

            summary = video_summary(str(dest))

            if summary["duration"] > MAX_VIDEO_DURATION_SECONDS + 0.01:
                dest.unlink(missing_ok=True)
                minutes = summary["duration"] / 60
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Video is te lang: maximaal 60 minuten toegestaan "
                        f"(aangeleverd: {minutes:.1f} minuten)."
                    ),
                )
        except HTTPException:
            raise
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=f"Video kon niet worden gecontroleerd: {e}",
            )

    return {
        "path": str(dest),
        "filename": file.filename,
        "size": dest.stat().st_size,
    }

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

    # FIX: create_job krijgt de volledige request.
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
        raise HTTPException(
            status_code=409,
            detail=f"Rapport nog niet klaar (status: {status.status})",
        )

    return report

@app.get("/api/files/{file_id}")
def get_file(file_id: str):
    path = job_manager.resolve_file(file_id)

    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="Bestand niet gevonden")

    return FileResponse(
        path,
        media_type="video/mp4",
        filename=Path(path).name,
    )

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

    job_manager.set_connection(
        platform,
        "connected",
        req.account_name.strip(),
    )

    return {
        "ok": True,
        "platform": platform,
        "connection": job_manager.get_account_data()["connections"][platform],
    }

@app.post("/api/connect/{platform}")
def connect_platform_alias(
    platform: str,
    req: ConnectionRequest | None = None,
):
    payload = req or ConnectionRequest(platform=platform)
    payload.platform = platform
    return connect_account(payload)

@app.get("/api/me")
def me():
    data = job_manager.get_account_data()
    return {
        "id": "local-customer",
        **data.get("profile", {}),
        "tokens": 50,
    }

class ProfileRequest(BaseModel):
    name: str = ""
    email: str = ""

def _update_profile(name: str, email: str):
    data = job_manager.get_account_data()
    profile = data.get("profile", {})
    profile["name"] = name.strip() or "ClipParty gebruiker"
    profile["email"] = email.strip()
    job_manager.update_profile(profile)
    return job_manager.get_account_data()["profile"]

@app.put("/api/account/profile")
def update_profile(req: ProfileRequest):
    return {
        "ok": True,
        "profile": _update_profile(req.name, req.email),
    }

ACCOUNT_MEDIA_DIR = (
    Path(__file__).resolve().parent.parent / "account_media"
)
ACCOUNT_MEDIA_DIR.mkdir(parents=True, exist_ok=True)

app.mount(
    "/account-media",
    StaticFiles(directory=str(ACCOUNT_MEDIA_DIR)),
    name="account-media",
)

@app.post("/api/account/avatar")
def upload_avatar(file: UploadFile = File(...)):
    suffix = Path(file.filename or "avatar.jpg").suffix.lower()
    safe = f"{uuid.uuid4().hex[:8]}{suffix}"
    dest = ACCOUNT_MEDIA_DIR / safe

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    url = f"/account-media/{safe}"
    data = job_manager.get_account_data()
    profile = data.get("profile", {})
    profile["avatar_url"] = url
    job_manager.update_profile(profile)

    return {"ok": True, "avatar_url": url}

class EarningRequest(BaseModel):
    platform: str
    amount: float
    status: str = "paid"
    reference: str = ""

@app.post("/api/account/earnings")
def add_account_earning(req: EarningRequest):
    if req.amount < 0:
        raise HTTPException(
            status_code=400,
            detail="Bedrag kan niet negatief zijn",
        )

    job_manager.add_earning(
        req.platform.strip().lower(),
        req.amount,
        req.status.strip().lower(),
        "manual",
        req.reference.strip(),
    )

    return {"ok": True}

@app.get("/api/account")
def account():
    return job_manager.get_account_data()

PLATFORM_META = {
    "cliparmy": {
        "name": "Clip Army",
        "url": "https://cliparmy.nl/",
    },
    "clipclub": {
        "name": "Clip Club",
        "url": "https://clip-club.nl/",
    },
    "whop": {
        "name": "Whop",
        "url": "https://whop.com/",
    },
}

@app.get("/api/platforms/{platform}/campaigns")
def platform_campaigns(platform: str):
    platform = platform.strip().lower()

    if platform not in PLATFORM_META:
        raise HTTPException(status_code=404, detail="Onbekend platform")

    campaigns = {
        "cliparmy": [],
        "clipclub": [],
        "whop": [],
    }[platform]

    account = job_manager.get_account_data()

    connected = (
        account.get("connections", {})
        .get(platform, {})
        .get("status") == "connected"
    )

    return {
        "platform": platform,
        "platform_name": PLATFORM_META[platform]["name"],
        "connected": connected,
        "campaigns": campaigns,
        "source_url": PLATFORM_META[platform]["url"],
        "account_specific": False,
    }

@app.get("/api/platforms/{platform}/campaigns/{campaign_id}")
def platform_campaign_detail(platform: str, campaign_id: str):
    data = platform_campaigns(platform)

    for campaign in data["campaigns"]:
        if campaign["id"] == campaign_id:
            return campaign

    raise HTTPException(
        status_code=404,
        detail="Campagne niet gevonden",
    )

class YoutubeResolveRequest(BaseModel):
    url: str

@app.post("/api/youtube/resolve")
def resolve_youtube(req: YoutubeResolveRequest):
    if not youtube.is_youtube_url(req.url):
        raise HTTPException(
            status_code=400,
            detail="Dit is geen YouTube-URL",
        )

    try:
        return youtube.resolve_metadata(req.url)
    except youtube.YtDlpError as e:
        raise HTTPException(
            status_code=422,
            detail=str(e),
        )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=API_PORT,
        reload=False,
    )
