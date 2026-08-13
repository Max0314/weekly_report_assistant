from __future__ import annotations

import hashlib
import hmac
import html
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ..config import Settings, settings
from ..db import Database, db
from ..time_utils import now_local, to_db
from .reports import ReportService, report_service


class RenderError(RuntimeError):
    pass


def _section_html(value: Any) -> str:
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return '<p class="empty">暂无</p>'
    items = []
    for line in lines:
        normalized = line[1:].strip() if line.startswith(("-", "•")) else line
        items.append(f"<li>{html.escape(normalized)}</li>")
    return f"<ul>{''.join(items)}</ul>"


def report_html(report: dict[str, Any]) -> str:
    sections = report.get("sections") or {}
    metrics = report.get("metrics") or {}
    window = report.get("window") or {}
    sources = report.get("sources") or []
    category_cards = "".join(
        f'<div class="metric"><span>{html.escape(str(name))}</span><strong>{int(count or 0)}</strong></div>'
        for name, count in (metrics.get("byCategory") or {}).items()
    )
    source_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('category') or ''))}</td>"
        f"<td>{html.escape(str(item.get('title') or ''))}</td>"
        f"<td>{html.escape(str(item.get('status') or '-'))}</td>"
        f"<td>{html.escape('、'.join(item.get('productManagerNames') or item.get('projectManagerNames') or []))}</td>"
        f"<td>{html.escape(str(item.get('dueAt') or '-').split('T')[0])}</td>"
        "</tr>"
        for item in sources[:80]
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(report.get('title') or '周报'))}</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#eef3f9;color:#142033;font-family:'Noto Sans CJK SC','Microsoft YaHei',sans-serif}}
.page{{width:1480px;margin:0 auto;background:#f8fafc;min-height:900px;padding:48px}}
.hero{{border-radius:28px;padding:38px 44px;color:#fff;background:linear-gradient(135deg,#173b68,#24689d 56%,#0f8a82)}}
.hero h1{{font-size:42px;margin:0 0 14px}} .hero p{{font-size:20px;margin:0;opacity:.9}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin:24px 0}}
.stat,.metric,.card{{background:#fff;border:1px solid #dce5ef;border-radius:18px;box-shadow:0 8px 24px rgba(23,59,104,.06)}}
.stat{{padding:20px}} .stat span,.metric span{{display:block;color:#5d6c80;font-size:15px}} .stat strong{{font-size:34px;color:#173b68}}
.lead{{padding:26px 30px;background:#fff;border-left:8px solid #0f8a82;border-radius:18px;font-size:22px;line-height:1.7}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:24px}} .card{{padding:26px 30px}}
.card h2{{font-size:25px;margin:0 0 16px;color:#173b68}} ul{{padding-left:24px;margin:0}} li{{font-size:18px;line-height:1.65;margin:7px 0}}
.risk h2{{color:#b24b2d}} .metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}
.metric{{padding:16px}} .metric strong{{font-size:25px;color:#24689d}}
.table-card{{margin-top:24px;padding:26px 30px}} table{{width:100%;border-collapse:collapse}} th,td{{padding:13px 12px;border-bottom:1px solid #e4eaf1;text-align:left;font-size:15px;vertical-align:top}} th{{color:#526276;background:#f3f6fa}}
.foot{{margin-top:26px;color:#718096;text-align:right;font-size:14px}} .empty{{color:#8492a6}}
@media(max-width:900px){{.page{{width:100%;padding:18px}}.stats{{grid-template-columns:repeat(2,1fr)}}.grid{{grid-template-columns:1fr}}}}
</style></head><body><main class="page">
<section class="hero"><h1>{html.escape(str(report.get('title') or '产品与项目管理周报'))}</h1><p>{html.escape(str(window.get('label') or report.get('periodKey') or ''))} · v{int(report.get('version') or 0)}</p></section>
<section class="stats">
<div class="stat"><span>纳入事项</span><strong>{int(metrics.get('itemCount') or 0)}</strong></div>
<div class="stat"><span>涉及负责人</span><strong>{int(metrics.get('managerCount') or 0)}</strong></div>
<div class="stat"><span>风险事项</span><strong>{int(metrics.get('riskCount') or 0)}</strong></div>
<div class="stat"><span>逾期事项</span><strong>{int(metrics.get('overdueCount') or 0)}</strong></div>
<div class="stat"><span>高优先级</span><strong>{int(metrics.get('highPriorityCount') or 0)}</strong></div>
</section>
<section class="lead">{html.escape(str(sections.get('executiveSummary') or '暂无总结'))}</section>
<section class="metrics">{category_cards}</section>
<section class="grid">
<article class="card"><h2>产品管理进展</h2>{_section_html(sections.get('productHighlights'))}</article>
<article class="card"><h2>项目管理进展</h2>{_section_html(sections.get('projectHighlights'))}</article>
<article class="card risk"><h2>风险与待跟进</h2>{_section_html(sections.get('risks'))}</article>
<article class="card"><h2>下周计划</h2>{_section_html(sections.get('nextPlans'))}</article>
<article class="card"><h2>需协调与支持</h2>{_section_html(sections.get('supportNeeds'))}</article>
</section>
<section class="card table-card"><h2>本周事实清单</h2><table><thead><tr><th>类别</th><th>事项</th><th>状态</th><th>负责人</th><th>截止</th></tr></thead><tbody>{source_rows or '<tr><td colspan="5">暂无</td></tr>'}</tbody></table></section>
<div class="foot">由周报助手根据 AI 多维表快照生成；统计数字由程序计算，AI 仅用于归纳文案。</div>
</main></body></html>"""


class ReportRenderer:
    def __init__(
        self,
        database: Database | None = None,
        reports: ReportService | None = None,
        config: Settings | None = None,
    ) -> None:
        self.db = database or db
        self.reports = reports or report_service
        self.settings = config or settings

    def render(self, report_id: int) -> dict[str, Any]:
        report = self.reports.get(report_id, include_sources=True)
        if report["workflowState"] in {"formal_sent", "recalled", "cancelled"}:
            raise RenderError("final report cannot be re-rendered")
        self.settings.artifact_path.mkdir(parents=True, exist_ok=True)
        path = self.settings.artifact_path / f"weekly-report-{report_id}-v{report['version']}.png"
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1480, "height": 1000}, device_scale_factor=1)
                page.set_content(report_html(report), wait_until="load")
                page.screenshot(path=str(path), full_page=True, type="png")
                browser.close()
        except Exception as exc:
            raise RenderError(f"Chromium render failed: {exc}") from exc
        timestamp = to_db(now_local())
        self.db.execute(
            """
            UPDATE weekly_report SET image_path=?, image_generated_at=?, workflow_state='rendered',
                updated_at=? WHERE id=?
            """,
            (str(path), timestamp, timestamp, int(report_id)),
        )
        return self.reports.get(report_id)

    def image_path(self, report_id: int) -> Path:
        row = self.db.fetch_one("SELECT image_path FROM weekly_report WHERE id=?", (int(report_id),))
        if not row:
            raise ValueError("weekly report not found")
        path = Path(str(row.get("image_path") or ""))
        if not path.is_file():
            raise ValueError("weekly report image not found")
        return path

    def public_token(self, report_id: int, expires_at: int) -> str:
        secret = self.settings.public_link_secret.strip()
        if not secret:
            raise RenderError("PUBLIC_LINK_SECRET is not configured")
        message = f"{int(report_id)}:{int(expires_at)}".encode("utf-8")
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    def verify_public_token(self, report_id: int, expires_at: int, token: str) -> bool:
        if int(expires_at or 0) < int(time.time()):
            return False
        try:
            expected = self.public_token(report_id, expires_at)
        except RenderError:
            return False
        return hmac.compare_digest(expected, str(token or ""))

    def public_urls(self, report_id: int, *, lifetime_seconds: int | None = None) -> dict[str, str]:
        base = self.settings.public_base_url.strip().rstrip("/")
        if not base:
            return {"reportUrl": "", "imageUrl": ""}
        lifetime = lifetime_seconds or self.settings.public_link_lifetime_days * 86400
        expires_at = int(time.time()) + lifetime
        token = self.public_token(report_id, expires_at)
        query = urlencode({"expires": expires_at, "token": token})
        return {
            "reportUrl": f"{base}/public/reports/{int(report_id)}?{query}",
            "imageUrl": f"{base}/api/public/reports/{int(report_id)}/image?{query}",
        }


report_renderer = ReportRenderer()
