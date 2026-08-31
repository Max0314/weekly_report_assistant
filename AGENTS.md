# AGENTS.md

本项目是从 `bi_center` 周报链路抽离出的独立服务，不得通过修改、导入或挂载 `bi_center` 源码运行。

## 边界

- 默认端口 `39057`，FastAPI + SQLite + 原生前端，单容器部署。
- `bi_center` 仅作为只读人员主数据 API；AI 多维表和钉钉机器人均通过官方开放接口访问。
- 不提交 `.env`、Client Secret、API Token、人员明细、运行数据库、消息事件或周报图片。
- 正式推送必须经过审核；`autoFormalSendEnabled` 在 v1 强制为 `false`。
- 不使用假数据、mock 或硬编码绕过正式环境权限问题。

## 修改与验证

- 优先最小改动，中文文件保持 UTF-8。
- 当前阶段每次修改在测试、部署和健康检查全部通过后，必须将最新综合版周报按正式版样式推送到且仅推送到“推送测试”群；使用本次部署提交号作为测试推送幂等键，并核对发送结果。该测试推送不得改变周报审核/正式发送状态，也不得替代正式业务群的人工审核。
- 修改 Python 后运行 `python -m unittest discover -s tests -v` 和 `python -m compileall -q app tests`。
- 修改 `static/app.js` 后运行 `node --check static/app.js`，并更新 `index.html` 中资源版本号。
- 数据库结构变化必须同步说明迁移、回滚和已有数据影响。
- 本地缺钉钉、AI 表、bi_center 正式凭证时，只能声明离线测试通过，不得表述为正式业务验收。

## 部署

- 调度器依赖进程内锁和 SQLite 幂等键，必须保持单 worker、单副本。
- 推送前检查公开 HTTPS 地址、图片链接签名、人员目录缓存、审核状态、预览/正式群配置。
- 部署后检查 `/api/health`、`docker compose ps` 和容器最近日志。
