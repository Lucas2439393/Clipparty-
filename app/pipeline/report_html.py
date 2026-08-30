"""Genereert een zelfstandig report.html naast report.json -- makkelijk te
openen/delen zonder de app te hoeven starten."""
from app.models import JobReport

TEMPLATE = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<title>Clip Studio -- rapport: {campaign_name}</title>
<style>
  body {{ background:#0e0f13; color:#e8e8ec; font-family: -apple-system, Segoe UI, Arial, sans-serif; padding:32px; }}
  h1 {{ font-size:22px; }}
  .summary {{ display:flex; gap:24px; margin:16px 0 28px; flex-wrap:wrap; }}
  .stat {{ background:#181a20; border:1px solid #24262e; border-radius:10px; padding:14px 18px; }}
  .stat b {{ display:block; font-size:22px; }}
  .clip {{ background:#15161b; border:1px solid #24262e; border-radius:12px; padding:16px; margin-bottom:14px; }}
  .clip h3 {{ margin:0 0 6px; }}
  .meta {{ color:#9a9ba5; font-size:13px; margin-bottom:8px; }}
  .pass {{ color:#4ade80; }}
  .fail {{ color:#f87171; }}
  .tag {{ display:inline-block; background:#20222b; border-radius:6px; padding:2px 8px; margin:2px; font-size:12px; }}
  .limits {{ color:#f0b429; }}
</style>
</head>
<body>
  <h1>Clip Studio -- {campaign_name}</h1>
  <div class="summary">
    <div class="stat"><b>{aantal_definitief}</b>definitieve clips</div>
    <div class="stat"><b>{aantal_kandidaten}</b>kandidaten onderzocht</div>
    <div class="stat"><b>{aantal_afgewezen}</b>afgewezen</div>
  </div>
  {limits_html}
  {clips_html}
</body>
</html>
"""

CLIP_TEMPLATE = """
<div class="clip">
  <h3>{filename}</h3>
  <div class="meta">{duration:.1f}s &middot; {resolution} &middot; {source_video}
    &middot; <span class="{status_class}">{compliance}</span></div>
  <div><b>Onderwerp:</b> {onderwerp}</div>
  <div><b>Hook:</b> {hook}</div>
  <div><b>Payoff:</b> {payoff}</div>
  <div><b>Caption:</b> {caption}</div>
  <div>{hashtags_html}</div>
</div>
"""


def render_html(report: JobReport) -> str:
    clips_html = "".join(
        CLIP_TEMPLATE.format(
            filename=c.filename, duration=c.duration, resolution=c.resolution,
            source_video=c.source_video, status_class="pass" if c.compliance == "PASS" else "fail",
            compliance=c.compliance, onderwerp=c.onderwerp or "-", hook=c.hook or "-",
            payoff=c.payoff or "-", caption=c.caption or "-",
            hashtags_html="".join(f'<span class="tag">{h}</span>' for h in c.hashtags),
        )
        for c in report.clips
    )
    limits_html = ""
    if report.technische_beperkingen:
        items = "".join(f"<li>{b}</li>" for b in report.technische_beperkingen)
        limits_html = f'<div class="limits"><b>Beperkingen:</b><ul>{items}</ul></div>'

    return TEMPLATE.format(
        campaign_name=report.campaign_name or "campagne",
        aantal_definitief=report.aantal_definitief,
        aantal_kandidaten=report.aantal_kandidaten,
        aantal_afgewezen=report.aantal_afgewezen,
        limits_html=limits_html,
        clips_html=clips_html or "<p>Geen definitieve clips.</p>",
    )
