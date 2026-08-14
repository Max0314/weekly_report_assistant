from __future__ import annotations

from typing import Any


# The Base structure is business configuration rather than a credential. Field
# matching remains name-based so field IDs may change without a code release.
SOURCE_TABLES: list[dict[str, Any]] = [
    {
        "key": "visits",
        "tableId": "dEOVLJG",
        "tableName": "拜访交流记录",
        "category": "客户与市场交流",
        "titleFields": ["对象名称"],
        "progressFields": ["主要内容"],
        "eventDateFields": ["交流日期"],
        "productManagerFields": ["产品经理"],
    },
    {
        "key": "tenders",
        "tableId": "0LDcV09",
        "tableName": "市场招投标",
        "category": "市场招投标",
        "titleFields": ["招标名称", "客户"],
        "statusFields": ["状态"],
        "progressFields": ["本周进展"],
        "planFields": ["下周计划"],
        "productManagerFields": ["产品经理"],
    },
    {
        "key": "research",
        "tableId": "2zJbiQc",
        "tableName": "产品策划分析调研",
        "category": "产品策划与调研",
        "titleFields": ["事项标题"],
        "statusFields": ["事项状态"],
        "progressFields": ["当前进展"],
        "planFields": ["后续计划"],
        "productManagerFields": ["产品经理"],
    },
    {
        "key": "projects",
        "tableId": "uRM5L3r",
        "tableName": "重点项目跟踪",
        "category": "重点项目",
        "titleFields": ["项目名称", "项目编号"],
        "statusFields": ["项目状态"],
        "progressFields": ["本周进展"],
        "productManagerFields": ["产品经理"],
        "projectView": True,
    },
    {
        "key": "other",
        "tableId": "qQdy02L",
        "tableName": "其他事项",
        "category": "其他事项",
        "titleFields": ["事项名称", "类型"],
        "statusFields": ["事项状态"],
        "progressFields": ["本周进展"],
        "planFields": ["下周计划"],
        "productManagerFields": ["产品经理"],
    },
    {
        "key": "product_management",
        "tableId": "PoYFuV8",
        "tableName": "产品管理事项",
        "category": "产品管理",
        "titleFields": ["产品管理事项名称"],
        "statusFields": ["事项状态"],
        "progressFields": ["本周进展", "详细信息"],
        "planFields": ["下周计划"],
        "productManagerFields": ["产品经理"],
    },
    {
        "key": "support",
        "tableId": "37mCxrX",
        "tableName": "支持及待办",
        "category": "支持与待办",
        "titleFields": ["支持事项描述"],
        "statusFields": ["状态"],
        "priorityFields": ["紧急程度"],
        "eventDateFields": ["创建日期"],
        "dueDateFields": ["截止日期"],
        "productManagerFields": ["产品经理"],
        "projectManagerFields": ["责任人"],
        "projectView": True,
    },
    {
        "key": "product_manager_roster",
        "tableId": "VdXcedx",
        "tableName": "产品经理名单",
        "roster": True,
        "userFields": ["姓名"],
        "departmentFields": ["所属部门"],
    },
    {
        "key": "weekly_archive",
        "tableId": "2m05o4u",
        "tableName": "周报存档",
        "archive": True,
    },
]


SOURCE_TABLE_BY_ID = {item["tableId"]: item for item in SOURCE_TABLES}


DEFAULT_WORKFLOW_CONFIG: dict[str, Any] = {
    "enabled": True,
    "sourceSyncEnabled": True,
    "sourceSyncIntervalMinutes": 60,
    "sourceFreshnessHours": 26,
    "directorySyncEnabled": True,
    "autoGenerateEnabled": True,
    "autoPreviewEnabled": False,
    "autoFormalSendEnabled": False,
    "requireApproval": True,
    "requirePreviewBeforeFormal": True,
    "generateWeekday": 4,
    "generateHour": 18,
    "generateMinute": 10,
    "periodEndWeekday": 4,
    "periodEndHour": 18,
    "quietStartHour": 21,
    "quietEndHour": 8,
    "dueSoonDays": 14,
    "enforceDirectoryForFormalSend": True,
    "sendGroupImages": True,
    "reportTitle": "产品与项目管理周报",
    "defaultRobotCode": "",
    "previewGroupTargets": [],
    "formalGroupTargets": [],
    "previewPersonalTargets": [],
    "formalPersonalTargets": [],
    "approverTargets": [],
    "approvalCommandScope": "any_configured_group",
    "confirmSendTarget": "formal",
    "projectManagerRoster": [],
    "projectManagerTitleKeywords": ["项目经理"],
    "projectManagerFieldOverrides": {},
    # Optional project background used to classify and summarize source facts.
    # It is configuration context only and must never be presented as weekly progress.
    "projectBaseline": [],
    # Archive write-back is opt-in because every Base can use different field
    # names/IDs. The map is semantic key -> exact fieldId or exact field name.
    "archiveWriteEnabled": False,
    "archiveTableId": "2m05o4u",
    "archiveFieldMap": {},
}
