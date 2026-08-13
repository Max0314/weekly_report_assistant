# task-001：独立周报助手初始化

- 状态：已部署，待正式凭证与业务联调
- 工作目录：`D:\code_CPL\weekly_report_assistant`
- 交付分支：`main`（新仓库初始实现）
- GitHub：`git@github.com:Max0314/weekly_report_assistant.git`
- 应用发布提交：`ccc748641bf6c1f84d80be8c5d3475cce5b32377`（后续文档提交不改变应用镜像）
- 服务器目录：`/home/max/apps/weekly_report_assistant`
- 宿主机端口：`127.0.0.1:39022`（容器内 `39057`）
- 公网入口：`https://neoflow-cn.neo-net.com/weekly-assistant/`

## 验收条件

- 不修改、不依赖 `bi_center` 源码和数据库。
- 可读取 AI 多维表多表快照和 `bi_center` 人员目录接口。
- 可生成综合/产品/项目周报，支持确定性兜底。
- 具备预览、审核、正式推送、撤回、幂等日志和机器人指令。
- 支持个人/群两种预览与正式目标；只配置个人目标时不会向群发送。
- 首次同步建立历史基线，每版周报保存事实快照和人员覆盖清单。
- 支持 `/weekly-assistant/` 子路径、回调生产拒绝策略和缺报单聊提醒。
- 正式发送后支持可选的 AI 表周报存档幂等回写，失败可单独重试且不重复推送。
- 正式推送默认不能自动触发，缺少关键配置时阻断。
- 提供管理页面、Docker 部署、文档和离线测试。

## 待正式环境验收

- 轮换并配置钉钉应用 Client Secret；聊天中已暴露的旧 Secret 不得上线。
- 配置 AI 表 operatorId 与开放平台读取/记录新增权限。
- 为本服务签发 bi_center 独立只读 Token；当前未复用已有 2 个 Token。
- 预览/正式群和审核人配置。
- HTTPS 回调和图片访问。
- 周报存档表 fieldId 映射与记录新增权限。
- 配置 AI 模型后验证智能摘要；未配置时仅使用确定性兜底模板。

## 已完成部署验证

- GitHub、本地和服务器 HEAD 一致。
- Docker Compose 单服务、单副本运行且 healthcheck 为 healthy。
- `.env` 权限为 `600`，运行目录未进入 Git。
- Nginx 全局语法检查通过并已 reload。
- 公网主页、CSS、JavaScript、`/api/health` 均返回 200。
- 不带回调 Token 的机器人请求返回 401。
- 公网页面已完成实际浏览器渲染验证。
