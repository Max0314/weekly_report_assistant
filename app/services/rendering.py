from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
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


_NUMBERED_ITEM_RE = re.compile(r"(?:^|(?<=[\s。；;！？!?：:]))(\d{1,2})[.、]\s+", re.MULTILINE)


def _section_items(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    matches = list(_NUMBERED_ITEM_RE.finditer(text))
    numbers = [int(match.group(1)) for match in matches]
    if len(matches) >= 2 and numbers[0] == 1 and all(
        current == previous + 1 for previous, current in zip(numbers, numbers[1:])
    ):
        return [
            text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
            for index, match in enumerate(matches)
            if text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)].strip()
        ]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [re.sub(r"^[\-•*]\s*", "", line) for line in lines]


def _section_html(value: Any) -> str:
    items = _section_items(value)
    if not items:
        return '<p class="empty">暂无</p>'
    return f'<ol class="section-list">{"".join(f"<li>{html.escape(item)}</li>" for item in items)}</ol>'


def _summary_html(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return '<p class="empty">暂无总结</p>'
    sentences = []
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        sentences.extend(part.strip() for part in re.findall(r"[^。！？!?]+[。！？!?]*", line) if part.strip())
    return "".join(f"<p>{html.escape(sentence)}</p>" for sentence in sentences)


def report_html(
    report: dict[str, Any],
    *,
    interactive: bool = False,
    personal_report_url: str = "",
    edit_report_url: str = "",
) -> str:
    sections = report.get("sections") or {}
    metrics = report.get("metrics") or {}
    window = report.get("window") or {}
    sources = report.get("sources") or []
    category_sections = sections.get("categorySections") or ReportService._category_sections(sources)
    category_cards = "".join(
        f'<div class="metric"><span>{html.escape(str(name))}</span><strong>{int(count or 0)}</strong></div>'
        for name, count in (metrics.get("byCategory") or {}).items()
    )
    source_rows = "".join(
        '<tr class="fact-row">'
        f'<td class="fact-category" data-label="类别">{html.escape(str(item.get("category") or "-"))}</td>'
        f'<td class="fact-title" data-label="事项">{html.escape(str(item.get("title") or "-"))}</td>'
        f'<td class="fact-status" data-label="状态">{html.escape(str(item.get("status") or "-"))}</td>'
        f'<td data-label="负责人">{html.escape("、".join(dict.fromkeys(str(entry.get("name") or entry.get("userId") or "") for entry in item.get("assignees") or [] if isinstance(entry, dict))) or "-")}</td>'
        f'<td data-label="截止">{html.escape(str(item.get("dueAt") or "-").split("T")[0])}</td>'
        "</tr>"
        for item in sources[:80]
    )
    category_section_cards = "".join(
        '<article class="card category-card">'
        f'<div class="category-heading"><h2>{html.escape(str(section.get("label") or "未分类"))}</h2>'
        f'<span>{int(section.get("itemCount") or 0)} 项</span></div>'
        f'{_section_html(section.get("digest") or section.get("content"))}'
        '</article>'
        for section in category_sections
        if isinstance(section, dict)
    )
    personal_action = (
        f'<a class="personal-open" href="{html.escape(personal_report_url, quote=True)}">'
        '查看个人周报</a>'
        if interactive and personal_report_url else ""
    )
    edit_action = (
        f'<a class="edit-open" href="{html.escape(edit_report_url, quote=True)}" '
        'target="_blank" rel="noopener noreferrer">✎ 编辑周报</a>'
        if interactive and edit_report_url else ""
    )
    interactive_actions = (
        '<div class="hero-actions"><span class="readonly-pill">只读浏览</span>'
        f'{personal_action}'
        f'{edit_action}'
        '<a id="externalOpen" class="external-open" target="_blank" rel="noopener noreferrer" '
        'aria-label="在外部浏览器打开周报">↗ 外部打开</a></div>'
        if interactive else ""
    )
    interactive_script = (
        '<script>document.getElementById("externalOpen").href=window.location.href;</script>'
        if interactive else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(str(report.get('title') or '周报'))}</title>
<style>
*{{box-sizing:border-box}} html{{background:#eef3f9}} body{{margin:0;overflow-x:hidden;background:#eef3f9;color:#142033;font-family:'Noto Sans CJK SC','Microsoft YaHei',sans-serif}}
.page{{width:100%;max-width:1480px;margin:0 auto;background:#f8fafc;min-height:900px;padding:48px}}
.hero{{border-radius:28px;padding:38px 44px;color:#fff;background:linear-gradient(135deg,#173b68,#24689d 56%,#0f8a82)}}
.hero-heading{{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}}
.hero h1{{font-size:42px;margin:0 0 14px}} .hero p{{font-size:20px;margin:0;opacity:.9}}
.hero-actions{{display:flex;align-items:center;justify-content:flex-end;flex-wrap:wrap;gap:8px;flex:0 0 auto}}
.readonly-pill{{display:inline-flex;flex:0 0 auto;padding:8px 13px;border:1px solid rgba(255,255,255,.3);border-radius:999px;background:rgba(255,255,255,.12);font-size:14px;font-weight:700}}
.personal-open,.edit-open,.external-open{{display:inline-flex;align-items:center;padding:8px 13px;border-radius:999px;text-decoration:none;font-size:14px;font-weight:800}}
.personal-open{{border:1px solid rgba(255,255,255,.34);background:rgba(255,255,255,.14);color:#fff}}
.edit-open{{border:1px solid rgba(255,255,255,.34);background:rgba(255,255,255,.14);color:#fff}}
.external-open{{background:#fff;color:#17517a;box-shadow:0 5px 14px rgba(0,0,0,.12)}}
.personal-open:hover,.edit-open:hover{{background:rgba(255,255,255,.22)}}
.external-open:hover{{background:#eff8ff}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;margin:24px 0}}
.stat,.metric,.card{{background:#fff;border:1px solid #dce5ef;border-radius:18px;box-shadow:0 8px 24px rgba(23,59,104,.06)}}
.stat{{padding:20px}} .stat span,.metric span{{display:block;color:#5d6c80;font-size:15px}} .stat strong{{font-size:34px;color:#173b68}}
.lead{{padding:26px 30px;background:#fff;border-left:8px solid #0f8a82;border-radius:18px;font-size:20px;line-height:1.75}}
.lead p{{margin:0}} .lead p+p{{margin-top:8px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:24px;align-items:start}} .card{{padding:26px 30px}}
.grid .card:last-child{{grid-column:1/-1}}
.category-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;margin-top:24px;align-items:start}}
.category-heading{{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:16px}}
.category-heading h2{{margin:0}}
.category-heading span{{display:inline-flex;padding:5px 9px;color:#315a9e;border-radius:999px;background:#eef4ff;font-size:12px;font-weight:750}}
.card h2{{font-size:25px;margin:0 0 16px;color:#173b68}}
.section-list{{list-style:none;counter-reset:section-item;padding:0;margin:0}}
.section-list li{{counter-increment:section-item;display:grid;grid-template-columns:28px minmax(0,1fr);gap:10px;font-size:18px;line-height:1.65;margin:12px 0}}
.section-list li::before{{content:counter(section-item);display:flex;align-items:center;justify-content:center;width:24px;height:24px;margin-top:3px;border-radius:50%;background:#e8f1f8;color:#24689d;font-size:13px;font-weight:700}}
.risk h2{{color:#b24b2d}} .metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}}
.metric{{padding:16px}} .metric strong{{font-size:25px;color:#24689d}}
.table-card{{margin-top:24px;padding:0;overflow:hidden}}
.table-card > summary{{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:22px 30px;cursor:pointer;list-style:none}}
.table-card > summary::-webkit-details-marker{{display:none}}
.table-card > summary h2{{margin:0}}
.table-card > summary span{{color:#718096;font-size:14px;font-weight:600}}
.table-card > summary span::after{{content:'展开'}}
.table-card[open] > summary span::after{{content:'收起'}}
.fact-table-wrap{{padding:0 30px 26px}}
table{{width:100%;border-collapse:collapse;table-layout:fixed}} th,td{{padding:13px 12px;border-bottom:1px solid #e4eaf1;text-align:left;font-size:15px;line-height:1.55;vertical-align:top;overflow-wrap:anywhere}} th{{color:#526276;background:#f3f6fa}}
th:nth-child(1){{width:18%}} th:nth-child(3){{width:11%}} th:nth-child(4){{width:12%}} th:nth-child(5){{width:11%}}
.fact-category,.fact-status{{color:#526276}} .fact-title{{font-weight:600;color:#1c3656}}
.foot{{margin-top:26px;color:#718096;text-align:right;font-size:14px}} .empty{{color:#8492a6}}
@media(max-width:1200px){{
  .page{{padding:28px}}
  .hero{{padding:30px 34px}}
}}
@media(max-width:900px){{
  .page{{width:100%;padding:14px}}
  .hero{{padding:22px 20px;border-radius:20px}} .hero-heading{{gap:14px}} .hero h1{{font-size:29px;line-height:1.22;margin-bottom:9px}} .hero p{{font-size:14px;line-height:1.5}} .readonly-pill{{padding:6px 9px;font-size:11px}}
  .stats{{grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}}
  .stat{{padding:12px;border-radius:14px}} .stat span{{font-size:12px}} .stat strong{{font-size:24px;line-height:1.2}}
  .lead{{padding:17px 18px;border-left-width:5px;border-radius:15px;font-size:16px;line-height:1.75}} .lead p+p{{margin-top:7px}}
  .metrics{{grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0}}
  .metric{{padding:12px;border-radius:14px}} .metric span{{font-size:12px;line-height:1.35}} .metric strong{{font-size:21px}}
  .grid{{grid-template-columns:1fr;gap:12px;margin-top:14px}} .grid .card:last-child{{grid-column:auto}}
  .category-grid{{grid-template-columns:1fr;gap:12px;margin-top:14px}}
  .card{{padding:18px;border-radius:15px}} .card h2{{font-size:20px;margin-bottom:12px}}
  .section-list li{{grid-template-columns:24px minmax(0,1fr);gap:8px;font-size:15.5px;line-height:1.7;margin:10px 0}}
  .section-list li::before{{width:21px;height:21px;margin-top:3px;font-size:11px}}
  .table-card{{margin-top:14px}}
  .table-card > summary{{padding:16px 18px}} .table-card > summary h2{{font-size:20px}} .table-card > summary span{{font-size:12px}}
  .fact-table-wrap{{padding:0 18px 18px}}
  table,tbody,tr,td{{display:block;width:100%}} thead{{display:none}}
  .fact-row{{margin-bottom:10px;padding:12px 13px;border:1px solid #e1e8f0;border-radius:13px;background:#f8fafc}}
  .fact-row:last-child{{margin-bottom:0}}
  .fact-row td{{display:grid;grid-template-columns:66px minmax(0,1fr);gap:9px;padding:6px 0;border:0;font-size:14px;line-height:1.55;overflow-wrap:break-word;word-break:normal}}
  .fact-row td::before{{content:attr(data-label);color:#7a8799;font-size:12px;font-weight:500}}
  .fact-row .fact-title{{font-size:15px}}
  .fact-category,.fact-status{{color:#1c5e86;font-weight:600}}
  .foot{{margin-top:16px;font-size:11px;line-height:1.6;text-align:left}}
}}
@media(max-width:520px){{
  .page{{padding:10px}} .hero{{padding:20px 17px}} .hero-heading{{display:block}} .hero h1{{font-size:26px}} .hero-actions{{justify-content:flex-start;margin-top:14px}} .readonly-pill,.personal-open,.edit-open,.external-open{{padding:6px 9px;font-size:11px}}
  .metrics{{grid-template-columns:repeat(2,1fr)}}
}}
</style></head><body><main class="page">
<section class="hero"><div class="hero-heading"><div><h1>{html.escape(str(report.get('title') or '产品与项目管理周报'))}</h1><p>{html.escape(str(window.get('label') or report.get('periodKey') or ''))} · v{int(report.get('version') or 0)}</p></div>{interactive_actions}</div></section>
<section class="stats">
<div class="stat"><span>纳入事项</span><strong>{int(metrics.get('itemCount') or 0)}</strong></div>
<div class="stat"><span>涉及负责人</span><strong>{int(metrics.get('managerCount') or 0)}</strong></div>
<div class="stat"><span>风险事项</span><strong>{int(metrics.get('riskCount') or 0)}</strong></div>
<div class="stat"><span>逾期事项</span><strong>{int(metrics.get('overdueCount') or 0)}</strong></div>
<div class="stat"><span>高优先级</span><strong>{int(metrics.get('highPriorityCount') or 0)}</strong></div>
</section>
<section class="lead">{_summary_html(sections.get('executiveSummary'))}</section>
<section class="metrics">{category_cards}</section>
<section class="category-grid">{category_section_cards or '<article class="card"><p class="empty">本周期暂无分类事项</p></article>'}</section>
<section class="grid">
<article class="card risk"><h2>风险与待跟进</h2>{_section_html(sections.get('risks'))}</article>
<article class="card"><h2>下周计划</h2>{_section_html(sections.get('nextPlans'))}</article>
<article class="card"><h2>需协调与支持</h2>{_section_html(sections.get('supportNeeds'))}</article>
</section>
<details class="card table-card fact-details"><summary><h2>本周事实清单</h2><span aria-hidden="true"></span></summary><div class="fact-table-wrap"><table><thead><tr><th>类别</th><th>事项</th><th>状态</th><th>负责人</th><th>截止</th></tr></thead><tbody>{source_rows or '<tr><td colspan="5" class="empty">暂无</td></tr>'}</tbody></table></div></details>
<div class="foot">由周报助手根据 AI 多维表快照生成；统计数字由程序计算，AI 仅用于归纳文案。</div>
</main>{interactive_script}</body></html>"""


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

    def personal_report_url(self, report_id: int) -> str:
        base = self.settings.public_base_url.strip().rstrip("/")
        if not base or not self.settings.dingtalk_sso_configured:
            return ""
        return f"{base}/api/public/personal-reports/{int(report_id)}/open"

    def personal_report_app_url(self, report_id: int) -> str:
        base = self.settings.public_base_url.strip().rstrip("/")
        if not base or not self.settings.dingtalk_sso_configured:
            return ""
        return f"{base}/#/personal-reports?reportId={int(report_id)}"

    def edit_report_app_url(self, report_id: int) -> str:
        base = self.settings.public_base_url.strip().rstrip("/")
        if not base or not self.settings.dingtalk_sso_configured:
            return ""
        return f"{base}/#/reports?editReportId={int(report_id)}"


report_renderer = ReportRenderer()
