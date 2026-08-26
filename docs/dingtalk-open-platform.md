# 钉钉开放平台配置清单

## 是否新建应用

v1 可以复用现有企业内部应用，前提是该应用允许增加 AI 多维表只读权限、机器人主动发消息与撤回能力，并可配置机器人消息回调。代码已与 `bi_center` 彻底分离，复用应用只代表共用钉钉身份和权限。

正式长期运行更建议新建独立应用，原因是权限最小化、回调地址互不影响、灰度发布和停用可独立进行、审计日志更容易区分。可先复用现有应用完成联调，再在上线前切换新应用；切换只需更换环境变量和群内机器人配置，不改业务代码。

## 应用侧能力

1. 企业内部应用处于可用/已发布状态。
2. 开通目标 AI 多维表所需的只读权限，并让 `DINGTALK_AITABLE_OPERATOR_ID` 对 Base 有访问权限；启用周报存档回写时，额外开通记录新增权限并确保操作人可编辑存档表。
3. 添加应用机器人，确认群机器人主动发送、单聊批量发送、群消息撤回和单聊消息撤回能力。
4. 子路径部署时将机器人消息回调指向 `https://<域名>/weekly-assistant/api/dingtalk/robot/callback?token=<DINGTALK_CALLBACK_TOKEN>`。Token 使用独立随机值，Nginx 对该路径关闭访问日志，避免查询参数落盘。
5. 群推送时把机器人加入预览群和正式群，分别记录 `openConversationId + robotCode`；仅个人推送时群目标可以保持为空，但仍需记录应用机器人的 `robotCode`。
6. 记录审核人的钉钉 `userId`，不要使用昵称做授权。

在开放平台权限管理中按实际 API 搜索并申请对应能力，避免不同控制台版本的权限显示名差异：

- `/v1.0/robot/groupMessages/send`：群机器人消息发送。
- `/v1.0/robot/oToMessages/batchSend`：机器人单聊批量发送。
- `/v1.0/robot/groupMessages/recall`：群机器人消息撤回。
- `/v1.0/robot/oToMessages/recall`：机器人单聊消息撤回。
- `/v1.0/notable/bases/{baseId}/sheets/{sheetId}/fields`：AI 表字段只读。
- `/v1.0/notable/bases/{baseId}/sheets/{sheetId}/records/list`：AI 表记录只读。
- `/v1.0/notable/bases/{baseId}/sheets/{sheetId}/records`：新增周报存档记录；仅启用归档回写时申请。

2026-08-26 已使用正式应用和正式操作人逐表验证 AI 多维表只读链路：8 张来源表的字段、记录接口均成功，`Notable.Base.Read.All` 权限问题已解除。应用换版、操作人或 Base 协作者权限调整后，仍需重新执行“同步 AI 表”验证；验证失败时不得用假数据绕过。

人员和组织数据由 `bi_center` 的只读 Token 提供；按当前部署决策复用一个已有 Token，不会获得写权限，本应用也不需要再申请钉钉通讯录读取权限。应用可见范围至少包含个人接收人和审核人；AI 表操作人还必须在目标 Base 的协作者/高级权限中具有对应表的读取权限。

## 尚需确认的参数

- AI 多维表 `operatorId`（当前指定陈鹏列，必须能读目标 Base）。
- `bi_center` 人员目录只读 Token，以及容器网络可访问的地址。
- 预览/正式的个人或群目标；个人目标需要 `userId + robotCode`，群目标需要 `openConversationId + robotCode`。
- 审核人 `userId` 列表。
- 生产 HTTPS 域名；钉钉服务器必须能访问签名后的周报图片。
- 实际项目经理字段/名单来源；当前表中明确可用的是“支持及待办”的“责任人”和项目类表视图。
- `周报存档` 表的精确 fieldId/字段名映射；必须包含可唯一查重的 `archiveKey`、标题和周期字段。

## 上线验证顺序

先同步人员目录，再同步 AI 表；生成一版不发送的草稿并核对事实快照和缺报清单；只配置测试接收人执行个人预览；从回调事件确认 senderId、conversationId、robotCode；由审核人发“确认发送”；最后检查个人正式消息、发送日志和撤回。启用群推送后再单独验证预览群与正式群，测试期间不要让两者复用同一个 `openConversationId`。
