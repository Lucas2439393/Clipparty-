from __future__ import annotations
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class CampaignSettings(BaseModel):
    min_clips: int = 0
    max_clips: int = 50
    max_duration: int = 60
    preferred_min_duration: int = 15
    preferred_max_duration: int = 35
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    subtitles: bool = False
    hooks: bool = True
    face_priority: bool = True
    quality: str = "highest"
    # LLM-provider voor briefing-extractie, clipselectie, compliance en captions.
    # "groq" (standaard, gebruikt de bestaande GROQ_API_KEY) of "anthropic"
    # (optioneel -- valt automatisch terug op Groq als ANTHROPIC_API_KEY ontbreekt).
    llm_provider: str = "groq"


class CampaignStartRequest(BaseModel):
    campaign_file: str = Field(..., description="Lokaal pad naar de campagnebriefing (pdf/docx/txt)")
    videos: List[str] = Field(..., description="Lokale paden of YouTube-URL's van bronvideo's")
    settings: CampaignSettings = CampaignSettings()
    campaign_name: Optional[str] = None
    customer_id: str = "local-customer"


class CampaignProfile(BaseModel):
    """Alles wat uit de briefing is geëxtraheerd. Niets hier is verzonnen --
    velden die niet in de briefing stonden blijven None/leeg."""
    merk: Optional[str] = None
    campagne: Optional[str] = None
    doel: Optional[str] = None
    doelgroep: Optional[str] = None
    bronmateriaal: Optional[str] = None
    toegestane_onderwerpen: List[str] = []
    verboden_onderwerpen: List[str] = []
    hook_richtlijnen: Optional[str] = None
    edit_richtlijnen: Optional[str] = None
    minimumduur: Optional[int] = None
    maximumduur: Optional[int] = None
    captions: Optional[str] = None
    hashtags: List[str] = []
    tags: List[str] = []
    links: List[str] = []
    disclaimers: Optional[str] = None
    platformregels: Optional[str] = None
    verboden_claims: List[str] = []
    overige_compliance_eisen: Optional[str] = None
    toegestane_bronvideos: List[str] = []
    raw_extraction_notes: Optional[str] = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class VideoAnalysis(BaseModel):
    source_path: str
    duration: float
    resolution: str
    fps: float
    codec: str
    has_audio: bool
    transcript: List[TranscriptSegment] = []
    face_visibility_ratio: Optional[float] = None  # 0-1, over gesamplede frames
    sampled_frame_times: List[float] = []
    notes: Optional[str] = None


class ClipCandidate(BaseModel):
    source_video: str
    start: float
    end: float
    duration: float
    topic: Optional[str] = None
    hook: Optional[str] = None
    payoff: Optional[str] = None
    reden_selectie: Optional[str] = None
    scores: Dict[str, float] = {}
    compliance_status: str = "PENDING"  # PASS / FAIL / PENDING
    compliance_reason: Optional[str] = None
    face_visible_estimate: Optional[float] = None
    caption: Optional[str] = None
    hashtags: List[str] = []


class ProducedClip(BaseModel):
    filename: str
    path: str
    file_id: Optional[str] = None
    source_video: str
    start: float
    end: float
    duration: float
    resolution: str
    onderwerp: Optional[str] = None
    hook: Optional[str] = None
    payoff: Optional[str] = None
    reden_selectie: Optional[str] = None
    visuele_continuiteit: Optional[str] = None
    gezicht_zichtbaar: Optional[str] = None
    beeldkwaliteit: Optional[str] = None
    compliance: str = "PASS"
    caption: Optional[str] = None
    hashtags: List[str] = []
    quality_check_passed: bool = False
    quality_check_notes: Optional[str] = None


class JobReport(BaseModel):
    campaign_name: Optional[str] = None
    llm_provider_used: str = "groq"
    llm_model_used: Optional[str] = None
    aantal_kandidaten: int = 0
    aantal_afgewezen: int = 0
    aantal_definitief: int = 0
    bronvideos: List[str] = []
    output_map: str = ""
    technische_beperkingen: List[str] = []
    clips: List[ProducedClip] = []


class JobStatus(BaseModel):
    job_id: str
    status: str = "queued"
    progress: int = 0
    stage_label: str = "In wachtrij"
    error: Optional[str] = None
    created_at: str
    updated_at: str
    campaign_name: Optional[str] = None
    output_dir: Optional[str] = None
    llm_provider_used: Optional[str] = None
    llm_model_used: Optional[str] = None
