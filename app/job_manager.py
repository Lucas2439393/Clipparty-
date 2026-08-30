"""
Beheert jobs: aanmaken, status/voortgang bijhouden, en de volledige pipeline
per campagne uitvoeren (briefing -> analyse -> selectie -> productie -> QC -> rapport).

Elke campagne/job is volledig zelfstandig: er wordt nooit staat van een eerdere
job hergebruikt voor briefingregels of compliance.
"""
import json
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from app.config import DEFAULT_OUTPUT_DIR, WORK_DIR, DOWNLOADS_DIR, JOBS_DB_PATH, ACCOUNT_DB_PATH, CLIP_RENDER_WORKERS
from app.models import (
    CampaignStartRequest, JobStatus, JobReport, ClipCandidate, ProducedClip,
)
from app.pipeline import brief_parser, video_analysis, clip_selector, producer, quality_check, youtube, report_html

STAGES = {
    "queued": ("In wachtrij", 0),
    "reading_brief": ("Briefing lezen", 8),
    "downloading": ("Video's downloaden", 20),
    "analyzing": ("Video analyseren", 38),
    "selecting": ("Clips selecteren", 58),
    "editing": ("Clips produceren", 78),
    "quality_check": ("Kwaliteitscontrole", 93),
    "completed": ("Klaar", 100),
    "failed": ("Mislukt", 0),
}

# job_id -> {file_id: absolute_path} -- gebruikt door GET /api/files/{file_id}
_file_registry: Dict[str, Dict[str, str]] = {}


def register_file(job_id: str, file_id: str, path: str):
    _file_registry.setdefault(job_id, {})[file_id] = path


def resolve_file(file_id: str) -> Optional[str]:
    for files in _file_registry.values():
        if file_id in files:
            return files[file_id]
    return None

_lock = threading.Lock()
_jobs: Dict[str, dict] = {}

def _account_default() -> dict:
    return {
        "connections": {
            "cliparmy": {"status": "not_connected", "label": "Clip Army"},
            "clipclub": {"status": "not_connected", "label": "Clip Club"},
            "whop": {"status": "not_connected", "label": "Whop"},
        },
        "earnings": [],
    }

def _load_account() -> dict:
    data = _account_default()
    if ACCOUNT_DB_PATH.exists():
        try:
            raw = json.loads(ACCOUNT_DB_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
                data["connections"] = {**_account_default()["connections"], **(raw.get("connections") or {})}
                data["earnings"] = list(raw.get("earnings") or [])
        except Exception:
            pass
    return data

_account = _load_account()

def _persist_account():
    ACCOUNT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNT_DB_PATH.write_text(json.dumps(_account, ensure_ascii=False, indent=2), encoding="utf-8")

def get_account_data() -> dict:
    with _lock:
        return json.loads(json.dumps(_account))

def set_connection(platform: str, status: str = "connected", account_name: str = ""):
    with _lock:
        item = _account.setdefault("connections", {}).setdefault(platform, {})
        item["status"] = status
        if account_name:
            item["account_name"] = account_name
        item["updated_at"] = _now()
    _persist_account()

def add_earning(platform: str, amount: float, status: str = "paid", source: str = "manual", reference: str = ""):
    with _lock:
        _account.setdefault("earnings", []).append({
            "id": uuid.uuid4().hex[:10],
            "platform": platform,
            "amount": round(float(amount), 2),
            "status": status,
            "source": source,
            "reference": reference,
            "created_at": _now(),
        })
    _persist_account()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist():
    with _lock:
        JOBS_DB_PATH.write_text(json.dumps(_jobs, default=str, indent=2), encoding="utf-8")


def _load_persisted():
    if JOBS_DB_PATH.exists():
        try:
            with _lock:
                _jobs.update(json.loads(JOBS_DB_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass


_load_persisted()


def _set_stage(job_id: str, stage: str, extra_progress: int = 0, error: Optional[str] = None):
    label, base_progress = STAGES[stage]
    with _lock:
        job = _jobs[job_id]
        job["status"] = stage
        job["stage_label"] = label
        job["progress"] = min(100, base_progress + extra_progress)
        job["updated_at"] = _now()
        if error:
            job["error"] = error
    _persist()


def create_job(req: CampaignStartRequest) -> str:
    job_id = uuid.uuid4().hex[:12]
    campaign_name = req.campaign_name or Path(req.campaign_file).stem
    output_dir = str(Path(DEFAULT_OUTPUT_DIR) / campaign_name)

    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "stage_label": STAGES["queued"][0],
            "progress": 0,
            "error": None,
            "created_at": _now(),
            "updated_at": _now(),
            "campaign_name": campaign_name,
            "customer_id": req.customer_id,
            "output_dir": output_dir,
            "request": req.model_dump(),
            "clips": [],
            "report": None,
            "llm_provider_used": None,
            "llm_model_used": None,
        }
    _persist()

    thread = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    thread.start()
    return job_id


def set_job_llm_info(job_id: str, provider: str, model: Optional[str]):
    """Zet zodra bekend (na de eerste geslaagde LLM-call) welk model
    daadwerkelijk gebruikt wordt, zodat GET /api/jobs/{job_id} dit al toont
    terwijl de job nog loopt -- niet pas in het eindrapport."""
    with _lock:
        if job_id in _jobs:
            _jobs[job_id]["llm_provider_used"] = provider
            _jobs[job_id]["llm_model_used"] = model
    _persist()


def get_job_status(job_id: str) -> Optional[JobStatus]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return JobStatus(
            job_id=job["job_id"], status=job["status"], progress=job["progress"],
            stage_label=job["stage_label"], error=job.get("error"),
            created_at=job["created_at"], updated_at=job["updated_at"],
            campaign_name=job.get("campaign_name"), output_dir=job.get("output_dir"),
            llm_provider_used=job.get("llm_provider_used"),
            llm_model_used=job.get("llm_model_used"),
        )


def get_job_clips(job_id: str) -> Optional[list]:
    with _lock:
        job = _jobs.get(job_id)
        return job["clips"] if job else None


def get_job_report(job_id: str) -> Optional[dict]:
    with _lock:
        job = _jobs.get(job_id)
        return job.get("report") if job else None


def _run_job(job_id: str):
    with _lock:
        req = CampaignStartRequest(**_jobs[job_id]["request"])
        output_dir = Path(_jobs[job_id]["output_dir"])
        campaign_name = _jobs[job_id]["campaign_name"]

    job_work_dir = WORK_DIR / job_id
    technische_beperkingen = []

    try:
        # -------- 1. Briefing lezen --------
        _set_stage(job_id, "reading_brief")
        profile, provider_used, fallback_reason, model_used = brief_parser.extract_campaign_profile(
            req.campaign_file, provider=req.settings.llm_provider,
        )
        if fallback_reason:
            technische_beperkingen.append(fallback_reason)
        set_job_llm_info(job_id, provider_used, model_used)

        # -------- 2. Bronvideo's bepalen --------
        candidate_videos = list(req.videos)
        if profile.toegestane_bronvideos:
            # De geuploade/gevalideerde bestanden zijn de bron van waarheid.
            # Briefings bevatten vaak omschrijvingen, hoofdstuktitels of andere
            # namen die niet 1-op-1 gelijk zijn aan lokale bestandsnamen.
            # We beperken de set daarom alleen als er een ondubbelzinnige
            # bestandsmatch is; anders gebruiken we alle aangeleverde video's
            # zonder een misleidende waarschuwing.
            def _norm_name(value: str) -> str:
                s = Path(str(value)).stem.lower()
                return "".join(ch for ch in s if ch.isalnum())

            requested = {_norm_name(v) for v in profile.toegestane_bronvideos if str(v).strip()}
            matches = [v for v in candidate_videos if _norm_name(v) in requested]
            if matches:
                candidate_videos = matches

        # -------- 2b. YouTube-bronnen downloaden --------
        _set_stage(job_id, "downloading")
        campaign_workspace = DOWNLOADS_DIR / job_id
        allowed_videos = []
        for i, v in enumerate(candidate_videos):
            if youtube.is_youtube_url(v):
                try:
                    local_path = youtube.download_source(v, campaign_workspace)
                    allowed_videos.append(local_path)
                except youtube.YtDlpError as e:
                    technische_beperkingen.append(f"YouTube-download mislukt voor {v}: {e}")
            else:
                allowed_videos.append(v)
            _set_stage(job_id, "downloading", extra_progress=int(12 * (i + 1) / max(1, len(candidate_videos))))

        missing = [v for v in allowed_videos if not v.startswith(("http://", "https://"))
                   and not Path(v).exists()]
        for m in missing:
            technische_beperkingen.append(f"Bronvideo ontbreekt lokaal en is overgeslagen: {m}")
        allowed_videos = [v for v in allowed_videos if v not in missing]

        if not allowed_videos:
            raise RuntimeError("Geen enkele toegestane bronvideo is lokaal beschikbaar.")

        # -------- 3. Video-analyse (alle bronnen) --------
        _set_stage(job_id, "analyzing")
        analyses = []
        face_samples_by_video = {}
        transcript_by_video = {}
        for i, v in enumerate(allowed_videos):
            analysis, _ = video_analysis.analyze_video(v, job_work_dir / "analysis")
            analyses.append(analysis)
            # Face analysis is deferred until we know which clips actually won.
            face_samples_by_video[v] = []
            transcript_by_video[v] = analysis.transcript
            _set_stage(job_id, "analyzing", extra_progress=int(20 * (i + 1) / len(allowed_videos)))

        # -------- 4. Clipselectie over alle bronnen gezamenlijk --------
        _set_stage(job_id, "selecting")
        candidates, _, sel_fallback, sel_model = clip_selector.find_candidates(
            profile, analyses, req.settings, provider=req.settings.llm_provider,
        )
        candidates, _, comp_fallback, comp_model = clip_selector.check_compliance(
            profile, candidates, provider=req.settings.llm_provider,
        )
        # Laatst gebruikte model wint voor het rapport (ze zouden in de praktijk
        # hetzelfde moeten zijn, tenzij er tussentijds een fallback plaatsvond).
        if sel_model:
            model_used = sel_model
        if comp_model:
            model_used = comp_model
        set_job_llm_info(job_id, provider_used, model_used)
        # Alleen de eerste keer melden dat er is teruggeschakeld -- niet drie keer
        # dezelfde regel (briefing/selectie/compliance kunnen allemaal fallbacken).
        for fb in (sel_fallback, comp_fallback):
            if fb and fb not in technische_beperkingen:
                technische_beperkingen.append(fb)

        aantal_kandidaten = len(candidates)
        rejected = [c for c in candidates if c.compliance_status != "PASS"]

        selected = clip_selector.rank_and_select(candidates, req.settings)

        # Final deterministic safety-net: if the LLM returned candidates but
        # compliance/ranking left us with zero PASS clips, try local transcript
        # windows once more. This keeps an isolated LLM/JSON/editoric failure
        # from producing a misleading "completed / 0 clips" run. Compliance is
        # still applied to these fallback candidates, so forbidden content is
        # never forced through just to satisfy a minimum.
        if not selected and analyses:
            emergency_candidates = []
            for analysis in analyses:
                emergency_candidates.extend(
                    clip_selector.fallback_candidates(analysis, profile, req.settings)
                )
            emergency_candidates, _, _, _ = clip_selector.check_compliance(
                profile, emergency_candidates, provider=req.settings.llm_provider
            )
            if emergency_candidates:
                candidates.extend(emergency_candidates)
                selected = clip_selector.rank_and_select(candidates, req.settings)
                rejected = [c for c in candidates if c.compliance_status != "PASS"]
                if selected:
                    technische_beperkingen.append(
                        "Lokale fallback-selectie gebruikt omdat de normale selectie geen geldige clips overliet."
                    )

        # Only analyze faces inside the clips that actually survived selection.
        # This replaces a full-video face scan and is the main latency reduction.
        from app.pipeline.frame_analysis import sample_face_positions, median_center_x_ratio
        for c in selected:
            samples = sample_face_positions(
                c.source_video, interval_sec=2.0,
                start_sec=c.start, end_sec=c.end
            )
            face_samples_by_video.setdefault(c.source_video, [])
            # Keep per-clip samples keyed by source; producer filters by clip range.
            face_samples_by_video[c.source_video].extend(samples)
            relevant = [s for s in samples if c.start <= s["time"] <= c.end]
            if relevant:
                c.face_visible_estimate = sum(1 for s in relevant if s["face_found"]) / len(relevant)

        # min_clips is alleen een gebruikersvoorkeur/target.
        # Nooit een fout- of waarschuwingsmelding tonen wanneer het model
        # minder sterke clips selecteert; zwakke clips worden niet kunstmatig toegevoegd.
        # -------- 5. Productie --------
        _set_stage(job_id, "editing")
        clips_dir = output_dir / "clips"
        captions_dir = output_dir / "captions"
        captions_dir.mkdir(parents=True, exist_ok=True)
        produced: list[ProducedClip] = [None] * len(selected)
        render_workers = min(CLIP_RENDER_WORKERS, max(1, len(selected)))
        with ThreadPoolExecutor(max_workers=render_workers) as pool:
            futures = {
                pool.submit(
                    producer.produce_clip,
                    cand, idx, req.settings, clips_dir, job_work_dir / "render",
                    face_samples_by_video, transcript_by_video,
                ): idx
                for idx, cand in enumerate(selected, start=1)
            }
            done = 0
            for future in as_completed(futures):
                idx = futures[future]
                clip = future.result()
                produced[idx - 1] = clip
                caption_text = (clip.caption or "") + "\n\n" + " ".join(f"#{h.lstrip('#')}" for h in clip.hashtags)
                (captions_dir / f"clip-{idx:02d}.txt").write_text(caption_text.strip(), encoding="utf-8")
                done += 1
                _set_stage(job_id, "editing", extra_progress=int(17 * done / max(1, len(selected))))

        # -------- 6. Kwaliteitscontrole --------
        _set_stage(job_id, "quality_check")
        final_clips = []
        for clip in produced:
            checked = quality_check.check_clip(clip, req.settings)
            if not checked.quality_check_passed:
                technische_beperkingen.append(
                    f"{checked.filename}: kwaliteitscontrole gefaald ({checked.quality_check_notes})"
                )
            checked.file_id = f"{job_id}_{Path(checked.path).stem}"
            register_file(job_id, checked.file_id, checked.path)
            final_clips.append(checked)

        # -------- 7. Rapport --------
        report = JobReport(
            campaign_name=campaign_name,
            llm_provider_used=provider_used,
            llm_model_used=model_used,
            aantal_kandidaten=aantal_kandidaten,
            aantal_afgewezen=len(rejected),
            aantal_definitief=len(final_clips),
            bronvideos=allowed_videos,
            output_map=str(output_dir),
            technische_beperkingen=technische_beperkingen,
            clips=final_clips,
        )

        with _lock:
            _jobs[job_id]["clips"] = [c.model_dump() for c in final_clips]
            _jobs[job_id]["report"] = report.model_dump()
        _persist()

        (output_dir).mkdir(parents=True, exist_ok=True)
        (output_dir / "report.json").write_text(
            json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "report.html").write_text(report_html.render_html(report), encoding="utf-8")

        _set_stage(job_id, "completed")

    except Exception as e:
        err = f"{e}\n{traceback.format_exc()}"
        _set_stage(job_id, "failed", error=str(e))
        with _lock:
            _jobs[job_id]["full_error"] = err
        _persist()


def get_dashboard(customer_id: str = "local-customer") -> dict:
    """Return the personal clipper dashboard: jobs, clips, connections and
    real earnings records when a platform integration supplies them."""
    with _lock:
        jobs = [dict(j) for j in _jobs.values()
                if j.get("customer_id", "local-customer") == customer_id]
        account = json.loads(json.dumps(_account))
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    campaigns = []
    clips = []
    for j in jobs:
        report = j.get("report") or {}
        job_clips = j.get("clips") or report.get("clips") or []
        campaigns.append({
            "job_id": j.get("job_id"),
            "name": j.get("campaign_name"),
            "status": j.get("status"),
            "created_at": j.get("created_at"),
            "clip_count": len(job_clips),
        })
        for c in job_clips:
            item = dict(c)
            item["job_id"] = j.get("job_id")
            item["campaign_name"] = j.get("campaign_name")
            clips.append(item)

    earnings = account.get("earnings") or []
    total_earned = round(sum(float(e.get("amount", 0) or 0) for e in earnings if e.get("status") == "paid"), 2)
    pending_earned = round(sum(float(e.get("amount", 0) or 0) for e in earnings if e.get("status") == "pending"), 2)
    by_platform = {}
    for e in earnings:
        p = str(e.get("platform") or "other").lower()
        by_platform[p] = round(by_platform.get(p, 0) + float(e.get("amount", 0) or 0), 2)

    return {
        "customer_id": customer_id,
        "campaign_count": len(campaigns),
        "clip_count": len(clips),
        "total_earned": total_earned,
        "pending_earned": pending_earned,
        "earnings_synced": bool(earnings),
        "earnings": earnings[-100:][::-1],
        "earnings_by_platform": by_platform,
        "connections": account.get("connections", {}),
        "campaigns": campaigns,
        "clips": clips,
    }

