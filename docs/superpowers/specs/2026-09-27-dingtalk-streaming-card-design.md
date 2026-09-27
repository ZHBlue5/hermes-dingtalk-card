# 钉钉增强版 AI Streaming Card

日期：2026-09-27  
状态：待审阅  
宿主：本机 Hermes Agent `0.21.3`（`~/.hermes/hermes-agent`）的插件 API

## 目标

在钉钉企业机器人里，用一张 AI 卡片展示 Hermes 的一次回复：状态、模型、耗时、token、最近几次工具调用、思考流、回答流。结束后 Header 色条和 Footer 切到完成或失败。

成功标准：单聊和群聊都能看到思考区；工具区是滚动列表；官方只更新 `content` 的卡片不再发出。

## 已定决策

| 项 | 决定 |
|---|---|
| 交付 | 本仓库独立 Hermes 插件，可选启用 |
| 做法 | 子类官方 `DingTalkAdapter`，`register_platform("dingtalk")` 后写覆盖 |
| 范围 | v1 一次做完：多变量、工具列表、双区流式、完成态 |
| 与官方卡 | 互斥。插件启用且配置了模板 ID 时，只发增强卡 |
| 思考可见性 | 单聊和群聊都展示 |
| 工具区 | 最近 5 条，新的在上 |
| 模板 | 仓库带描述文件；能导入就导入，否则按同一份清单在控制台手建 |

## 安装与覆盖

插件目录名是 `dingtalk-streaming-card`。放到 `~/.hermes/plugins/dingtalk-streaming-card/`（从本仓库拷贝或软链），并执行 `hermes plugins enable dingtalk-streaming-card`。

Hermes 发现顺序是先登记内置平台（钉钉为延迟加载），再加载已启用的用户插件。用户插件调用 `register_platform("dingtalk")` 时，具体注册会清掉同名延迟加载器，成为该进程里唯一的钉钉适配器。

不依赖 `HERMES_ENABLE_PROJECT_PLUGINS`。本仓库不把插件只放在项目级 `.hermes/plugins` 里指望自动加载。

`plugin.yaml` 的 `kind` 为 `platform`。用户插件不是内置源，启用后会真正加载，而不是只登记延迟加载器。`register(ctx)` 里完成平台注册和 hook 注册。

## 卡片变量

建卡时写入全部 `cardParamMap`。`thinking` 与 `content` 用流式接口（markdown，`isFull=true`，每次带全文）。其余变量用普通卡片参数更新。

| 变量 | 更新 | 含义 |
|---|---|---|
| `status` | 普通 | `running` / `done` / `error` |
| `headerColor` | 普通 | `info` / `success` / `danger` |
| `title` | 普通 | `Hermes` |
| `model` | 普通 | 当前模型名，没有则为 `—` |
| `elapsed` | 普通 | 如 `12.4s` |
| `tool` | 普通 | 最近 5 条工具的 markdown |
| `toolCount` | 普通 | 本次已调用次数 |
| `tokens` | 普通 | 运行中为 `—`；结束时如 `in 1.2k · out 340` |
| `thinking` | 流式 | 思考全文 |
| `content` | 流式 | 回答全文 |
| `footer` | 普通 | 见状态机 |

工具行：`Shell · ls -la`、`MCP · server.tool`、`GitHub · …`、`Browser · …`。分类规则：

按下面顺序，先匹配先生效：

1. 工具名等于 `terminal` → Shell
2. 工具名以 `browser_` 开头 → Browser
3. 工具名包含 `github`（大小写不敏感）→ GitHub
4. 工具名以 `mcp` 开头，或 Hermes 标明来自 MCP → MCP
5. 其余用原名

参数只留一行，最多 80 个字符，超出截断。列表只保留最近 5 条。进行中的条目带「进行中」，`post_tool_call` 之后改为完成。

## 状态机

1. **创建**：投放卡片。`status=running`，`headerColor=info`，思考和回答为空，`footer=生成中`。
2. **流式**：思考和回答每 400ms 整段覆盖一次。单次内容不超过 1024 字节，`thinking` 与 `content` 各自累计不超过 10240 字节，超出截断并在末尾加 `…`。
3. **工具**：更新 `tool` 和 `toolCount`，不另开卡片。
4. **完成**：先写 `status=done`、`headerColor=success`、`tokens`、`footer=已完成 · {elapsed} · {tokens}`，再对 `thinking` 和 `content` 发 `isFinalize=true`。内容和上一帧相同也要发。
5. **失败或中断**：`status=error`，`headerColor=danger`，`isError=true`。失败时 footer 为一行短错误；用户中断时 footer 为 `已停止`。

`elapsed` 从建卡时刻计算，与回答帧共用 400ms 节流。

## 数据从哪来

进程内按 `chat_id` 登记当前卡。Hook 只有 `session_id`。`pre_gateway_dispatch` 记下 gateway 的 `session_store`，再用 `lookup_by_session_id` 取 `origin.chat_id`。对不上当前卡的旁路更新丢掉，不写到别的会话。

| 区域 | 来源 |
|---|---|
| `content` | 子类的 `send` / `edit_message`。若正文在 `\n\n---\n` 之后拼了工具进度，写入前剥掉 |
| `thinking` | `on_stream_delta` 且 `kind=reasoning` |
| 工具列表 | `pre_tool_call`（名称、参数摘要、进行中）和 `post_tool_call`（完成） |
| `model` | 同一次 turn 的 hook 载荷 |
| `tokens` | `post_api_request` 的 usage，按本轮累加，结束时写入 |
| `elapsed` | 建卡时刻 |

必须打开 Hermes 配置 `plugins.stream_reasoning_deltas: true`，否则没有思考流。钉钉平台的 `show_reasoning` 保持关闭，避免思考被再贴进回答正文。

不使用 `set_status_text`。那条短语是给 Slack 状态栏的，约 49 字，没有稳定的工具名。

子类继续负责官方适配器已经实现的进线、媒体、权限和表情。`send` / `edit_message` 以及建卡、流式更新、普通参数更新由子类实现。

## 模板

插件运行时不创建模板。本仓库提供：

- `templates/hermes-streaming-card.json`：布局和变量描述，字段与上表一致。控制台能导入就导入。
- `templates/README.md`：导入失败时的手工搭建步骤。

版面自上而下：色条、标题/模型/耗时、工具区、思考区、回答区、Footer。`thinking` 和 `content` 在模板里标成流式 markdown。色条绑定 `headerColor`：

| 值 | 色 | 何时 |
|---|---|---|
| `info` | `#1677FF` | 生成中 |
| `success` | `#00B578` | 完成 |
| `danger` | `#F53F3F` | 失败或中断 |

最后一帧的 `isFinalize` / `isError` 同时驱动钉钉自带的「输入中 / 完成 / 出错」。

模板 ID 配在钉钉平台现有的 `card_template_id` 上，不写进仓库。插件启用且该 ID 有值时只发增强卡。未配置模板 ID 时，与官方适配器一样发送 webhook markdown。

## 失败

- 流式更新失败：同一变量重试 1 次。仍失败则把卡打成 `error`。错误帧也失败则停止，日志留下 `outTrackId`。
- 建卡失败：只发一条 webhook markdown（回答），不留半张卡。
- 超长：按状态机截断。
- 用户中断：失败态，footer 为 `已停止`。
- 进程中途退出：卡停在生成中。v1 不做后台清扫。
- 会话绑定失败：丢掉这次思考、工具或 token 更新。回答仍走 `edit_message`。

## 测试

不连接钉钉。一个小测试文件覆盖：

- 工具分类，以及列表只留最近 5 条
- 剥掉正文 `\n\n---\n` 之后的工具进度
- 超长截断
- `running → done` 与 `running → error`（含中断 footer）
- `session_id` 对不上 `chat_id` 时不修改其他卡

## 不做

- 不改 Hermes 核心源码
- 不在运行时调用钉钉建模板接口
- 不与官方单 `content` 卡同时各发一张
- 不做进程崩溃后的卡片清扫
- 不把 Slack 状态短语当作工具列表来源
