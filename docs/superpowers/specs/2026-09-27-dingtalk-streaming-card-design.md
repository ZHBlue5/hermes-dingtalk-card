# 钉钉增强版 AI Streaming Card

日期：2026-09-27  
状态：待审阅  
宿主：本机 Hermes Agent `0.21.3`（`~/.hermes/hermes-agent`）

一份回复一张卡。卡上有状态、模型、耗时、token、最近 5 次工具、思考流、回答流。结束时 Header 与 Footer 切到完成或失败。

## 决策

| 项 | 决定 |
|---|---|
| 交付 | 本仓库插件 `dingtalk-streaming-card`，可选启用 |
| 做法 | 子类官方 `DingTalkAdapter`，`register_platform("dingtalk")` 整体替换 |
| 与官方卡 | 插件启用且配置了 `card_template_id` 时只发增强卡 |
| 建卡 | 惰性。本轮首个真实输出时建卡，不在进线时建卡 |
| 卡片身份 | 一张卡对应一个 `turn_id`。`chat_id` 只决定投到哪个会话 |
| 思考 | 单聊和群聊默认都展示。`group_thinking: false` 时群聊不写 `thinking`，单聊不受影响 |
| 工具区 | 最近 5 条，新的在上。参数单行最多 80 字符，写入前脱敏 |
| 终态 | 以 `on_session_end` 为主，另有两层兜底。不用 `on_stream_end` 判断回合结束 |
| 模板 | 仓库提供描述文件。能导入就导入，否则按同一清单在控制台手建 |
| 长度 | spike 回填前按「模板」里的保守预算实现。超长部分用 webhook 补发 |

## 卡片

建卡时写入全部 `cardParamMap`。`thinking` 与 `content` 走流式接口（markdown，`isFull=true`，每次带全文）。其余变量走 `UpdateCard`（`PUT /v1.0/card/instances`）。官方适配器没有这条调用，由子类补上。

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

工具分类按顺序，先匹配先生效：

1. 工具名以 `mcp__` 开头 → `MCP · {server}.{tool}`（从 `mcp__{server}__{tool}` 拆出）
2. 工具名等于 `terminal` → `Shell · …`
3. 工具名以 `browser_` 开头 → `Browser · …`
4. 工具名包含 `github`（大小写不敏感）→ `GitHub · …`
5. 其余用原名

`mcp__github__create_issue` 归 MCP，显示 `github.create_issue`。参数先脱敏再截成一行 80 字符。进行中的条目带「进行中」，`post_tool_call` 之后改为完成。

## 状态机

`elapsed` 从本轮首个带 `turn_id` 的 hook 到达时算起，不用建卡时刻。它和回答帧共用 400ms 节流。完成帧和失败帧立即发出。

`tokens` 累加本轮 `post_api_request` 的 `usage`。失败的调用不计入。轮边界以 `on_session_end` 为准；没走到这一 hook 时，用已经累加到的数字。

1. **创建。** 本轮首个 `on_stream_delta`（任意 kind）或首个 `pre_tool_call` 触发，先到者为准。写入 `status=running`、`headerColor=info`、`footer=生成中`，并带上此刻已有的思考、回答和工具。不在 `pre_gateway_dispatch` 建卡。
2. **流式。** 思考和回答每 400ms 整段覆盖一次。长度见「模板」。
3. **工具。** 只从 hook 更新 `tool` 和 `toolCount`，不另开卡片，也不从正文里解析工具行。
4. **段边界。** 网关在工具调用前会 `edit_message(finalize=True)`，并在下一段用 `send()` 开新消息。插件把这次 finalize 只当作缓冲区落盘：不关卡，不写完成态。子类的 `send()` 在本轮已有卡时复用同一个 `out_track_id`，并且不调用基类的 `_close_streaming_siblings`。
5. **完成。** 只由下面的终态信号触发。先 `UpdateCard` 写入 `status=done`、`headerColor=success`、`tokens`、`footer=已完成 · {elapsed} · {tokens}`，再对已经流过至少一帧的 `thinking` 和 `content` 发 `isFinalize=true`。内容和上一帧相同也要发。
6. **失败或中断。** `status=error`，`headerColor=danger`。失败时 footer 为一行短错误。用户中断时 footer 为 `已停止`。`isError=true` 打在已经流过至少一帧的流上；两个流都还没开始时不发 `isError`，只改 `status`、`headerColor`、`footer`。

终态信号三层都听，先到者收口，重复到达不改已经收口的卡：

| 顺序 | 信号 | 覆盖 |
|---|---|---|
| 1 | `on_session_end` | 正常完成，以及走到 `finalize_turn` 的失败和中断。带 `turn_id` |
| 2 | `agent_loop_stopped` | 网关或 TUI 主动停止。没有 `turn_id`，按 `session_key` 找到该 session 上未收口的卡 |
| 3 | `api_request_error`，或 `post_tool_call` 且 `status=error` | 预检超时、retry 耗尽、内容策略拒答等不会走到 `finalize_turn` 的路径 |

`on_stream_end` 只表示一次模型流式请求结束，一轮里会响多次，不能用来收口。

### 同一会话里的多张卡

登记按 `turn_id`。另外保留 `chat_id → {out_track_id: 卡}`，只用于投递和收尾。

- 同一 `turn_id` 的段边界复用已有卡，不新建。
- 同一 session 进入下一轮或被重置时，把该 session 上一张未收口的卡打成 `error`，footer 为 `已结束`。
- 不同 session 共用同一个 `chat_id`（群里另一个人）时，不碰对方的卡。

## 接入

插件目录 `~/.hermes/plugins/dingtalk-streaming-card/`，执行 `hermes plugins enable dingtalk-streaming-card`。`plugin.yaml` 的 `kind` 为 `platform`，并在 `provides_hooks` 里列出实际注册的 hook。不依赖 `HERMES_ENABLE_PROJECT_PLUGINS`。

宿主配置：

```yaml
display:
  platforms:
    dingtalk:
      streaming: true
      show_reasoning: false
      tool_progress: off
plugins:
  stream_reasoning_deltas: true
```

`streaming: true` 否则网关不建 stream consumer，回答不会经 `edit_message` 更新。`stream_reasoning_deltas: true` 否则没有 `kind=reasoning`。`show_reasoning` 保持 false，避免思考被再贴进最终回答。`tool_progress` 保持 off：钉钉没有 native streaming，进度会另建一张卡，并在每次 `send()` 时关掉该会话里还开着的卡。

插件自己的配置写在 `~/.hermes/config.yaml`：

```yaml
plugins:
  entries:
    dingtalk-streaming-card:
      settings:
        group_thinking: true
```

`connect()` 时若 `alibabacloud-dingtalk` 不可导入，或 `card_template_id` 为空，记 ERROR 并给出安装或配置提示，此后只走 webhook markdown。宿主的 `ensure_dingtalk_deps` 在 `dingtalk-stream` 和 `httpx` 已存在时不会补装卡片 SDK，所以探测要放在插件里。

`register_platform` 是整条 `PlatformEntry` 替换，没有字段合并。做法是让官方 `register()` 先对一个只记录参数的 ctx 调用，再把 `adapter_factory` 换成子类，用完整参数调用真正的 `register_platform`。返回 `None` 时记 ERROR，表示注册没有生效。

模板 ID 仍用钉钉平台已有的 `card_template_id`，不写进仓库。换增强模板后必须换成新 ID：旧的单 `content` 模板缺变量时会渲染成空白。启动时打印一行，说明增强卡是否已按模板启用。

## 数据与线程

`pre_gateway_dispatch` 记下 `session_store`。投递目标用 `event.source.chat_id`，与出站 `send` / `edit_message` 的 `chat_id` 同源。`lookup_by_session_id` 只作辅助：它是线性扫描，session 重置后可能命中多条，不能当作回合身份。对不上当前 `turn_id` 的旁路更新丢掉。

| 区域 | 来源 |
|---|---|
| `content` | 本轮 `turn_id` 对应卡上的 `send` / `edit_message`。正文原样写入 |
| `thinking` | `on_stream_delta` 且 `kind=reasoning`，写入前脱敏 |
| 工具列表 | `pre_tool_call` 与 `post_tool_call` |
| `model` | 同一次 turn 的 hook 载荷 |
| `tokens` | `post_api_request` 的 usage |
| `elapsed` | 本轮首个带 `turn_id` 的 hook |
| 收口 | 终态信号三层，见状态机 |

`on_stream_delta` 跑在每回调一个的 daemon 线程里，回调只改内存并入队，钉钉请求用 `asyncio.run_coroutine_threadsafe` 回到网关 loop。队列上限 1024，满了丢最旧。思考区每次全量覆盖，允许缺字，不为缺字重试。

`pre_tool_call` 只做内存更新。超时或仍在运行会挡住工具调用。回调抛异常不会挡工具，也不要用抛异常表达控制流。

同一 `outTrackId` 的写出串行：单飞队列，同一变量的待发帧合并成最后一帧。

### 脱敏

`thinking` 和每个工具参数值写入前过 `redact_sensitive_text`。`browser_type` 的 `text` 再叠加官方 `redact_tool_args_for_display`。回答正文不再套一层：`kind=text` 在进 hook 前已经过 scrubber。

## 模板

插件运行时不创建模板。仓库提供：

- `templates/hermes-streaming-card.json`：布局和变量，字段与上表一致
- `templates/README.md`：导入失败时的手工搭建步骤

版面自上而下：色条、标题/模型/耗时、工具区、思考区、回答区、Footer。`thinking` 和 `content` 标成流式 markdown。

色条在模板里固定 `#1677FF`。`headerColor` 仍按状态写入；控制台若支持「样式绑定变量」，再把 `success` 绑 `#00B578`、`danger` 绑 `#F53F3F`。钉钉自带的「输入中」在流式开始后自动出现。`isFinalize` / `isError` 只驱动「完成」和「出错」。

### 长度

spike 回填前按这些上限实现。截断按 UTF-8 字符边界，并补齐未闭合的代码围栏。超出的回答正文用 webhook markdown 补发。

| 项 | 上限 |
|---|---|
| 单次流式帧 | 1024 字节 |
| `thinking` 累计 | 1024 字节，之后不再增长，末尾加 `…` |
| `content` 累计 | 2048 字节，之后不再增长，末尾加 `…`，footer 加「回答过长已截断」 |
| 每个普通变量的值 | 1024 字节 |

两个流式区各自遵守自己的上限，不把对方的额度拿来用。

## 失败

- 流式更新失败：同一变量重试 1 次。仍失败则把卡打成 `error`。错误帧也失败则停止，日志留下 `outTrackId`。无论卡是否收成错误，最终答案再经 webhook markdown 发一次。
- 建卡失败：只发 webhook markdown，不留半张卡。
- 建卡成功但投放失败：放弃该实例，日志留下 `outTrackId`，回落 webhook markdown。
- 依赖或模板 ID 缺失：加载时记 ERROR，走 webhook markdown。
- 超长：按长度表截断，剩余正文 webhook 补发。
- 用户中断：失败态，footer 为 `已停止`。
- 进程退出：卡停在生成中。没有后台定时任务。
- 孤儿卡：同一 `chat_id` 下次建卡时，把该会话里没有 `turn_id` 关联、且超过 10 分钟的卡收成 `error`，footer 为 `未完成`。
- 绑定失败：丢掉这次思考、工具或 token 更新。回答仍走 `edit_message`。
- 同一 session 的新轮：上一张卡 footer 为 `已结束`。另一个 session 的卡不动。

## 测试

不连接钉钉。工具分类、脱敏截断、状态迁移、帧合并做成纯函数。适配器用注入的假 client。

- `mcp__github__create_issue` 归 MCP，显示 `github.create_issue`；`terminal`、`browser_*`、其余原名；列表只留 5 条
- 工具参数单行、80 字符、敏感串被替换
- 截断落在字符边界，未闭合围栏被补齐，两区不共用额度
- `running → done` 与 `running → error`，中断 footer 为 `已停止`
- 段边界的 `edit_message(finalize=True)` 不写完成态，下一段复用同一 `out_track_id`
- 400ms 内多帧合并成一次；完成帧不被节流推迟
- 同一变量失败重试 1 次，再失败转 `error`；错误帧失败只记日志；流式失败后答案仍走一次 webhook
- `turn_id` 不匹配不写其他卡；同一 `turn_id` 换了 `session_id` 仍写同一张卡
- 两个 `turn_id` 共用一个 `chat_id` 时互不收口
- 终态信号都没到、且超过 10 分钟时，下次同 `chat_id` 建卡会把孤儿卡收成 `error`

钉钉 API 的实际上限另做一次 spike，不放进上述测试：用双变量模板依次打全量流式帧、`UpdateCard`、终态帧，记录单帧与累计上限、两个流式变量能否并存、流式过程中 `UpdateCard` 会不会清掉缓冲。若 `UpdateCard` 会清缓冲，完成顺序改为先终态帧、再写普通变量，并改状态机第 5 步。spike 只上调「长度」表里的数字，不取消 webhook 补发。

## 不做

- 不改 Hermes 核心
- 不在运行时创建钉钉模板
- 不与官方单 `content` 卡各发一张
- 不做后台定时清扫
- 不用 `set_status_text` 做工具区
- 不在 `pre_tool_call` 里发网络请求
- 不在 `pre_gateway_dispatch` 建卡
- 不用 `on_stream_end` 收口
- 不从回答正文里剥工具进度

## 依据

下列行为已按宿主 0.21.3 源码核对，正文规则由此而来。

| 规则 | 来源 |
|---|---|
| 钉钉默认不建 stream consumer | `gateway/display_config.py` 将钉钉归入 `_TIER_LOW` |
| 进线 hook 早于鉴权 | `gateway/run_inbound.py` |
| 段边界会清掉消息 id 并再次 `send()` | `gateway/stream_consumer.py`；官方 `send()` 会先关掉同会话开着的卡再新建 |
| 工具进度在钉钉上另建卡 | `accepts_tool_progress` 取决于 native streaming；钉钉没有该标志 |
| 回答正文不含 `---` 工具进度 | 该拼接只在 native streaming 分支 |
| `on_stream_end` 不是回合结束 | `agent/stream_delivery.py`，位于单次请求的重试循环内 |
| `finalize_turn` 有多条提前返回 | `agent/conversation_loop.py` |
| `turn_id` 出现在流、工具、usage、`on_session_end` | 对应的 agent hook 载荷；`agent_loop_stopped` 只有 `session_key` |
| 同群不同人可并行 | `group_sessions_per_user` 默认 true，锁在 session key 上 |
| 推理文本未脱敏，回答文本已 scrub | `agent/stream_delivery.py` |
| `redact_tool_args_for_display` 只处理 `browser_type.text` | `agent/display.py` |
| 卡片 SDK 不会被 `ensure_dingtalk_deps` 补装 | `plugins/platforms/dingtalk/adapter.py`、`tools/lazy_deps.py` |
| 平台注册是整条替换 | `gateway/platform_registry.py`；官方 `register()` 传入整组 entry 字段 |
