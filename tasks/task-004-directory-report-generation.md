# Task 004：组织目录缓存与首版周报生成

## 目标

- 将 bi_center 的员工、组织归属和负责人角色只读同步到本项目 SQLite。
- 支持按姓名、部门、业务组、职务和负责人角色检索人员，并提供组织检索接口。
- 使用 AI 表格全量数据生成首版产品/项目经理周报，仅向陈鹏列发送预览。
- 服务器模型切换到 `qwen3.7`，密钥仅保存在服务器 `.env`。

## 交付信息

- 工作目录：`D:\code_CPL\weekly_report_assistant`
- 交付分支：`feature/task-004-directory-report-generation`
- 基线：`origin/main` @ `149c4d08da7afe8f1e073d21c6a738d07076c541`
- 当前状态：已提交未推送

## 数据库迁移

- 对 `employee_cache` 采用幂等 `ALTER TABLE` 增加组织、任职及负责人字段。
- 新增 `organization_cache` 与 `employee_org_relation_cache`，同步时事务性重建缓存数据。
- 回滚代码不会破坏已有数据；如需彻底回滚，可停服备份 SQLite 后删除两张新增缓存表，新增员工列可保留为空。
- 不修改 bi_center，不向其写入数据；不保存上游完整原始响应。

## 验收条件

- 旧数据库启动时自动完成增量迁移。
- 目录同步能写入员工、组织、员工组织关系和负责人角色。
- 人员搜索可返回组织信息与负责人角色；组织搜索可返回类型、人数和负责人数。
- 单元测试、Python 编译检查通过。
- 生产模型连接成功，AI 表格和目录同步成功。
- 生成的新周报 `aiStatus=success`，发送目标只有陈鹏列，且仅执行预览发送，不执行正式推送。
- 部署后 `/api/health`、Compose 状态与容器日志正常。
