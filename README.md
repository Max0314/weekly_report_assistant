# 产品与项目经理周报助手

独立于 `bi_center` 的周报服务。它读取钉钉 AI 多维表中的过程记录，通过 `bi_center` 内部接口匹配人员信息，生成产品经理/项目经理/综合周报，并支持“个人或群预览 → 审核人确认 → 个人或群正式推送 → 可撤回”的钉钉机器人链路。

## 设计结论

- **不影响 bi_center**：不导入、不修改其源码和数据库，只调用版本化的只读人员目录接口。
- **事实与文案分离**：事项数量、负责人、风险、逾期等由程序确定性计算；AI 只归纳 6 个文案区块，失败时自动回退。
- **最小化 AI 数据**：默认不向模型发送人员姓名；按风险优先最多发送 120 条事实，字段截断，完整事实仍保留在本地周报清单。
- **多表快照与可选归档**：按字段名解析 8 张来源表，首次同步建立历史基线；每版周报保存生成时的不可变事实快照。正式发送成功后可按显式 fieldId/字段名映射回写 `周报存档`，默认关闭。
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
