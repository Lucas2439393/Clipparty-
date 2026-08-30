from app.models import CampaignProfile, CampaignSettings, ClipCandidate
from app.pipeline.clip_selector import check_compliance, rank_and_select


def test_uploaded_uuid_prefixed_source_is_not_rejected():
    profile = CampaignProfile(toegestane_bronvideos=["video playback (2).mp4"])
    candidate = ClipCandidate(
        source_video=r"C:\\ClipParty\\uploads\\f2d158dd_video playback (2).mp4",
        start=10,
        end=30,
        duration=20,
        scores={"overall": 80},
    )
    checked, *_ = check_compliance(profile, [candidate])
    assert checked[0].compliance_status == "PASS"
    selected = rank_and_select(checked, CampaignSettings(max_clips=1))
    assert len(selected) == 1


def test_forbidden_claim_still_fails():
    profile = CampaignProfile(verboden_claims=["100% gegarandeerd"])
    candidate = ClipCandidate(
        source_video="video.mp4", start=0, end=20, duration=20,
        scores={"overall": 80}, hook="100% gegarandeerd resultaat"
    )
    checked, *_ = check_compliance(profile, [candidate])
    assert checked[0].compliance_status == "FAIL"
