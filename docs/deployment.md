# 部署与运维

## 首次部署

1. 从 `.env.example` 创建服务器本地 `.env`，通过密钥管理或人工安全输入补齐；不得复制聊天中已暴露的旧 Secret。
2. 子路径部署设置：

   ```text
   APP_PUBLISH_PORT=39022
   APP_BASE_PATH=/weekly-assistant
   PUBLIC_BASE_URL=https://neoflow-cn.neo-net.com/weekly-assistant
   ```

3. 生成不同的长随机值用于 `ADMIN_API_TOKEN`、`PUBLIC_LINK_SECRET` 和 `DINGTALK_CALLBACK_TOKEN`。
4. 执行 `docker compose up -d --build`，等待 Chromium 依赖安装和健康检查通过。
5. 打开管理页，按“人员 → AI 表 → 覆盖检查 → 生成 → 正文核对 → 图片 → 个人预览 → 审核 → 个人正式发送”的顺序联调；确认消息链路后再配置字段映射并启用存档回写。

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

生产环境未设置 `DINGTALK_CALLBACK_TOKEN` 时，回调固定返回 `503`，不会把未经保护的事件加入队列。

## 健康与诊断

- `/api/health` 仅检查服务与 SQLite。
- `/api/readiness` 需要管理令牌，显示钉钉、AI 表、bi_center、回调鉴权、公开链接、个人/群目标、归档配置和人员缓存状态，不返回密钥。
- `/api/coverage` 显示预期产品/项目经理与本周有效事项覆盖；`POST /api/coverage/remind` 仅在管理员确认后发送一次性缺报单聊。
- `sync_run` 保存逐表同步结果；最新同步失败或超过 `sourceFreshnessHours` 时，自动生成和自动预览会被阻断。
- `job_status` 保存调度失败及重试次数；机器人事件和推送日志只在管理接口可见。
- 外部接口失败时不删除上一版快照；正式推送失败进入 `retryable_error`。

## 数据库迁移

启动时自动执行仅新增字段的幂等迁移：

- `weekly_report.source_snapshot_json TEXT NOT NULL DEFAULT '[]'`
- `weekly_report.coverage_json TEXT NOT NULL DEFAULT '{}'`
- `weekly_report.archive_status/archive_record_id/archive_error TEXT NOT NULL DEFAULT ''`
- `weekly_report.archive_attempted_at/archived_at TEXT NOT NULL DEFAULT ''`
- `weekly_report.archive_payload_json TEXT NOT NULL DEFAULT '{}'`

影响：已有周报内容、状态和发送日志不变；新增归档字段初值为空，不会自动回写历史周报。旧周报没有历史事实快照时继续按原兼容逻辑读取源记录，新生成周报保存生成时事实与覆盖清单。

## 回滚

1. 部署前备份 `runtime/weekly_report_assistant.db` 和 `runtime/reports/`。
2. 停止当前容器，以旧镜像重建；不要删除或覆盖 `runtime/`。
3. 旧版本会忽略新增列，因此应用回滚无需删除列。
4. 如需物理删除新增列，应在数据库副本上重建表并验证后替换；不在生产库上直接执行破坏性变更。

撤回钉钉消息依赖发送时取得的 `processQueryKey`，超出钉钉允许窗口时可能无法撤回。部分撤回失败时周报进入 `retryable_error`，不会错误标记为全部撤回。
