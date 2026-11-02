# Task 008：模型生成链路修复与隔离验收

## 目标与边界

- 验证 `openai/gpt-6-luna` 的来源整理、板块生成、JSON 解析、后台队列、草稿保存和读取。
- 只修改独立周报助手，不修改 BI；不改正式周报、不重放历史任务、不发送钉钉消息。
- 用户本次明确禁止发消息，故不执行项目常规的部署后测试群推送，通知出口不计为已验收。

## 根因与改动

- 正式基线 `7f87401`：10 月 31 日生成的周报 36 两个板块均因旧模型 `application_model_not_allowed`（403）降级；此前周报成功。既有部署已将生效模型切至 Luna，数据库没有模型覆盖。
- 11 月 2 日最新 AI 表同步 261 条成功，来源/TB 调度正常；本次不修改这些后台任务。
- 原连接测试只检查 `choices` 是列表，空结果、无效 JSON、错误状态、拒绝和截断均可误记成功。新增用例在修复前复现 8 类误报，修复后校验完整 JSON 的 `status=ok`。
- 隔离实测 Luna 成功生成两个板块并保存读取；未复现参数不兼容，不修改温度、token 或 reasoning 参数。
- 增加可复用的隔离验收脚本，详见 `docs/deployment.md`。无结构变更、历史数据迁移或外部依赖变更。

## 验证与交付状态

- 本地 `.venv/Scripts/python.exe -m unittest discover -s tests -v`：100 项通过。
- `python -m compileall -q app tests` 及验收脚本语法检查通过；前端未修改，无需前端检查。
- GitHub 为准源；本项目未配置 GitLab remote，不额外创建同步目标。

## 正式部署与验收（2026-11-02）

- 应用提交 `4ed78cceb3e228718c91c260d4f8219d4cd9d699` 已推送 GitHub main，正式服务器从 GitHub 快进同步后构建部署。构建使用统一 `service-repair-build.lock`，保持单 worker、单副本。
- 运行镜像 `sha256:0f56bba60584257103316347fe99138cafc591ae08166d1335d7e13fd2e08a51`；容器内修改文件与 GitHub 提交内容的 SHA-256 一致。Compose healthy，内外网 `/api/health` 正常，启动及验收后日志无错误。
- 在正式容器环境执行隔离脚本，3 个真实 Luna 请求串行完成，耗时 8.08 秒；两条合成来源准确归入国内和海外板块，`aiStatus=success`、`workflowState=draft_generated`、队列 `success`。编辑期间生成正确排队，释放租约后后台处理完成，再次处理为空。
- 两个板块均保留“已完成技术评审、待补充交付日期、确认日期后提交方案”的原始事实，标案 0/1 各在对应板块；其他栏目按无事实输出“暂无”。人工核对无跨板块混写、无索要 README/目录/源码等无关内容。
- 六章非空字符串、不可变来源快照、新服务实例读取、内容哈希均通过；临时 SQLite 自动删除，隔离发送日志为 0。
- 正式 `POST /api/model-config/test` 验证接口与结果持久化，返回 Luna 成功（1783ms）；`/api/readiness` 的 `ready`、`aiSummary`、来源快照、TB 就绪均为 true。该操作只更新模型测试诊断记录。
- 部署前后正式 36 条周报、88 条发送日志的完整行内容 SHA-256 均完全相同；正式队列 8 条均为 `success`。旧周报 36 的已发送 fallback 历史保留，不重生成、不重发。
- SQLite/周报产物备份和前后摘要保存在 `/home/max/.codex-ops/model-switch-20261102/weekly-repair-4ed78cc/`，目录 700、文件 600；回滚镜像标签为 `weekly-report-assistant:rollback-7f87401`。
- 本次验收覆盖真实模型与本服务主要业务链路；上游采集采用既存真实成功同步证据，未重新拉取全量上游数据，未实发钉钉消息。不存在待修复的已复现生成问题，未声称完成消息出口验收。
