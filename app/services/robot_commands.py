from __future__ import annotations

import json
import re
from typing import Any

from ..db import Database, db
from ..integrations.dingtalk_robot.dingtalk_robot import (
    DingTalkRobotClient,
    parse_robot_callback_event,
    robot_client,
)
from ..time_utils import now_local, to_db
from .collector import SourceCollector, source_collector
from .delivery import DeliveryService, delivery_service
from .directory import DirectoryService, directory_service
from .rendering import ReportRenderer, report_renderer
from .reports import ReportService, report_service
from .workflow_config import WorkflowConfigService, workflow_config_service


HELP_TEXT = """### 周报助手

- `周报状态`：查看最新周报状态
- `生成周报`：生成产品与项目管理周报草稿
- `预览周报 [编号]`：推送到预览群
- `确认发送 [编号]`：审核通过并推送正式群
- `需要修改 [编号]：意见`：退回修改
- `取消周报 [编号]`：取消未发送周报
- `撤回周报 [编号]`：撤回已发送消息
- `同步周报数据`：立即同步 AI 表和人员目录

涉及生成、审核、发送和撤回的指令仅允许配置的审核人执行。"""

SENSITIVE_COMMANDS = {"generate", "preview", "confirm", "changes", "cancel", "recall", "sync"}


def _clean_message(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ").strip()
    text = re.sub(r"^@[\w\-\u4e00-\u9fff]+\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_command(value: Any) -> dict[str, Any]:
    text = _clean_message(value)
    lowered = text.lower()
    command_text = text.rstrip("。.!！?？~ ")
    command = "unknown"
    if not text or lowered in {"help", "?", "帮助", "周报帮助", "菜单"}:
        command = "help"
    elif any(item in text for item in ("周报状态", "查看周报", "最新周报")) or lowered == "status":
        command = "status"
    elif command_text in {"同步周报数据", "同步数据", "立即同步"}:
        command = "sync"
    elif re.fullmatch(r"(?:重新)?生成(?:本周)?(?:产品经理|项目经理|综合)?周报", command_text):
        command = "generate"
    elif re.fullmatch(r"(?:预览周报|发送预览)(?:\s*(?:(?:编号|id|#)\s*)?\d+)?", command_text, re.I):
        command = "preview"
    elif re.fullmatch(r"(?:确认发送|审核通过)(?:\s*(?:(?:编号|id|#)\s*)?\d+)?", command_text, re.I):
        command = "confirm"
    elif text.startswith("需要修改") or text.startswith("退回修改"):
        command = "changes"
    elif re.fullmatch(r"取消周报(?:\s*(?:(?:编号|id|#)\s*)?\d+)?", command_text, re.I):
        command = "cancel"
    elif re.fullmatch(r"(?:撤回周报|撤回发送)(?:\s*(?:(?:编号|id|#)\s*)?\d+)?", command_text, re.I):
        command = "recall"
    command_head = re.split(r"[:：]", text, maxsplit=1)[0]
    id_match = re.search(r"(?:编号|id|#)\s*(\d+)", command_head, re.IGNORECASE)
    if not id_match and command in {"preview", "confirm", "changes", "cancel", "recall"}:
        id_match = re.match(r"^(?:预览周报|发送预览|确认发送|审核通过|需要修改|退回修改|取消周报|撤回周报|撤回发送)\s+(\d+)\b", text, re.I)
    reason = ""
    if command == "changes":
        parts = re.split(r"[:：]", text, maxsplit=1)
        if len(parts) > 1:
            reason = parts[1].strip()
        elif id_match:
            reason = text[id_match.end() :].strip(" -，,：:")
    report_kind = "combined"
    if "产品经理" in text or "产品版" in text:
        report_kind = "product"
    elif "项目经理" in text or "项目版" in text:
        report_kind = "project"
    return {
        "command": command,
        "reportId": int(id_match.group(1)) if id_match else None,
        "reason": reason,
        "reportKind": report_kind,
        "normalizedText": text,
    }


class RobotCommandService:
    def __init__(
        self,
        database: Database | None = None,
        reports: ReportService | None = None,
        delivery: DeliveryService | None = None,
        renderer: ReportRenderer | None = None,
        config_service: WorkflowConfigService | None = None,
        collector: SourceCollector | None = None,
        directory: DirectoryService | None = None,
        robot: DingTalkRobotClient | None = None,
    ) -> None:
        self.db = database or db
        self.reports = reports or report_service
        self.delivery = delivery or delivery_service
        self.renderer = renderer or report_renderer
        self.config_service = config_service or workflow_config_service
        self.collector = collector or source_collector
        self.directory = directory or directory_service
        self.robot = robot or robot_client

    def record(self, payload: dict[str, Any]) -> int:
        event = parse_robot_callback_event(payload)
        parsed = parse_command(event["messageText"])
        return self.db.execute(
            """
            INSERT INTO dingtalk_robot_event(
                conversation_id, conversation_title, conversation_type, sender_nick,
                sender_id, robot_code, message_text, command, command_payload,
                handle_status, response_text, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', '', ?, ?)
            """,
            (
                event["openConversationId"], event["conversationTitle"], event["conversationType"],
                event["senderNick"], event["senderId"], event["robotCode"], event["messageText"],
                parsed["command"], json.dumps(parsed, ensure_ascii=False),
                json.dumps(event["raw"], ensure_ascii=False), to_db(now_local()),
            ),
        )

    def _configured_group(
        self,
        conversation_id: str,
        robot_code: str = "",
        *,
        formal_only: bool = False,
    ) -> bool:
        config = self.config_service.get()
        groups = config["formalGroupTargets"] if formal_only else [
            *config["previewGroupTargets"], *config["formalGroupTargets"]
        ]
        return any(
            item.get("enabled") is not False
            and item.get("openConversationId") == conversation_id
            and item.get("robotCode") == robot_code
            for item in groups
        )

    def _configured_robot(self, robot_code: str) -> bool:
        code = str(robot_code or "").strip()
        if not code:
            return False
        config = self.config_service.get()
        if code == str(config.get("defaultRobotCode") or "").strip():
            return True
        targets = [
            *config["previewGroupTargets"],
            *config["formalGroupTargets"],
            *config["previewPersonalTargets"],
            *config["formalPersonalTargets"],
        ]
        return any(
            item.get("enabled") is not False and str(item.get("robotCode") or "").strip() == code
            for item in targets
        )

    def _is_approver(self, sender_id: str) -> bool:
        return any(
            item.get("enabled") is not False and item.get("userId") == sender_id
            for item in self.config_service.get()["approverTargets"]
        )

    def _authorized(self, row: dict[str, Any], command: str) -> tuple[bool, str]:
        if command not in SENSITIVE_COMMANDS:
            return True, ""
        sender_id = str(row.get("sender_id") or "")
        conversation_id = str(row.get("conversation_id") or "")
        robot_code = str(row.get("robot_code") or "")
        conversation_type = str(row.get("conversation_type") or "").lower()
        is_private = any(flag in conversation_type for flag in ("1", "single", "private", "oto"))
        if not sender_id or not self._is_approver(sender_id):
            return False, "该指令仅允许配置的周报审核人执行。"
        config = self.config_service.get()
        formal_only = (
            config.get("approvalCommandScope") == "formal_only"
            and command in {"confirm", "changes", "cancel", "recall"}
        )
        if is_private and not self._configured_robot(robot_code):
            return False, "当前机器人未配置为周报助手。"
        if not is_private and not self._configured_group(conversation_id, robot_code, formal_only=formal_only):
            return False, "当前群未配置为周报预览群或正式群。"
        return True, ""

    @staticmethod
    def _is_private(row: dict[str, Any]) -> bool:
        conversation_type = str(row.get("conversation_type") or "").lower()
        return any(flag in conversation_type for flag in ("1", "single", "private", "oto"))

    def _context_report_ids(self, row: dict[str, Any], command: str) -> list[int]:
        if self._is_private(row):
            sender_id = str(row.get("sender_id") or "")
            logs = self.db.fetch_all(
                """
                SELECT business_id, target_id, snapshot_json FROM dingtalk_robot_send_log
                WHERE business_type='weekly_report' AND target_type='personal' AND send_status='sent'
                ORDER BY id DESC LIMIT 100
                """
            )
            logs = [item for item in logs if sender_id in str(item.get("target_id") or "").split(",")]
        else:
            conversation_id = str(row.get("conversation_id") or "")
            logs = self.db.fetch_all(
                """
                SELECT business_id, target_id, snapshot_json FROM dingtalk_robot_send_log
                WHERE business_type='weekly_report' AND target_type='group'
                  AND conversation_id=? AND send_status='sent'
                ORDER BY id DESC LIMIT 100
                """,
                (conversation_id,),
            )
        result: list[int] = []
        for log in logs:
            try:
                report_id = int(log.get("business_id") or 0)
                snapshot = json.loads(str(log.get("snapshot_json") or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            try:
                report = self.reports.get(report_id)
            except ValueError:
                continue
            state = report.get("workflowState")
            if command in {"confirm", "changes"}:
                if report.get("confirmStatus") != "awaiting":
                    continue
                if not self._is_private(row) and snapshot.get("phase") != "preview":
                    continue
            elif command == "recall":
                if state != "formal_sent" or snapshot.get("phase") != "formal":
                    continue
            if report_id and report_id not in result:
                result.append(report_id)
        return result

    def _resolve_report(self, report_id: int | None, row: dict[str, Any], command: str) -> dict[str, Any]:
        if report_id:
            return self.reports.get(report_id)
        if command in {"confirm", "changes", "recall"}:
            candidate_ids = self._context_report_ids(row, command)
            if len(candidate_ids) == 1:
                return self.reports.get(candidate_ids[0])
            if len(candidate_ids) > 1:
                joined = "、".join(f"#{item}" for item in candidate_ids[:6])
                raise ValueError(f"当前存在多个可处理版本（{joined}），请在指令中写明周报编号。")
            raise ValueError("当前会话没有可关联的周报，请在指令中写明周报编号。")
        if command == "cancel":
            candidates = [
                item for item in self.reports.list(limit=20)
                if item.get("workflowState") not in {"formal_sent", "recalled", "cancelled"}
            ]
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                raise ValueError("存在多个未结束版本，请在取消指令中写明周报编号。")
        if command == "preview":
            candidates = [
                item for item in self.reports.list(limit=20)
                if item.get("workflowState") in {"draft_generated", "rendered", "awaiting_approval", "retryable_error"}
                and item.get("confirmStatus") != "confirmed"
            ]
            if candidates:
                return candidates[0]
        report = self.reports.latest_any()
        if not report:
            raise ValueError("尚未生成周报，请先发送“生成周报”。")
        return report

    @staticmethod
    def _status_text(report: dict[str, Any] | None) -> str:
        if not report:
            return "当前没有已生成的周报。"
        metrics = report.get("metrics") or {}
        return (
            f"最新周报：#{report['id']} {report['title']} v{report['version']}\n"
            f"周期：{(report.get('window') or {}).get('label') or report['periodKey']}\n"
            f"状态：{report['workflowState']}\n"
            f"事项：{metrics.get('itemCount', 0)}，风险：{metrics.get('riskCount', 0)}，"
            f"逾期：{metrics.get('overdueCount', 0)}"
        )

    def _execute(self, row: dict[str, Any], parsed: dict[str, Any]) -> str:
        command = parsed["command"]
        allowed, reason = self._authorized(row, command)
        if not allowed:
            return reason
        actor = str(row.get("sender_id") or row.get("sender_nick") or "dingtalk")
        if command in {"help", "unknown"}:
            return HELP_TEXT
        if command == "status":
            return self._status_text(self.reports.latest_any())
        if command == "sync":
            parts: list[str] = []
            try:
                result = self.directory.refresh(actor=actor)
                parts.append(f"人员目录同步完成：{result['count']} 人")
            except Exception as exc:
                parts.append(f"人员目录同步失败：{exc}")
            try:
                result = self.collector.sync_all(actor=actor)
                parts.append(f"AI 表同步完成：{result['recordCount']} 条，变化 {result['changedCount']} 条")
            except Exception as exc:
                parts.append(f"AI 表同步失败：{exc}")
            return "\n".join(parts)
        if command == "generate":
            report = self.reports.generate(report_kind=parsed["reportKind"], actor=actor)
            return f"已生成周报 #{report['id']} v{report['version']}，共纳入 {report['metrics']['itemCount']} 项。"
        report = self._resolve_report(parsed.get("reportId"), row, command)
        report_id = int(report["id"])
        if command == "preview":
            if not report.get("imageReady"):
                self.renderer.render(report_id)
            result = self.delivery.preview(report_id)
            return f"周报 #{report_id} 预览已处理：成功 {result['sent']}，失败 {result['failed']}。"
        if command == "confirm":
            self.reports.approve(report_id, actor=actor)
            if not self.reports.get(report_id).get("imageReady"):
                self.renderer.render(report_id)
            result = self.delivery.formal(report_id)
            return f"周报 #{report_id} 已审核并正式推送：成功 {result['sent']}，失败 {result['failed']}。"
        if command == "changes":
            reason = parsed.get("reason") or "请在管理端补充修改意见"
            self.reports.request_changes(report_id, actor=actor, reason=reason)
            return f"周报 #{report_id} 已退回修改：{reason}"
        if command == "cancel":
            self.reports.cancel(report_id, actor=actor)
            return f"周报 #{report_id} 已取消。"
        if command == "recall":
            result = self.delivery.recall(report_id)
            return f"周报 #{report_id} 已撤回 {result['recalled']} 条群消息。"
        return HELP_TEXT

    def _reply(self, row: dict[str, Any], text: str) -> None:
        conversation_id = str(row.get("conversation_id") or "")
        robot_code = str(row.get("robot_code") or "")
        sender_id = str(row.get("sender_id") or "")
        if conversation_id and self._configured_group(conversation_id, robot_code):
            self.robot.send_group(
                open_conversation_id=conversation_id,
                robot_code=robot_code,
                msg_key="sampleMarkdown",
                msg_param={"title": "周报助手", "text": text},
            )
        elif sender_id:
            self.robot.send_private(
                [sender_id], robot_code=robot_code, msg_key="sampleMarkdown",
                msg_param={"title": "周报助手", "text": text},
            )

    def handle(self, event_id: int) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM dingtalk_robot_event WHERE id=?", (int(event_id),))
        if not row:
            raise ValueError("robot event not found")
        try:
            parsed = json.loads(str(row.get("command_payload") or "{}"))
            response = self._execute(row, parsed if isinstance(parsed, dict) else {})
            status = "handled"
        except Exception as exc:
            response = f"指令处理失败：{exc}"
            status = "error"
        self.db.execute(
            "UPDATE dingtalk_robot_event SET handle_status=?, response_text=?, handled_at=? WHERE id=?",
            (status, response[:4000], to_db(now_local()), int(event_id)),
        )
        try:
            self._reply(row, response)
        except Exception:
            pass
        return {"id": int(event_id), "status": status, "response": response}

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT id, conversation_title, conversation_type, sender_nick, sender_id,
                   conversation_id, robot_code, message_text, command, handle_status,
                   response_text, created_at, handled_at
            FROM dingtalk_robot_event ORDER BY id DESC LIMIT ?
            """,
            (max(1, min(100, int(limit))),),
        )


robot_command_service = RobotCommandService()
