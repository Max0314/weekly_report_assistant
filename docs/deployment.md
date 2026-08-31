# 部署与运维

## 首次部署

1. 从 `.env.example` 创建服务器本地 `.env`，通过密钥管理、服务器到服务器传输或人工安全输入补齐；不得把 Secret 写入 Git、部署日志或命令输出。本次生产部署按负责人决定复用现有钉钉应用 Secret。
2. 子路径部署设置：

   ```text
   APP_PUBLISH_PORT=39022
   APP_BASE_PATH=/weekly-assistant
   PUBLIC_BASE_URL=https://neoflow-cn.neo-net.com/weekly-assistant
   ```

3. 生成不同的长随机值用于 `ADMIN_API_TOKEN`、`ADMIN_SESSION_SECRET`、`PUBLIC_LINK_SECRET` 和 `DINGTALK_CALLBACK_TOKEN`，设置 `DINGTALK_SSO_ENABLED=true`。四个值不得复用。
4. 执行 `docker compose up -d --build`，等待 Chromium 依赖安装和健康检查通过。
5. 从 `bi_center` 正式容器的受保护运行环境安全复制 `TEAMBITION_SOURCE=native`、`TEAMBITION_OPEN_API_BASE`、`TEAMBITION_OPEN_APP_ID`、`TEAMBITION_OPEN_APP_SECRET` 和 `TEAMBITION_OPEN_ORGANIZATION_ID`，不在终端输出值；保持 `TEAMBITION_SYNC_ENABLED=false`，先完成一次手动同步和看板核验。
6. 钉钉开放平台新增 `Contact.User.Read`，配置并发布登录回调 `${PUBLIC_BASE_URL}/api/auth/dingtalk/callback`。首次访问管理页应自动进入钉钉授权；能匹配到 `bi_center` 有效在职人员目录的账号即可进入，登录不依赖周报确认人名单。
7. 打开管理页，按“人员 → AI 表 → TB → 覆盖检查 → 生成 → 正文核对 → 图片 → 个人预览 → 审核 → 个人正式发送”的顺序联调；确认消息链路后再配置字段映射并启用存档回写。

## Nginx 子路径

容器内端口保持 `39057`；NeoFlow 服务器按账号端口段发布到宿主机 `127.0.0.1:39022`，外部统一通过现有 HTTPS 域名访问：

```nginx
location = /weekly-assistant {
    return 301 /weekly-assistant/;
}

# 查询参数中包含独立回调 Token，因此关闭该路径的访问日志。
location = /weekly-assistant/api/dingtalk/robot/callback {
    access_log off;

    proxy_pass http://127.0.0.1:39022;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /weekly-assistant;
}

# OAuth 回调查询参数中包含一次性 authCode 和签名 state，同样关闭访问日志。
location = /weekly-assistant/api/auth/dingtalk/callback {
    access_log off;

    proxy_pass http://127.0.0.1:39022;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /weekly-assistant;
}

location ^~ /weekly-assistant/static/ {
    proxy_pass http://127.0.0.1:39022/weekly-assistant/static/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /weekly-assistant;
    expires 5m;
}

location /weekly-assistant/ {
    proxy_pass http://127.0.0.1:39022;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Prefix /weekly-assistant;
    proxy_read_timeout 120s;
    client_max_body_size 2m;
}
```

配置后验证：

```bash
curl -fsS https://neoflow-cn.neo-net.com/weekly-assistant/api/health
curl -I https://neoflow-cn.neo-net.com/weekly-assistant/static/app.js
```

开放平台回调地址为：

```text
https://neoflow-cn.neo-net.com/weekly-assistant/api/dingtalk/robot/callback?token=<DINGTALK_CALLBACK_TOKEN>
```

机器人消息回调与管理页 OAuth 回调是两条独立链路，登录回调为：

```text
https://neoflow-cn.neo-net.com/weekly-assistant/api/auth/dingtalk/callback
```

生产环境未设置 `DINGTALK_CALLBACK_TOKEN` 时，回调固定返回 `503`，不会把未经保护的事件加入队列。
容器的 Uvicorn 关闭通用访问日志，避免机器人 Token、OAuth `authCode/state` 进入 Docker 日志；应用错误和生命周期日志仍保留。

## 健康与诊断

- `/api/health` 仅检查服务与 SQLite。
- `/api/readiness` 需要有效钉钉管理会话或运维令牌，显示钉钉、AI 表、最近源表快照、bi_center、TB 重点项目状态、最近模型连接测试、回调鉴权、公开链接、个人/群目标、归档配置和人员缓存状态，不返回密钥。应用配置齐全但 AI 表或已启用的 TB 重点项目状态最近同步失败、无可用匹配或过期时，总体状态仍为未就绪。
- `/api/model-config` 需要管理令牌，返回当前模型、来源与脱敏 Key；连接测试可使用未保存候选配置，留空 Key 时安全复用当前生效 Key。
- `/api/teambition/status`、`/api/teambition/dashboard` 和 `POST /api/sync/teambition` 均需要管理令牌；状态接口只返回来源、配置布尔值、数量和最近批次，不返回 App ID、Secret、组织 ID 或访问令牌。
- `/api/coverage` 显示预期产品/项目经理与本周有效事项覆盖；`POST /api/coverage/remind` 仅在管理员确认后发送一次性缺报单聊。
- `sync_run` 保存逐表同步结果；`teambition_sync_run` 保存 TB 批次及重点项目匹配摘要。AI 表最新同步失败/过期/空快照，或已启用的 TB 最新批次失败/过期时，自动生成和自动预览会被阻断；关闭自动同步不会绕过该门禁，只能手动刷新快照或明确关闭“补充重点项目状态”。
- `job_status` 保存调度失败及重试次数；机器人事件和推送日志只在管理接口可见。
- 外部接口失败时不删除上一版快照；正式推送失败进入 `retryable_error`。

## 数据库迁移

启动时自动执行仅新增字段的幂等迁移：

- `weekly_report.source_snapshot_json TEXT NOT NULL DEFAULT '[]'`
- `weekly_report.coverage_json TEXT NOT NULL DEFAULT '{}'`
- `weekly_report.archive_status/archive_record_id/archive_error TEXT NOT NULL DEFAULT ''`
- `weekly_report.archive_attempted_at/archived_at TEXT NOT NULL DEFAULT ''`
- `weekly_report.archive_payload_json TEXT NOT NULL DEFAULT '{}'`
- `source_record.category_key TEXT NOT NULL DEFAULT ''`
- `source_record.category_order INTEGER NOT NULL DEFAULT 999`
- `source_record.subcategory TEXT NOT NULL DEFAULT ''`
- `source_record.assignees_json TEXT NOT NULL DEFAULT '[]'`
- `employee_cache` 增加工号、主部门 ID、任职来源、负责人标志与负责人范围字段
- 新建 `organization_cache` 和 `employee_org_relation_cache` 两张可重建缓存表
- 新建 `teambition_task`、`teambition_project`、`teambition_user_map` 和 `teambition_sync_run` 四张 TB 缓存/审计表；`teambition_project` 增加重点项目匹配、进度和项目状态字段。历史 `source_record.table_id=teambition_tasks` 投影会在下一次 TB 同步时停用，不再进入新周报

影响：已有周报内容、状态、事实快照和发送日志不变；`teambition_project` 新字段以空值、`0` 或 `-1` 初始化，部署后的下一次正式 AI 表/TB 同步仅为当前多维表重点项目补齐真实状态，不会回写或伪造历史周报。个人周报继续由生成时快照实时派生。回滚旧镜像时新增列会被忽略，既有项目、任务、人员和发送数据不丢失。

## 回滚

1. 部署前备份 `runtime/weekly_report_assistant.db` 和 `runtime/reports/`。
2. 停止当前容器，以旧镜像重建；不要删除或覆盖 `runtime/`。
3. 旧版本会忽略新增列和缓存表，因此应用回滚无需删除结构。
4. 如需彻底回滚目录缓存结构，停服并备份 SQLite 后，可在数据库副本中删除 `employee_org_relation_cache`、`organization_cache`；员工新增列可保留为空。
5. 如需彻底回滚 TB 接入，先设置 `TEAMBITION_SYNC_ENABLED=false`，再在数据库副本中删除四张 `teambition_*` 表和 `source_record.table_id=teambition_tasks` 投影；历史周报的事实快照保持不变。
6. 回滚本次分类/个人视图无需删除数据：旧镜像会忽略 `source_record` 新增列；如需物理删除新增列，应在数据库副本上重建表并验证后替换，不在生产库上直接执行破坏性变更。

撤回钉钉消息依赖发送时取得的 `processQueryKey`，超出钉钉允许窗口时可能无法撤回。部分撤回失败时周报进入 `retryable_error`，不会错误标记为全部撤回。
