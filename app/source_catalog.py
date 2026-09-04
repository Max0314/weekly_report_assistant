from __future__ import annotations

from typing import Any


# The Base structure is business configuration rather than a credential. Field
# matching remains name-based so field IDs may change without a code release.
SOURCE_TABLES: list[dict[str, Any]] = [
    {
        "key": "visits",
        "tableId": "dEOVLJG",
        "tableName": "拜访交流记录",
        "categoryKey": "customer_visit",
        "categoryOrder": 10,
        "category": "客户拜访与交流",
        "subcategoryFields": ["交流方式"],
        "titleFields": ["对象名称"],
        "progressFields": ["主要内容"],
        "eventDateFields": ["交流日期"],
        "productManagerFields": ["产品经理"],
        "assigneeFields": [{"fields": ["产品经理"], "role": "产品经理"}],
    },
    {
        "key": "tenders",
        "tableId": "0LDcV09",
        "tableName": "市场招投标",
        "categoryKey": "market_tender",
        "categoryOrder": 20,
        "category": "市场招投标",
        "titleFields": ["招标名称", "客户"],
        "statusFields": ["状态"],
        "progressFields": ["本周进展"],
        "planFields": ["下周计划"],
        "productManagerFields": ["产品经理"],
        "assigneeFields": [{"fields": ["产品经理"], "role": "产品经理"}],
    },
    {
        "key": "research",
        "tableId": "2zJbiQc",
        "tableName": "产品策划分析调研",
        "categoryKey": "product_research",
        "categoryOrder": 30,
        "category": "产品策划分析调研",
        "subcategoryFields": ["事项类型"],
        "titleFields": ["事项标题"],
        "statusFields": ["事项状态"],
        "progressFields": ["当前进展"],
        "planFields": ["后续计划"],
        "productManagerFields": ["产品经理"],
        "assigneeFields": [{"fields": ["产品经理"], "role": "产品经理"}],
    },
    {
        "key": "projects",
        "tableId": "uRM5L3r",
        "tableName": "重点项目跟踪",
        "categoryKey": "key_project",
        "categoryOrder": 40,
        "category": "重点项目跟踪",
        "titleFields": ["项目名称", "项目编号"],
        "statusFields": ["项目状态"],
        "progressFields": ["本周进展"],
        "productManagerFields": ["产品经理"],
        "assigneeFields": [
            {"fields": ["产品经理"], "role": "产品经理"},
            {"fields": ["项目经理", "项目负责人"], "role": "项目负责人"},
        ],
        "projectView": True,
    },
    {
        "key": "other",
        "tableId": "qQdy02L",
        "tableName": "其他事项",
        "categoryKey": "other_work",
        "categoryOrder": 70,
        "category": "其他事项",
        "subcategoryFields": ["类型"],
        "titleFields": ["事项名称", "类型"],
        "statusFields": ["事项状态"],
        "progressFields": ["本周进展"],
        "planFields": ["下周计划"],
        "eventDateFields": ["添加时间"],
        "productManagerFields": ["产品经理"],
        "assigneeFields": [{"fields": ["产品经理"], "role": "产品经理"}],
    },
    {
        "key": "product_management",
        "tableId": "PoYFuV8",
        "tableName": "产品管理事项",
        "categoryKey": "product_management",
        "categoryOrder": 50,
        "category": "产品管理事项",
        "titleFields": ["产品管理事项名称"],
        "statusFields": ["事项状态"],
        "progressFields": ["本周进展", "详细信息"],
        "planFields": ["下周计划"],
        "productManagerFields": ["产品经理"],
        "assigneeFields": [{"fields": ["产品经理"], "role": "产品经理"}],
    },
    {
        "key": "support",
        "tableId": "37mCxrX",
        "tableName": "支持及待办",
        "categoryKey": "support_todo",
        "categoryOrder": 60,
        "category": "支持及待办",
        "titleFields": ["支持事项描述"],
        "statusFields": ["状态"],
        "priorityFields": ["紧急程度"],
        "eventDateFields": ["创建日期"],
        "dueDateFields": ["截止日期"],
        "productManagerFields": ["产品经理"],
        "projectManagerFields": ["责任人"],
        "assigneeFields": [
            {"fields": ["产品经理"], "role": "产品经理"},
            {"fields": ["责任人"], "role": "责任人"},
        ],
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
        "key": "feedback",
        "tableId": "6YCHLaR",
        "tableName": "使用反馈记录",
        "categoryKey": "usage_feedback",
        "categoryOrder": 80,
        "category": "使用反馈与改进",
        "subcategoryFields": ["反馈类型"],
        "titleFields": ["反馈问题描述", "涉及表格/模块"],
        "statusFields": ["处理状态"],
        "progressFields": ["回复及处理结果"],
        "eventDateFields": ["反馈日期"],
        "projectManagerFields": ["责任人"],
        "assigneeFields": [{"fields": ["责任人"], "role": "责任人"}],
    },
    {
        "key": "weekly_archive",
        "tableId": "2m05o4u",
        "tableName": "周报存档",
        "archive": True,
    },
]


SOURCE_TABLE_BY_ID = {item["tableId"]: item for item in SOURCE_TABLES}


TEAMBITION_TABLE_ID = "teambition_tasks"
DEFAULT_TEAMBITION_DEPARTMENTS = [
    "检测技术部",
    "AI应用研发部",
    "物联网事业部",
    "媒体终端软件研发部",
    "通讯终端软件研发部",
    "产品工程部",
    "硬件研发部",
]


DEFAULT_WORKFLOW_CONFIG: dict[str, Any] = {
    "enabled": True,
    "sourceSyncEnabled": True,
    "sourceSyncIntervalMinutes": 60,
    "sourceFreshnessHours": 26,
    "directorySyncEnabled": True,
    "teambitionSyncEnabled": True,
    "teambitionSyncIntervalMinutes": 60,
    "teambitionIncludeInReports": True,
    "teambitionDepartmentNames": DEFAULT_TEAMBITION_DEPARTMENTS,
    "autoGenerateEnabled": True,
    "autoPreviewEnabled": False,
    "autoFormalSendEnabled": False,
    "requireApproval": True,
    "requirePreviewBeforeFormal": True,
    # The production cadence is fixed in SchedulerService: Saturday 09:00
    # generates and sends only to the preview test group; Saturday 17:00
    # sends the final version privately; Sunday 20:00 checks the approved
    # current version for formal delivery.  These legacy display fields are
    # retained for backward-compatible configuration reads only.
    "generateWeekday": 5,
    "generateHour": 9,
    "generateMinute": 0,
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
    # Exactly one enabled recipient is required before the fixed Saturday
    # private-final job can run.  It is configured from the read-only people
    # directory and intentionally has no hard-coded user ID.
    "saturdayFinalPersonalTargets": [],
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
