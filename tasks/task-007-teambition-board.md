# Task 007：Teambition 任务接入与工作看板

## 目标

- 使用与 `bi_center` 一致的 Teambition native/dingtalk 官方只读接口协议。
- 在周报助手独立 SQLite 中缓存任务、项目、钉钉/TB 身份映射和同步批次。
- 新增 TB 工作看板，展示进行中、逾期、7 天内到期和本月完成任务。
- 将 TB 叶子任务纳入综合版和项目经理版周报，不修改或读取 `bi_center` 的 TB 数据库。

## 当前状态

- 代码、数据库迁移、看板、API、调度入口、周报事实投影和离线测试已完成。
- 本地 54 个单元测试、Python 编译检查和 `node --check static/app.js` 通过。
- 桌面 1440px 与移动 390px 页面完成浏览器验收，无横向溢出和控制台错误。
- 已核实 `bi_center` 正式容器实际使用 `TEAMBITION_SOURCE=native`，并从其受保护运行环境安全复用 TB 原生 App ID、Secret 和 Organization ID；值未输出且仅写入 Git 忽略的本地 `.env` 与权限为 600 的正式服务器 `.env`，此前临时写入的 dingtalk 专用覆盖凭据已清空。
- TB 原生只读链路实测通过：30 个钉钉 UserID 全部成功映射，首个任务探针读取 169 条任务、21 个项目，任务结构校验通过；探测过程未输出人员标识或任务内容。
- 已安全复用正式 `bi_center` 人员目录只读 Token。通过临时 SSH 隧道和临时 SQLite 完成单成员端到端验证：读取 906 人正式目录，探针成员同步 34 条任务、5 个项目，生成 30 条有效叶子事实和 5 条当前看板事项；临时库已自动删除，未使用假数据绕过正式权限。
- 2026-08-26 已使用周报助手自身客户端验证 8 张正式 AI 多维表，合计读取 43 个字段、40 条记录；并在临时 SQLite 中完成“906 人目录 → 8 表/40 条 AI 表记录 → 单成员 163 条 TB 任务/39 个项目 → 综合/产品/项目三版草稿”真实链路演练，三版均为 `draft_generated`，未发送任何消息，临时库已删除。
- 调度器已增加双快照门禁：AI 表快照不再把 TB 投影误计为非空；只要 TB 被配置为纳入周报，最近 TB 批次就必须成功或部分成功、未过期且至少有一名成员成功，否则自动生成和自动预览均被阻断。关闭自动同步不会绕过该门禁。

## 数据库迁移

- 新增 `teambition_task`：任务事实、执行人、父子关系、日期、状态和原始响应。
- 新增 `teambition_project`：项目名称与归档状态。
- 新增 `teambition_user_map`：钉钉 UserID 与 TB UserID 映射、逐人同步状态。
- 新增 `teambition_sync_run`：批次状态、人数、任务数、失败数和错误摘要。
- TB 叶子任务投影到既有 `source_record`，使用 `base_id=teambition`、`table_id=teambition_tasks`。

已有 AI 表事实、周报、推送日志和人员目录不受影响。回滚旧镜像会忽略新增表；物理回滚必须停服备份后再删除 TB 表与投影数据。历史周报已经保存的事实快照不删除。

## 正式环境验收（2026-08-26）

1. NeoFlow 正式服务器已部署应用提交 `1eb0f1d`，单容器映射 `127.0.0.1:39022 -> 39057`，公网入口为 `https://neoflow-cn.neo-net.com/weekly-assistant/`；部署前 `.env`、SQLite 与周报产物备份在 `/home/max/backups/weekly_report_assistant/20260826-183046`。
2. SQLite 幂等迁移已创建四张 TB 表；正式同步读取 906 人目录、8 张 AI 多维表/40 条记录，TB 范围 384 人连续两批均为 384 成功、0 失败，最近自动批次读取 34,911 条任务。当前缓存 31,047 条有效任务、1,243 个有效项目和 29,766 条有效叶子事实。
3. 2026-08 看板验收共 2,888 条当月事项：进行中 796、逾期 145、7 天内到期 301、本月完成 1,646；正式 Nginx 路径、静态资源版本和健康接口均通过。
4. `week:20260824` 已生成并渲染综合版、产品经理版、项目经理版三份草稿，分别纳入 1,285、26、1,271 条事实；草稿未预览、未审批、未发送，部署后钉钉发送日志新增 0 条。
5. `SCHEDULER_ENABLED=true`、`TEAMBITION_SYNC_ENABLED=true` 已生效，目录、AI 表和 TB 三个调度作业均成功，`/api/readiness` 为 `true`。`autoPreviewEnabled=false`、`autoFormalSendEnabled=false`、`requireApproval=true`，正式发送继续强制人工审核。
