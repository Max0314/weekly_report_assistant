# 产品与项目经理周报助手

独立于 `bi_center` 的周报服务。它读取钉钉 AI 多维表和 Teambition（TB）任务，通过 `bi_center` 内部接口匹配人员信息，生成产品经理/项目经理/综合周报，并支持“个人或群预览 → 审核人确认 → 个人或群正式推送 → 可撤回”的钉钉机器人链路。

## 设计结论

- **不影响 bi_center**：不导入、不修改其源码和数据库，只调用版本化的只读人员目录接口；员工、部门、业务组及负责人范围缓存到本服务 SQLite，供检索和收件人选择使用。
- **事实与文案分离**：事项数量、负责人、风险、逾期等由程序确定性计算；AI 只归纳 6 个文案区块，失败时自动回退。
- **最小化 AI 数据**：默认不向模型发送人员姓名；按风险优先最多发送 120 条事实，字段截断，完整事实仍保留在本地周报清单。
- **多表快照与可选归档**：按字段名解析 8 张来源表，首次同步建立历史基线；每版周报保存生成时的不可变事实快照。正式发送成功后可按显式 fieldId/字段名映射回写 `周报存档`，默认关闭。
- **TB 工作看板**：兼容 `bi_center` 的 Teambition native/dingtalk 两种只读接口，按钉钉 UserID 映射执行人，缓存任务、项目和同步批次；父任务、归档项目和删除任务不进入看板与周报。
- **人工控制正式发送**：自动同步、自动生成和可选自动预览可以启用，正式发送必须由审核人确认。
- **可追溯**：周报按周期和类型版本化；消息在外部调用前原子占位并保存 `processQueryKey`，支持安全重试和群/个人撤回。
- **人员覆盖**：产品经理名单来自 AI 表，项目经理可由显式名单和 `bi_center` 职位关键词组成；管理页显示缺报人员并可人工发送一次性单聊提醒。
- **子路径部署**：`APP_BASE_PATH`、相对静态资源和前端 API 解析均支持共享域名下的 `/weekly-assistant/`。

详细流程见 [架构与数据口径](docs/architecture.md)，开放平台配置见 [钉钉配置清单](docs/dingtalk-open-platform.md)。

## 本地启动

```bash
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 39057 --workers 1
```

访问 `http://127.0.0.1:39057/`。管理端始终要求 `ADMIN_API_TOKEN`。不要把真实密钥提交到 Git。

## 必填配置

| 配置 | 用途 |
|---|---|
| `APP_BASE_PATH` | 共享域名下的外部子路径，如 `/weekly-assistant` |
| `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` | 企业内部应用访问令牌 |
| `DINGTALK_CALLBACK_TOKEN` | 生产机器人回调的独立共享 Token；为空时生产回调拒绝服务 |
| `DINGTALK_AITABLE_OPERATOR_ID` | 对目标 Base 有读取权限的操作人 |
| `AITABLE_BASE_ID` | AI 多维表 Base ID |
| `BI_CENTER_API_TOKEN` | 人员目录只读 Bearer Token；可按部署决策复用已有 Token，不获得写权限 |
| `TEAMBITION_SOURCE` | `native`（当前与 `bi_center` 正式容器一致）或兼容模式 `dingtalk` |
| `TEAMBITION_OPEN_APP_ID` / `TEAMBITION_OPEN_APP_SECRET` / `TEAMBITION_OPEN_ORGANIZATION_ID` | native OpenAPI 的只读凭证；使用与 `bi_center` 相同的服务器密钥，不进入 Git |
| `TEAMBITION_DINGTALK_APP_KEY` / `TEAMBITION_DINGTALK_APP_SECRET` | dingtalk 模式的企业内部应用凭证；留空时复用本服务的 `DINGTALK_APP_KEY` / `DINGTALK_APP_SECRET` |
| `TEAMBITION_SYNC_ENABLED` | TB 小时级同步的部署总开关；完成手动同步验收后再设为 `true` |
| `ADMIN_API_TOKEN` | 管理接口令牌 |
| `PUBLIC_BASE_URL` / `PUBLIC_LINK_SECRET` | 钉钉可访问的 HTTPS 周报与图片签名链接 |

群的 `openConversationId`、应用机器人的 `robotCode`、个人接收人和审核人的 `userId` 在管理页配置，不进入环境变量。只给一个人推送时，在“从 bi_center 选择个人接收人”中选择该员工；页面会清空群目标，并同时设置个人预览、个人正式接收人和审核人。

个人推送对应的核心配置形状为：

```json
{
  "defaultRobotCode": "ding...",
  "previewGroupTargets": [],
  "formalGroupTargets": [],
  "previewPersonalTargets": [{"name": "接收人", "userId": "user-id", "enabled": true}],
  "formalPersonalTargets": [{"name": "接收人", "userId": "user-id", "enabled": true}],
  "approverTargets": [{"name": "接收人", "userId": "user-id", "enabled": true}]
}
```

AI 模型配置是可选项；`AI_PROVIDER` 标记供应商，`AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` 三项齐全时调用 OpenAI-compatible Chat Completions，否则使用确定性模板并在周报中标记 `fallback/deterministic`。管理页提供与 `bi_center` 一致的 Provider、API Base、模型名、API Key 脱敏、连接测试、保存和恢复部署配置能力；页面保存的是本服务独立覆盖，不会反向修改 `bi_center`。

## TB 工作看板

管理后台的“TB 工作看板”显示进行中、已逾期、7 天内到期和已完成四列任务，支持月份、部门/业务组、状态和关键词筛选。默认同步范围与 `bi_center` 一致，为检测技术部、AI应用研发部、物联网事业部、媒体终端软件研发部、通讯终端软件研发部、产品工程部和硬件研发部。

TB 数据通过官方只读接口直接进入本服务 SQLite；不会读取 `bi_center` 的 TB 数据库，也不会导入或挂载其源码。当前 native 模式沿用 `bi_center` 正式容器的 `open.teambition.com` 链路：先通过 `/idmap/dingtalk/getTbUserId` 映射员工身份，再调用 `/all-task/search`、`/v3/task/query` 和 `/v3/project/query` 拉取任务与项目；兼容模式仍支持钉钉 `/v1.0/project/users/{userId}/tasks/search`。同步后的叶子任务会转换为 `TB任务` 来源事实，默认进入综合版和项目经理版周报；可在看板关闭“纳入项目周报”。

## 周报存档回写

管理页启用“正式发送成功后回写周报存档”前，先读取 `周报存档` 表字段并配置精确映射。至少需要 `archiveKey`、`title`、`periodKey`；`archiveKey` 用于在外部表中查重，避免服务中断后重复新增记录。

```json
{
  "archiveWriteEnabled": true,
  "archiveTableId": "2m05o4u",
  "archiveFieldMap": {
    "archiveKey": "归档键",
    "title": "周报标题",
    "periodKey": "周期",
    "reportKind": "周报类型",
    "version": "版本",
    "summary": "摘要",
    "sentAt": "发送时间",
    "reportUrl": "周报链接"
  }
}
```

映射值可使用字段的精确 `fieldId` 或精确字段名，实际写入统一转换为 `fieldId`。归档失败只记录 `archive_status/archive_error`，不改变 `formal_sent`，也不会重发钉钉消息；管理页可单独“归档/重试”。

## 验证

```bash
python -m unittest discover -s tests -v
python -m compileall -q app tests
node --check static/app.js
```

容器部署：

```bash
docker compose up -d --build
docker compose ps
```

NeoFlow 服务器使用 `APP_PUBLISH_PORT=39022` 将容器内 `39057` 映射到账号已分配的宿主机端口段；公网入口为 `https://neoflow-cn.neo-net.com/weekly-assistant/`。

服务必须保持 `--workers 1` 和单副本；如未来需要水平扩展，应先把调度锁迁移到 Redis/数据库分布式锁。
