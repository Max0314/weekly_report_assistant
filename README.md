# 产品与项目经理周报助手

独立于 `bi_center` 的周报服务。它读取钉钉 AI 多维表，并仅为多维表“重点项目跟踪”中的项目补充 Teambition（TB）项目状态；通过 `bi_center` 内部接口匹配人员信息，生成产品经理/项目经理/综合周报，并支持“个人或群预览 → 审核人确认 → 个人或群正式推送 → 可撤回”的钉钉机器人链路。

## 设计结论

- **不影响 bi_center**：不导入、不修改其源码和数据库，只调用版本化的只读人员目录接口；员工、部门、业务组及负责人范围缓存到本服务 SQLite，供检索和收件人选择使用。
- **事实与文案分离**：事项数量、负责人、风险、逾期等由程序确定性计算；AI 只归纳 6 个文案区块，失败时自动回退。
- **最小化 AI 数据**：默认不向模型发送人员姓名；按风险优先最多发送 120 条事实，字段截断，完整事实仍保留在本地周报清单。
- **多表分类快照与可选归档**：按字段名解析 9 张同步表，将客户拜访、招投标、调研、重点项目、产品管理、支持待办、其他事项和使用反馈按业务分类汇总；每版周报保存生成时的不可变事实与负责人角色快照。正式发送成功后可按显式 fieldId/字段名映射回写 `周报存档`，默认关闭。
- **TB 重点项目补充**：兼容 `bi_center` 的 Teambition native/dingtalk 两种只读接口；按多维表重点项目名称检索，并用项目编号或规范化名称做唯一匹配，只读取匹配项目的进度、项目状态和状态正文。TB 任务不再作为独立周报事实。
- **重点项目 TB 状态页**：“周报管理”下提供独立页面，直接以多维表当前有效的“重点项目跟踪”记录为动态白名单；逐项展示多维表项目信息、TB 唯一匹配依据、项目进度和最新状态。多维表新增记录会先以待匹配状态出现，删除记录会立即退出页面范围。
- **固定周末编排与人工审核**：Asia/Shanghai 周六 09:00 生成最新综合版并仅投递“推送测试”群，周六 17:00 按正式样式单聊已配置的最终版接收人，周日 20:00 仅会投递已人工审核、内容哈希未变化的当前综合版。`autoFormalSendEnabled` 在 v1 始终为 `false`。
- **可追溯**：周报按周期和类型版本化；每次团队或个人保存都会创建新的综合版修订并使旧审核失效。审核记录绑定内容 SHA-256，消息在外部调用前原子占位并保存 `processQueryKey`，支持安全重试和群/个人撤回。
- **人员覆盖**：产品经理名单来自 AI 表，项目经理可由显式名单和 `bi_center` 职位关键词组成；管理页显示缺报人员并可人工发送一次性单聊提醒。
- **子路径部署**：`APP_BASE_PATH`、相对静态资源和前端 API 解析均支持共享域名下的 `/weekly-assistant/`。
- **钉钉身份登录**：管理页首次访问通过钉钉 OAuth 验证身份；能匹配到 `bi_center` 有效在职人员目录的账号即可进入，不依赖周报确认人配置。会话使用独立密钥签名的 `HttpOnly` Cookie，`ADMIN_API_TOKEN` 仅保留为运维兜底。
- **团队与个人周报编辑**：详情页可从“外部打开”左侧进入团队周报编辑器，维护标题、六个总结区块、各分类摘要及本版每个事项的分类、状态、负责人、进展、计划和风险；个人周报允许本人维护个人总结、分类摘要及事项展示内容，审核人可代为编辑。编辑只创建新的本地事实快照版本，不回写多维表；TB 官方项目状态保持只读。任一保存都会清除旧预览、图片和审核结果，要求重新走预览与审核流程。

详细流程见 [架构与数据口径](docs/architecture.md)，开放平台配置见 [钉钉配置清单](docs/dingtalk-open-platform.md)。

## 本地启动

```bash
python -m venv .venv
.venv\Scripts\pip.exe install -r requirements-dev.txt
.venv\Scripts\python.exe -m playwright install chromium
copy .env.example .env
.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 39057 --workers 1
```

访问 `http://127.0.0.1:39057/`。本地未启用钉钉 SSO 时可使用 `ADMIN_API_TOKEN`；正式环境应启用钉钉登录。不要把真实密钥提交到 Git。

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
| `DINGTALK_SSO_ENABLED` / `ADMIN_SESSION_SECRET` | 启用管理页钉钉 OAuth 与服务端签名长会话；签名密钥必须与其他 Secret 独立 |
| `ADMIN_SESSION_DAYS` | 管理会话有效期，默认 30 天；人员离职或从有效目录移除后会即时失去权限 |
| `PUBLIC_BASE_URL` / `PUBLIC_LINK_SECRET` | 钉钉可访问的 HTTPS 周报与图片签名链接 |

群的 `openConversationId`、应用机器人的 `robotCode`、个人接收人和审核人的 `userId` 在管理页配置，不进入环境变量。只给一个人推送时，在“从 bi_center 选择个人接收人”中选择该员工；页面会清空群目标，并同时设置个人预览、个人正式接收人和审核人。

管理页 OAuth 需在钉钉开放平台为企业内部应用新增 `Contact.User.Read`，将回调地址配置为 `${PUBLIC_BASE_URL}/api/auth/dingtalk/callback`，并发布包含该权限和回调配置的应用版本。OAuth 仅读取当前登录用户身份；AI 多维表、机器人和 TB 的服务端接口继续使用各自既有凭证。

个人目标收到的 ActionCard 主按钮会直接进入该期“我的个人周报”，正文同时保留团队周报详情入口。个人推送要求 `PUBLIC_BASE_URL`、`DINGTALK_SSO_ENABLED=true` 和 `ADMIN_SESSION_SECRET` 均已配置；任一缺失时发送会在投递前阻断，避免产生无法打开或无法识别身份的入口。

个人推送对应的核心配置形状为：

```json
{
  "defaultRobotCode": "ding...",
  "previewGroupTargets": [],
  "formalGroupTargets": [],
  "previewPersonalTargets": [{"name": "接收人", "userId": "user-id", "enabled": true}],
  "formalPersonalTargets": [{"name": "接收人", "userId": "user-id", "enabled": true}],
  "saturdayFinalPersonalTargets": [{"name": "周六最终版接收人", "userId": "user-id", "enabled": true}],
  "approverTargets": [{"name": "接收人", "userId": "user-id", "enabled": true}]
}
```

个人编辑覆盖写入独立的 `weekly_report_personal_edit` 表；保存时会复制到新综合版修订。群目标必须以 `openConversationId + robotCode` 成对配置，预览和正式群不得复用同一会话 ID；周六最终版接收人必须恰好一名。机器人群发接口未提供可核验的逐人 @ 合同，因此周六测试消息使用明确文字提醒，不声称已 @ 任何人。

AI 模型配置是可选项；`AI_PROVIDER` 标记供应商，`AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL` 三项齐全时调用 OpenAI-compatible Chat Completions，否则使用确定性模板并在周报中标记 `fallback/deterministic`。管理页提供与 `bi_center` 一致的 Provider、API Base、模型名、API Key 脱敏、连接测试、保存和恢复部署配置能力；页面保存的是本服务独立覆盖，不会反向修改 `bi_center`。

## TB 工作看板

管理后台的“TB 工作看板”只显示已与多维表重点项目唯一匹配的项目任务，按进行中、已逾期、7 天内到期和已完成四列展示，支持月份、部门/业务组、状态和关键词筛选。默认执行人范围与 `bi_center` 一致，为检测技术部、AI应用研发部、物联网事业部、媒体终端软件研发部、通讯终端软件研发部、产品工程部和硬件研发部。

TB 数据通过官方只读接口直接进入本服务 SQLite；不会读取 `bi_center` 的 TB 数据库，也不会导入或挂载其源码。当前 native 模式先按多维表重点项目名称调用 `/v3/project/query`，经项目编号或规范化名称唯一匹配后，再调用 `/v3/project/{projectId}/status/list` 读取该项目的最新状态；无关项目的项目详情和状态不会写入重点项目缓存。执行人任务仅用于匹配项目的工作看板，不再投影为 `TB任务` 周报事实。配置项“补充重点项目状态”可关闭此项周报补充。

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
