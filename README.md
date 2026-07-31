# astrbot_plugin_QmessageQ

QmessageQ 多功能 QQ 消息工具箱（适配 aiocqhttp / NapCat / OneBot v11）。

## 功能

- **图片藏文案（`/himg`）**：把文案塞进图片消息的 `summary` 字段后重发，对端客户端显示的是文案而非 `[图片]` 占位。
- **转发消息伪装（`/fake`）**：发送合并转发消息，伪造节点的昵称与 QQ 号；消息附带图片时会伪装成该人发的图。
- **真 @（`/at`）**：发送真实的 @ 提及或 @全体成员。
- **伪 @（`/fakeat`）**：发送指向不存在 QQ 号（`0`）的 @ 段，渲染类似 @ 但不会真的提醒任何人。
- **LLM @ 工具（`at_user`）**：让 LLM 在回复开头 @ 指定用户或全体成员，目标支持群昵称/群名片、QQ 号或 `all`。

## 配置

在 AstrBot WebUI 的插件配置中设置：

- `admin_only`：开启后所有命令（`himg`、`fake`、`at`、`fakeat`）仅对管理员生效，默认开启。
- `llm_tool_admin_only`：开启后 LLM `at_user` 工具仅对管理员生效，默认开启。

修改配置后请重载插件。

## 命令

所有命令默认仅管理员可用（`admin_only` 可关闭）；LLM `at_user` 工具的权限由 `llm_tool_admin_only` 单独控制。

### 图片藏文案

```
/himg <文案>
```
发送时在同一消息里附一张图片，bot 会重发该图片并把文案写入 `summary` 字段。

```
/himg <文案> <图片URL>
```
不带附件时，末尾参数若是 `http(s)://` 链接则作为图片来源。

### 转发消息伪装

```
/fake <昵称> <QQ号> <文本>
```
发送一条包含单个节点的合并转发消息，节点昵称与 QQ 号为指定值。命令附带图片时，图片会作为该人发出的图片放进节点内容。

### 真 @

```
/at <QQ号|all> <文本>
```
`qq=all` 时发送 @全体成员（仅群聊有效）。

### 伪 @

```
/fakeat <昵称> <文本>
```
发送 `at`（qq=`0`）+ 文本，看起来像 @ 但不会通知任何人。

## LLM 工具

工具名：`at_user`

参数：

- `target`（string）：要 @ 的目标，可以是群成员的昵称/群名片、QQ 号，或 `all`。
- `at_all`（boolean，默认 `false`）：为 `true` 时 @ 全体成员并忽略 `target`。

工具内部先按当前群成员昵称/名片匹配 QQ 号，匹配不到时按 QQ 号原样使用。记录的 @ 目标会插入到 LLM 回复的开头。

## 说明

- 命令名固定为 `himg` / `fake` / `at` / `fakeat`（AstrBot 的 `@filter.command` 在导入时注册，暂不支持按配置动态改名）。
- `himg` 依赖协议端透传图片段 `summary` 字段（NapCat / LLOneBot 支持），实际渲染效果请以你的客户端为准。
- `fakeat` 的伪 @ 使用 `qq=0`，为社区常用 best-effort 做法，渲染效果依 NapCat 版本与客户端而定。
- 仅支持 aiocqhttp（OneBot v11）平台。
