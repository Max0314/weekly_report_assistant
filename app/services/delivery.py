from __future__ import annotations

import json
import re
import uuid
from typing import Any

from ..db import Database, db
from ..integrations.dingtalk_robot.dingtalk_robot import DingTalkRobotClient, robot_client
from ..time_utils import from_db, now_local, to_db
from .archive import ArchiveService, archive_service
from .directory import DirectoryService, directory_service
from .rendering import ReportRenderer, report_renderer
from .reports import ReportService, report_service
from .workflow_config import WorkflowConfigService, workflow_config_service


class DeliveryError(RuntimeError):
    pass


class DeliveryService:
    def __init__(
        self,
        database: Database | None = None,
        reports: ReportService | None = None,
        renderer: ReportRenderer | None = None,
        config_service: WorkflowConfigService | None = None,
        robot: DingTalkRobotClient | None = None,
        directory: DirectoryService | None = None,
        archive: ArchiveService | None = None,
    ) -> None:
        self.db = database or db
        self.reports = reports or report_service
        self.renderer = renderer or report_renderer
        self.config_service = config_service or workflow_config_service
        self.robot = robot or robot_client
        self.directory = directory or directory_service
        self.archive = archive or archive_service

    def _archive_after_formal(self, report_id: int, *, report_url: str = "") -> dict[str, Any]:
        try:
            archive_config = self.config_service.get()
            needs_report_url = "reportUrl" in (archive_config.get("archiveFieldMap") or {})
            if archive_config.get("archiveWriteEnabled") and needs_report_url and not report_url:
                try:
                    report_url = str(self.renderer.public_urls(report_id).get("reportUrl") or "")
                except Exception:
                    report_url = ""
            return self.archive.write(report_id, report_url=report_url)
        except Exception as exc:
            # The external message has already been delivered. Archive failure
            # is returned and stored independently so a retry never re-sends it.
            return {"status": "error", "skipped": False, "recordId": "", "error": str(exc)}

    @staticmethod
    def _markdown(report: dict[str, Any], *, preview: bool) -> str:
        sections = report.get("sections") or {}
        metrics = report.get("metrics") or {}
        by_status = metrics.get("byStatus") or {}
        prefix = "【预览】" if preview else ""

        def compact(value: Any, limit: int = 160) -> str:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"

        summary = str(sections.get("executiveSummary") or "暂无总结").strip()
        summary_sentences = [
            compact(item)
            for line in summary.splitlines()
            for item in re.findall(r"[^。！？!?]+[。！？!?]*", line)
            if item.strip()
        ][:3] or ["暂无总结"]
        risk_text = str(sections.get("risks") or "").strip()
        risk_items = [
            compact(re.sub(r"^\d{1,2}[.、]\s*", "", item.lstrip("-•* ")), 140)
            for item in re.split(r"\n+|(?<=。)\s*(?=\d{1,2}[.、]\s*)", risk_text)
            if item.strip()
        ][:2]

        lines = [
            f"### {prefix}{report.get('title') or '产品与项目管理周报'}",
            "",
            f"**周期**：{(report.get('window') or {}).get('label') or report.get('periodKey')}",
            f"**版本**：v{report.get('version')}",
            "",
            "---",
            "",
            "**核心数据**",
            "",
            f"- 纳入事项：**{int(metrics.get('itemCount') or 0)}** 项 ｜ 涉及负责人：**{int(metrics.get('managerCount') or 0)}** 人",
            f"- 已完成：**{int(by_status.get('已完成') or 0)}** 项 ｜ 进行中：**{int(by_status.get('进行中') or 0)}** 项",
            f"- 风险：**{int(metrics.get('riskCount') or 0)}** 项 ｜ 逾期：**{int(metrics.get('overdueCount') or 0)}** 项 ｜ 高优先级：**{int(metrics.get('highPriorityCount') or 0)}** 项",
            "",
            "**管理摘要**",
            "",
            *(f"> {item}" for item in summary_sentences),
            "",
            "**风险聚焦**",
            "",
            *(f"{index}. {item}" for index, item in enumerate(risk_items, start=1)),
            *( ["- 暂无需要单独聚焦的风险。"] if not risk_items else [] ),
        ]
        if preview:
            lines.extend(
                [
                    "",
                    "---",
                    "",
                    "**审核操作**",
                    "",
                    "- 通过：回复 `确认发送`",
                    "- 退回：回复 `需要修改：具体意见`",
                ]
            )
        return "\n".join(lines)

    def _claim_send(self, idempotency_key: str) -> str:
        """Atomically reserve a send key before making the external API call."""
        timestamp = to_db(now_local())
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT send_status, created_at FROM dingtalk_robot_send_log WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing and str(existing["send_status"] or "") == "sent":
                return "sent"
            if existing and str(existing["send_status"] or "") == "pending":
                claimed_at = from_db(existing["created_at"])
                if claimed_at and (now_local() - claimed_at).total_seconds() < 600:
                    return "pending"
            if existing:
                connection.execute(
                    """
                    UPDATE dingtalk_robot_send_log
                    SET send_status='pending', process_query_key='', error_text='', created_at=?
                    WHERE idempotency_key=?
                    """,
                    (timestamp, idempotency_key),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO dingtalk_robot_send_log(send_status, idempotency_key, created_at)
                    VALUES ('pending', ?, ?)
                    """,
                    (idempotency_key, timestamp),
                )
        return "claimed"

    def _log(
        self,
        *,
        report_id: int,
        target_type: str,
        target_name: str,
        target_id: str,
        conversation_id: str,
        robot_code: str,
        msg_key: str,
        result: dict[str, Any],
        idempotency_key: str,
        snapshot: dict[str, Any],
        business_type: str = "weekly_report",
        business_id: str = "",
    ) -> None:
        self.db.execute(
            """
            INSERT INTO dingtalk_robot_send_log(
                business_type, business_id, target_type, target_name, target_id,
                conversation_id, robot_code, msg_key, process_query_key, send_status,
                error_text, snapshot_json, idempotency_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                business_type=excluded.business_type, business_id=excluded.business_id,
                target_type=excluded.target_type, target_name=excluded.target_name,
                target_id=excluded.target_id, conversation_id=excluded.conversation_id,
                robot_code=excluded.robot_code, msg_key=excluded.msg_key,
                process_query_key=excluded.process_query_key, send_status=excluded.send_status,
                error_text=excluded.error_text, snapshot_json=excluded.snapshot_json,
                created_at=excluded.created_at
            """,
            (
                business_type,
                business_id or str(report_id),
                target_type,
                target_name,
                target_id,
                conversation_id,
                robot_code,
                msg_key,
                str(result.get("processQueryKey") or ""),
                "sent" if result.get("sent") else "error",
                str(result.get("error") or "")[:2000],
                json.dumps(snapshot, ensure_ascii=False),
                idempotency_key,
                to_db(now_local()),
            ),
        )

    def _send_targets(
        self,
        report_id: int,
        *,
        preview: bool,
        groups: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        config = self.config_service.get()
        report = self.reports.get(report_id)
        group_targets = groups if groups is not None else (
            config["previewGroupTargets"] if preview else config["formalGroupTargets"]
        )
        personal_targets = [] if groups is not None else (
            config["previewPersonalTargets"] if preview else config["formalPersonalTargets"]
        )
        targets = [
            {
                "type": "group",
                "name": str(item.get("name") or "钉钉群"),
                "targetId": str(item.get("openConversationId") or ""),
                "conversationId": str(item.get("openConversationId") or ""),
                "robotCode": str(item.get("robotCode") or ""),
            }
            for item in group_targets
            if item.get("enabled") is not False
        ]
        targets.extend(
            {
                "type": "personal",
                "name": str(item.get("name") or item.get("userId") or "审核人"),
                "targetId": str(item.get("userId") or ""),
                "conversationId": "",
                "robotCode": str(item.get("robotCode") or config.get("defaultRobotCode") or ""),
            }
            for item in personal_targets
            if item.get("enabled") is not False
        )
        if not targets:
            raise DeliveryError("preview target is not configured" if preview else "formal target is not configured")
        if preview and (
            report["workflowState"] in {"formal_sent", "recalled", "cancelled", "need_changes"}
            or report.get("confirmStatus") == "confirmed"
        ):
            raise DeliveryError("this report is no longer eligible for preview")
        if not preview:
            if report["workflowState"] == "formal_sent":
                return {
                    "sent": 0,
                    "failed": 0,
                    "skipped": True,
                    "results": [],
                    "archive": self._archive_after_formal(report_id),
                    "report": self.reports.get(report_id),
                }
            if report["workflowState"] in {"recalled", "cancelled"}:
                raise DeliveryError("recalled or cancelled report cannot be sent again; generate a new version")
            if config.get("requirePreviewBeforeFormal") and not report.get("previewedAt"):
                raise DeliveryError("report must be previewed before formal delivery")
            if config.get("requireApproval") and report.get("confirmStatus") != "confirmed":
                raise DeliveryError("report must be approved before formal delivery")
            if config.get("enforceDirectoryForFormalSend") and self.directory.cache_status()["count"] <= 0:
                raise DeliveryError("bi_center employee directory cache is empty; formal delivery is blocked")
        if config.get("sendGroupImages") and not report.get("imageReady"):
            raise DeliveryError("report image must be rendered before delivery")
        try:
            urls = self.renderer.public_urls(report_id)
        except Exception as exc:
            if config.get("sendGroupImages"):
                raise DeliveryError(str(exc)) from exc
            urls = {"reportUrl": "", "imageUrl": ""}
        if config.get("sendGroupImages") and not urls.get("imageUrl"):
            raise DeliveryError("PUBLIC_BASE_URL and PUBLIC_LINK_SECRET are required for image delivery")
        markdown = self._markdown(report, preview=preview)
        phase = "preview" if preview else "formal"
        results: list[dict[str, Any]] = []
        sent = 0
        failed = 0
        for target in targets:
            target_type = str(target["type"])
            target_id = str(target["targetId"])
            conversation_id = str(target["conversationId"])
            robot_code = str(target["robotCode"])
            name = str(target["name"])
            card_key = f"report:{report_id}:{phase}:{target_type}:{target_id}:{robot_code}:card"
            claim = self._claim_send(card_key)
            if claim == "sent":
                card_result = {"sent": True, "skipped": True, "processQueryKey": "", "error": ""}
            elif claim == "pending":
                raise DeliveryError(f"delivery is already in progress for {name}")
            else:
                msg_param: dict[str, Any] = {
                    "title": f"{'【预览】' if preview else ''}{report['title']}",
                    "text": markdown,
                }
                msg_key = "sampleMarkdown"
                if urls.get("reportUrl"):
                    msg_key = "sampleActionCard"
                    msg_param.update({"singleTitle": "查看周报", "singleURL": urls["reportUrl"]})
                if target_type == "group":
                    card_result = self.robot.send_group(
                        open_conversation_id=conversation_id,
                        robot_code=robot_code,
                        msg_key=msg_key,
                        msg_param=msg_param,
                    )
                else:
                    card_result = self.robot.send_private(
                        [target_id], robot_code=robot_code, msg_key=msg_key, msg_param=msg_param
                    )
                self._log(
                    report_id=report_id,
                    target_type=target_type,
                    target_name=name,
                    target_id=target_id,
                    conversation_id=conversation_id,
                    robot_code=robot_code,
                    msg_key=msg_key,
                    result=card_result,
                    idempotency_key=card_key,
                    snapshot={"phase": phase, "reportUrl": urls.get("reportUrl")},
                )
            results.append({"target": name, "messageType": "card", **card_result})
            sent += 1 if card_result.get("sent") else 0
            failed += 0 if card_result.get("sent") else 1
            if not (config.get("sendGroupImages") and urls.get("imageUrl")):
                continue
            image_key = f"report:{report_id}:{phase}:{target_type}:{target_id}:{robot_code}:image"
            image_claim = self._claim_send(image_key)
            if image_claim == "sent":
                image_result = {"sent": True, "skipped": True, "processQueryKey": "", "error": ""}
            elif image_claim == "pending":
                raise DeliveryError(f"image delivery is already in progress for {name}")
            else:
                if target_type == "group":
                    image_result = self.robot.send_group(
                        open_conversation_id=conversation_id,
                        robot_code=robot_code,
                        msg_key="sampleImageMsg",
                        msg_param={"photoURL": urls["imageUrl"]},
                    )
                else:
                    image_result = self.robot.send_private(
                        [target_id], robot_code=robot_code, msg_key="sampleImageMsg",
                        msg_param={"photoURL": urls["imageUrl"]},
                    )
                self._log(
                    report_id=report_id,
                    target_type=target_type,
                    target_name=name,
                    target_id=target_id,
                    conversation_id=conversation_id,
                    robot_code=robot_code,
                    msg_key="sampleImageMsg",
                    result=image_result,
                    idempotency_key=image_key,
                    snapshot={"phase": phase, "imageUrl": urls.get("imageUrl")},
                )
            results.append({"target": name, "messageType": "image", **image_result})
            sent += 1 if image_result.get("sent") else 0
            failed += 0 if image_result.get("sent") else 1
        timestamp = to_db(now_local())
        if preview:
            state = "awaiting_approval" if sent and not failed else "retryable_error"
            self.db.execute(
                """
                UPDATE weekly_report SET workflow_state=?, previewed_at=?, confirm_status='awaiting',
                    send_error=?, updated_at=? WHERE id=?
                """,
                (state, timestamp if sent else "", "; ".join(str(item.get("error") or "") for item in results if item.get("error"))[:2000], timestamp, report_id),
            )
            if sent and not failed:
                self.send_approval_request(report_id)
        else:
            state = "formal_sent" if sent and not failed else "retryable_error"
            send_status = "sent" if sent and not failed else "partial" if sent else "error"
            error = "; ".join(str(item.get("error") or "") for item in results if item.get("error"))[:2000]
            self.db.execute(
                """
                UPDATE weekly_report SET workflow_state=?, send_status=?, send_error=?, sent_at=?, updated_at=? WHERE id=?
                """,
                (state, send_status, error, timestamp if sent else "", timestamp, report_id),
            )
        archive_result = (
            self._archive_after_formal(report_id, report_url=str(urls.get("reportUrl") or ""))
            if not preview and state == "formal_sent"
            else {"status": "not_attempted", "skipped": True, "recordId": "", "error": ""}
        )
        return {
            "sent": sent,
            "failed": failed,
            "results": results,
            "archive": archive_result,
            "report": self.reports.get(report_id),
        }

    def preview(self, report_id: int, *, groups: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return self._send_targets(report_id, preview=True, groups=groups)

    def formal(self, report_id: int) -> dict[str, Any]:
        return self._send_targets(report_id, preview=False)

    def send_approval_request(self, report_id: int) -> dict[str, Any]:
        config = self.config_service.get()
        approvers = [item for item in config["approverTargets"] if item.get("enabled") is not False]
        preview_personal_ids = {
            str(item.get("userId") or "")
            for item in config["previewPersonalTargets"]
            if item.get("enabled") is not False and item.get("userId")
        }
        user_ids = [
            str(item.get("userId") or "")
            for item in approvers
            if item.get("userId") and str(item.get("userId")) not in preview_personal_ids
        ]
        if not user_ids:
            if approvers and preview_personal_ids:
                return {"sent": True, "skipped": True, "error": "", "processQueryKey": ""}
            return {"sent": False, "error": "approver is not configured"}
        groups = config["previewGroupTargets"] or config["formalGroupTargets"]
        personal = config["previewPersonalTargets"] or config["formalPersonalTargets"]
        robot_code = str(
            config.get("defaultRobotCode")
            or ((groups[0] if groups else {}).get("robotCode") if groups else "")
            or ((personal[0] if personal else {}).get("robotCode") if personal else "")
            or ""
        )
        report = self.reports.get(report_id)
        approval_key = f"report:{report_id}:approval:{uuid.uuid5(uuid.NAMESPACE_URL, ','.join(sorted(user_ids)))}"
        claim = self._claim_send(approval_key)
        if claim == "sent":
            return {"sent": True, "skipped": True, "error": "", "processQueryKey": ""}
        if claim == "pending":
            return {"sent": False, "pending": True, "error": "approval request is already in progress"}
        result = self.robot.send_private(
            user_ids,
            robot_code=robot_code,
            msg_key="sampleMarkdown",
            msg_param={
                "title": "周报待审核",
                "text": self._markdown(report, preview=True),
            },
        )
        self._log(
            report_id=report_id,
            target_type="personal",
            target_name="审核人",
            target_id=",".join(user_ids),
            conversation_id="",
            robot_code=robot_code,
            msg_key="sampleMarkdown",
            result=result,
            idempotency_key=approval_key,
            snapshot={"phase": "approval", "approvers": [item.get("name") for item in approvers]},
        )
        return result

    def send_coverage_reminders(
        self,
        *,
        period_key: str = "",
        report_kind: str = "combined",
    ) -> dict[str, Any]:
        config = self.config_service.get()
        coverage = self.reports.manager_coverage(period_key=period_key, report_kind=report_kind)
        missing = [item for item in coverage["missing"] if str(item.get("userId") or "").strip()]
        robot_code = str(config.get("defaultRobotCode") or "").strip()
        if not robot_code:
            targets = [
                *config["previewPersonalTargets"],
                *config["formalPersonalTargets"],
                *config["previewGroupTargets"],
                *config["formalGroupTargets"],
            ]
            robot_code = next(
                (str(item.get("robotCode") or "").strip() for item in targets if item.get("robotCode")),
                "",
            )
        if missing and not robot_code:
            raise DeliveryError("defaultRobotCode is required for coverage reminders")
        results: list[dict[str, Any]] = []
        business_id = f"{coverage['periodKey']}:{report_kind}"
        for person in missing:
            user_id = str(person["userId"])
            role_name = "项目经理" if person.get("role") == "project" else "产品经理"
            key = f"coverage-reminder:{business_id}:{person.get('role')}:{user_id}"
            claim = self._claim_send(key)
            if claim == "sent":
                result = {"sent": True, "skipped": True, "error": "", "processQueryKey": ""}
            elif claim == "pending":
                result = {"sent": False, "pending": True, "error": "reminder is already in progress"}
            else:
                result = self.robot.send_private(
                    [user_id],
                    robot_code=robot_code,
                    msg_key="sampleMarkdown",
                    msg_param={
                        "title": "周报信息待补充",
                        "text": (
                            f"### 周报信息待补充\n\n"
                            f"{person.get('name') or role_name}，当前 {coverage['label']} 的{role_name}周报汇总"
                            "尚未匹配到你的有效事项。请检查 AI 表中的负责人、进展、计划和风险信息。"
                        ),
                    },
                )
                self._log(
                    report_id=0,
                    target_type="personal",
                    target_name=str(person.get("name") or user_id),
                    target_id=user_id,
                    conversation_id="",
                    robot_code=robot_code,
                    msg_key="sampleMarkdown",
                    result=result,
                    idempotency_key=key,
                    snapshot={"phase": "coverage_reminder", "coverage": person},
                    business_type="weekly_report_reminder",
                    business_id=business_id,
                )
            results.append({"target": str(person.get("name") or user_id), **result})
        return {
            "periodKey": coverage["periodKey"],
            "reportKind": report_kind,
            "missing": coverage["missingCount"],
            "eligible": len(missing),
            "sent": sum(1 for item in results if item.get("sent") and not item.get("skipped")),
            "failed": sum(1 for item in results if not item.get("sent")),
            "results": results,
        }

    def recall(self, report_id: int) -> dict[str, Any]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM dingtalk_robot_send_log
            WHERE business_type='weekly_report' AND business_id=?
              AND target_type IN ('group', 'personal')
              AND send_status='sent' AND process_query_key<>''
            ORDER BY id DESC
            """,
            (str(report_id),),
        )
        formal_rows: list[dict[str, Any]] = []
        for row in rows:
            try:
                snapshot = json.loads(str(row.get("snapshot_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                snapshot = {}
            if snapshot.get("phase") == "formal":
                formal_rows.append(row)
        results: list[dict[str, Any]] = []
        for row in formal_rows:
            if str(row.get("target_type") or "") == "personal":
                result = self.robot.recall_private(
                    robot_code=str(row.get("robot_code") or ""),
                    process_query_keys=[str(row.get("process_query_key") or "")],
                )
            else:
                result = self.robot.recall_group(
                    open_conversation_id=str(row.get("conversation_id") or ""),
                    robot_code=str(row.get("robot_code") or ""),
                    process_query_keys=[str(row.get("process_query_key") or "")],
                )
            if result.get("recalled"):
                self.db.execute(
                    "UPDATE dingtalk_robot_send_log SET send_status='recalled', error_text='' WHERE id=?",
                    (int(row["id"]),),
                )
            results.append({"logId": row["id"], **result})
        if not formal_rows:
            raise DeliveryError("no recallable formal processQueryKey was found")
        recalled = sum(1 for item in results if item.get("recalled"))
        failed = len(results) - recalled
        timestamp = to_db(now_local())
        if recalled and not failed:
            self.db.execute(
                "UPDATE weekly_report SET workflow_state='recalled', send_status='recalled', updated_at=? WHERE id=?",
                (timestamp, int(report_id)),
            )
        else:
            error = "; ".join(str(item.get("error") or "") for item in results if item.get("error"))[:2000]
            self.db.execute(
                """
                UPDATE weekly_report SET workflow_state='retryable_error', send_status='partial',
                    send_error=?, updated_at=? WHERE id=?
                """,
                (error, timestamp, int(report_id)),
            )
        return {
            "recalled": recalled,
            "failed": failed,
            "results": results,
            "report": self.reports.get(report_id),
        }


delivery_service = DeliveryService()
