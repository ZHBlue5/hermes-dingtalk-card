# 钉钉增强版 AI Streaming Card

日期：2026-09-27  
状态：待审阅  
宿主：本机 Hermes Agent `0.21.3`（`~/.hermes/hermes-agent`）

本文是实现契约。宿主源码依据收在文末附录，正文不再重复修订过程。

## 目标

在钉钉企业机器人里，用一张 AI 卡片展示 Hermes 的一轮回复：状态、模型、耗时、token、最近几次工具调用、思考流、回答流。结束后 Header 色条和 Footer 切到完成或失败。

成功标准：

- 单聊展示思考区；群聊默认也展示，可用配置关掉
- 工具区是最近 5 条的滚动列表
- 一轮回复只保留一张增强卡（段边界不另建卡）
- 插件启用且配置了增强模板 ID 后，不再发官方只含 `content` 的卡

## 已定决策

| 项 | 决定 |
|---|---|
| 交付 | 本仓库插件 `dingtalk-streaming-card`，可选启用 |
| 做法 | 子类官方 `DingTalkAdapter`，`register_platform("dingtalk")` 整体替换 |
| 范围 | v1 一次做完：多变量、工具列表、双区流式、完成态、孤儿卡顺带收尾 |
| 与官方卡 | 互斥。有模板 ID 时只发增强卡 |
| 建卡 | 惰性。本轮首个真实输出时建卡，不在进线时建卡 |
| 卡片身份 | 一张卡对应一个 `turn_id`。`session_id → chat_id` 只决定投到哪个会话 |
| 段边界 | `edit_message(finalize=True)` 只落盘，不关卡、不写完成态；下一段复用同一 `out_track_id` |
| 终态 | `on_session_end` 为主，`agent_loop_stopped` 补中断，`api_request_error` / `post_tool_call(status=error)` 兜底。不用 `on_stream_end` 判断回合结束 |
| 思考 | 单聊始终展示。群聊默认展示；`DINGTALK_STREAMING_CARD_GROUP_THINKING=false` 或 `0` 时群聊不写 `thinking` |
| 工具区 | 最近 5 条，新的在上。参数先脱敏再截成一行，最多 80 个字符 |
| 脱敏 | `thinking` 与每个工具参数值写入前过 `redact_sensitive_text`。回答正文不再套一层 |
| 模板 | 仓库带描述文件。能导入就导入，否则按同一清单在控制台手建 |
| 超长 | 流式区按「长度预算」截断，剩余正文用 webhook markdown 补发 |

## 安装与注册

插件放到 `~/.hermes/plugins/dingtalk-streaming-card/`（拷贝或软链），再执行 `hermes plugins enable dingtalk-streaming-card`。

不依赖 `HERMES_ENABLE_PROJECT_PLUGINS`。不要只放在项目级 `.hermes/plugins` 里。

`plugin.yaml` 的 `kind` 为 `platform`，并声明 `provides_hooks`，列出实际注册的 hook。漏声明时加载仍会成功，`hermes plugins validate` / `plugins doctor` 会报 `undeclared hooks registered`。

发现顺序是先登记内置钉钉（延迟加载），再加载已启用的用户插件。`register_platform("dingtalk")` 会清掉同名延迟加载器。返回 `None` 表示没生效，必须打错误日志，不能当成成功。

`register_platform` 是整条 `PlatformEntry` 替换，没有字段合并。不要手抄官方那 17 个参数。做法：用只记录 kwargs 的 stub 调用官方 `register(ctx)`，把 `adapter_factory` 换成子类，再交给真正的 `register_platform`。

### 前置配置

缺任何一项，插件都按「失败」一节退化，并在启动日志里写明缺什么。

```yaml
display:
  platforms:
    dingtalk:
      streaming: true        # 否则网关不建 stream consumer，edit_message 不会被调用
      show_reasoning: false  # 为 true 时网关把 reasoning 前置进最终回答
      tool_progress: off     # 必须关闭，见下
plugins:
  stream_reasoning_deltas: true  # 否则没有 kind=reasoning
```

`tool_progress` 必须关闭。钉钉没有 `SUPPORTS_NATIVE_STREAMING`，进度不会并进回答流，而是另一次 `send()`。基类 `send()` 会先 `_close_streaming_siblings` 把该会话里还开着的卡 finalize 掉。进度气泡和回答卡会抢同一个槽。工具区由本插件的 hook 渲染，不需要这张气泡。

群聊思考开关：环境变量 `DINGTALK_STREAMING_CARD_GROUP_THINKING`。未设置、`true`、`1` 都表示展示。`false` 或 `0` 表示群聊不写 `thinking`。单聊不受影响。

### 依赖

卡片路径需要 `alibabacloud-dingtalk`（`CARD_SDK_AVAILABLE`）。`ensure_dingtalk_deps` 在 `dingtalk-stream` 和 `httpx` 已存在时直接返回，不会补装卡片 SDK。

`register(ctx)` 时探测这个包。缺失则记 ERROR 并打印安装命令，走 webhook markdown。不能只打警告：没有卡片 SDK 时 `SUPPORTS_MESSAGE_EDITING` 为假，`edit_message` 直接失败，增强卡等于没启用。

## 卡片变量

建卡时写入全部 `cardParamMap`。`thinking` 与 `content` 走流式接口（markdown，`isFull=true`，每次带全文）。其余变量走 `UpdateCard`（`PUT /v1.0/card/instances`）。官方适配器今天只有建卡和流式两条路径，这一调用是插件新增的。

| 变量 | 更新 | 含义 |
|---|---|---|
| `status` | 普通 | `running` / `done` / `error` |
| `headerColor` | 普通 | `info` / `success` / `danger` |
| `title` | 普通 | `Hermes` |
| `model` | 普通 | 当前模型名，没有则为 `—` |
| `elapsed` | 普通 | 如 `12.4s`，从回合起点算 |
| `tool` | 普通 | 最近 5 条工具的 markdown |
| `toolCount` | 普通 | 本次已调用次数 |
| `tokens` | 普通 | 运行中为 `—`；结束时如 `in 1.2k · out 340` |
| `thinking` | 流式 | 思考全文 |
| `content` | 流式 | 回答全文 |
| `footer` | 普通 | 见状态机 |

工具行示例：`Shell · ls -la`、`MCP · github.create_issue`、`Browser · …`。只看工具名，按顺序匹配，先匹配先生效：

1. 以 `mcp__` 开头 → MCP。显示名取 `mcp__{server}__{tool}` 的 `{server}.{tool}`
2. 等于 `terminal` → Shell
3. 以 `browser_` 开头 → Browser
4. 包含 `github`（大小写不敏感）→ GitHub
5. 其余用原名

因此 `mcp__github__create_issue` 归 MCP，显示 `github.create_issue`。没有 `mcp__` 前缀的工具不另判 MCP。

参数先对每个值做 `redact_sensitive_text`，再收成一行，最多 80 个字符。不用 `redact_tool_args_for_display` 当通用脱敏：它只处理 `browser_type` 的 `text`。`browser_type` 可以在通用脱敏之外再调一次，与官方展示对齐。

列表只留最近 5 条，新的在上。`pre_tool_call` 标「进行中」，`post_tool_call` 改为完成。

## 状态机

1. **创建。** 本轮首个 `on_stream_delta`（任意 kind）或首个 `pre_tool_call`，先到者建卡，并回填已经累积的思考、回答和工具。写入 `status=running`、`headerColor=info`、`footer=生成中`。不在 `pre_gateway_dispatch` 建卡：该 hook 在鉴权之前，会给未授权用户和最终没有回复的消息留下空卡。
2. **流式。** 思考和回答每 400ms 整段覆盖一次。长度见「长度预算」。
3. **工具。** 只从 hook 更新 `tool` 和 `toolCount`，不另开卡，也不从正文里解析工具行。
4. **段边界。** 每次工具调用前，网关会 `on_delta(None)` → `on_segment_break`，finalize 当前消息再开下一段。`close_for_approval_prompt` 对钉钉是空操作，不作为段边界。插件把这类 `edit_message(finalize=True)` 只当成缓冲区落盘，不关卡，不写完成态。下一段的第一个 delta 会走 `adapter.send()`。基类 `send()` 会先关掉同 chat 里还开着的卡，再建一张新卡。子类必须在 `send()` 里跳过这套 sibling 关闭和建卡，复用本 `turn_id` 已有的 `out_track_id`。
5. **完成。** 只由 `on_session_end` 判定（带 `completed` / `failed` / `interrupted` 和 `turn_id`）。`on_stream_end` 只包单次模型流，一轮会来多次，不能用来收卡。先写 `status=done`、`headerColor=success`、`tokens`、`footer=已完成 · {elapsed} · {tokens}`，再对已经流过的 `thinking` 和 `content` 发 `isFinalize=true`。内容和上一帧相同也要发。完成帧不走 400ms 节流。若 spike 证明流式过程中 `UpdateCard` 会打断缓冲，顺序改成先 `isFinalize`，再写普通变量。
6. **失败或中断。** `status=error`，`headerColor=danger`。失败时 footer 为一行短错误。用户中断（`on_session_end.interrupted`，或 `agent_loop_stopped`）时 footer 为 `已停止`。失败帧不走节流。

`isError` 一次只能打在一个流式 key 上。打在已经至少流过一帧的流上，多个都流过时打 `content`。两个都还没流过时不发 `isError`，只改 `status`、`headerColor`、`footer`。

终态三层一起用，因为 `finalize_turn` 之前的提前返回不会触发 `on_session_end`（预检压缩超时、codex 回退失败、retry 耗尽、内容策略拒答、`_Trunc.end_turn`，以及 `_persist_disabled` 的后台任务）：

- `on_session_end`：正常完成，以及能走到 finalize 的失败和中断
- `agent_loop_stopped`：主动停止。没有 `turn_id`，只有 `session_key`，按 `session_key` 找到卡
- `api_request_error` 或 `post_tool_call(status=error)`：兜住到不了 finalize 的失败

三层都没到的卡，按「失败」里的孤儿卡规则收。

`elapsed` 从本轮第一个带 `turn_id` 的 hook 到达时起算，不用建卡时刻，否则会漏掉首 token 之前的时间。完成和失败两帧立即 flush。

`tokens` 累加本轮 `post_api_request` 的 `usage`。失败的调用不计入。轮边界以 `on_session_end` 为准；没等到它时，在失败态写入已经累加到的值。

### 并发

串行化只到 session key，不到 `chat_id`。`group_sessions_per_user` 默认为真，同一个群里两个参与者可以各跑一轮，共用一个 `chat_id`。按 `turn_id` 区分，不按「是不是第二张卡」区分：

- 同一 `turn_id` 的段边界：复用原卡，不新建
- 同一 session 的重置或新轮：把该 session 上一张卡收成 `error`，footer 为 `上一轮已结束`
- 不同 `turn_id` 共用一个 `chat_id`：两张卡各自收尾，不改对方

## 数据从哪来

进程内以 `turn_id` 作为卡片身份。另有 `chat_id → {out_track_id: 卡}`，用来投递和收尾。

`on_stream_start`、`on_stream_delta`、`on_stream_end`、`pre_tool_call`、`post_tool_call`、`post_api_request`、`on_session_end` 都带 `turn_id`。`agent_loop_stopped` 没有，只能用 `session_key`。

投递目标：`pre_gateway_dispatch` 记下 `session_store`。载荷里的 `event.source.chat_id` 与出站 `send` / `edit_message` 的 `chat_id` 同源（入站是 `conversation_id or sender_id`）。`lookup_by_session_id` 在多个 key 指向同一 `session_id` 时取字典序第一个，只用来找会话，不用来当回合身份。对不上当前 `turn_id` 的旁路更新丢掉。

| 区域 | 来源 |
|---|---|
| `content` | 子类 `send` / `edit_message`，只写入本 `turn_id` 的卡。正文原样写入，不剥离 `---` |
| `thinking` | `on_stream_delta` 且 `kind=reasoning`，写入前脱敏。群聊开关关闭时不写 |
| 工具列表 | `pre_tool_call` 与 `post_tool_call` |
| `model` | 同一次 turn 的 hook 载荷 |
| `tokens` | `post_api_request` 的 usage |
| `elapsed` | 本轮第一个带 `turn_id` 的 hook |
| 完成 / 失败 | 终态三层。不用 `on_stream_end` |

### 线程

- `on_stream_delta` 跑在每个回调自己的 daemon 线程里，同步执行，不能 await。回调只改内存并入队，钉钉调用用 `asyncio.run_coroutine_threadsafe` 回到网关 loop。
- 该队列上限 1024，满了丢最旧。思考区按整段覆盖渲染，允许缺字，不为缺字重试。
- `pre_tool_call` 超时（默认 30 秒）或仍在运行会 fail-closed，工具被拦下。这里只做内存更新，不发网络请求。回调抛异常是 fail-open，不要用异常做控制流。
- 同一 `out_track_id` 的写操作单飞：合并同一变量的待发帧，思考、回答、普通参数按序发出。

子类继续用官方适配器的进线、媒体、权限和表情。建卡、流式更新、`UpdateCard`、`send` / `edit_message` 由子类实现。不用 `set_status_text`：钉钉的 `supports_status_text` 为假，那条短语约 49 字，没有稳定工具名。

## 模板

运行时不创建模板。仓库提供：

- `templates/hermes-streaming-card.json`：布局和变量，与上表一致
- `templates/README.md`：导入失败时的手工步骤

版面自上而下：色条、标题/模型/耗时、工具区、思考区、回答区、Footer。`thinking` 和 `content` 标成流式 markdown。

模板 ID 用钉钉平台现有的 `card_template_id`，不写进仓库。有值时只发增强卡，启动时打一条日志。没有值时走 webhook markdown，并写明增强卡未启用。换上增强模板后必须换成新的 ID：旧的单 `content` 模板缺变量时会渲染成空白，而不是报错。

### 长度预算

spike 实测之前，按下面的数实现。字节按 UTF-8 计，截断落在字符边界，不切出半个字符。被截断的正文补上未闭合的代码围栏。

| 项 | 预算 |
|---|---|
| 单次流式 `content` 字段 | 1024 字节 |
| `thinking` 与 `content` 累计合计 | 3072 字节 |
| 每个普通变量的 value | 1024 字节 |
| 每个普通变量的 key | 100 字节 |

合计 3072 来自钉钉对流式 markdown 的公开建议（单次约 1K，总量约 3K），不是 `MAX_MESSAGE_LENGTH`。官方适配器单次能发 20000 字符，那是单条消息上限，不是整卡预算。两个流式区从 3072 里一起扣，不能各自再取 10240。`thinking` 先占已写入的部分，`content` 用剩余额度。任一侧截断后，剩余正文用 webhook markdown 补发。完成态 footer 仍是 `已完成 · {elapsed} · {tokens}`，若思考被截断则在后面追加 ` · 思考过长已截断`，若回答被截断则追加 ` · 回答过长已截断`。失败态同样追加，不替换 `已停止` 或那一行短错误。

spike 若测出更高上限，或测出两个流式变量不能并存，先改本节再写流式发送。发送逻辑不另定一套数字。

### 色条

| 值 | 色 | 何时 |
|---|---|---|
| `info` | `#1677FF` | 生成中 |
| `success` | `#00B578` | 完成 |
| `danger` | `#F53F3F` | 失败或中断 |

控制台若不能把样式绑定到 `headerColor`，色条固定 `#1677FF`。完成和失败仍靠 `isFinalize` / `isError`。钉钉的「输入中」在流式开始后自动出现，不靠这两个标志。

## 失败

- 流式更新失败：同一变量重试 1 次。仍失败则把卡打成 `error`。错误帧也失败则停止，日志留下 `outTrackId`。无论卡是否收成错误，最终答案都再经 webhook markdown 发一次。
- 建卡失败：只发 webhook markdown，不留半张卡。
- 建卡成功但投放失败：放弃该实例，日志留下 `outTrackId`，回落 webhook markdown。
- 缺 `alibabacloud-dingtalk` 或没配 `card_template_id`：加载时记 ERROR 并给出安装或配置提示，走 webhook markdown。
- 超长：按长度预算截断，剩余正文补发 webhook markdown。
- 用户中断：失败态，footer 为 `已停止`。
- 进程中途退出：卡会停在生成中。v1 不做后台定时任务。
- 孤儿卡：同 `chat_id` 下次建卡时，收掉该 chat 下已没有 `turn_id` 关联、且距最后一次更新超过 120 秒的卡，打成 `error`，footer 为 `未正常结束`。不另起后台任务。
- 绑定失败：丢掉这次思考、工具或 token 更新。回答仍走 `edit_message`。
- 同一 session 的重置或新轮：上一张卡 footer 为 `上一轮已结束`。
- 不同 session 共用一个 `chat_id`：不改对方的卡。

## 测试

不连接钉钉。工具分类、脱敏截断、状态迁移、帧合并抽成不依赖 SDK 的函数。适配器层注入假 client。

- `mcp__github__create_issue` 归 MCP，显示 `github.create_issue`；`terminal`、`browser_*`、其余原名；列表只留 5 条
- 工具参数先脱敏再截成一行 80 字符
- 截断落在字符边界；`thinking` 与 `content` 合计不超过 3072 字节；未闭合围栏被补上
- `running → done`，以及 `running → error`（中断 footer 为 `已停止`）
- 段边界的 `edit_message(finalize=True)` 不写完成态，下一段不新建卡，复用同一 `out_track_id`
- 别的 `out_track_id` 上的 `send` / `edit_message` 不写入本卡的 `content`
- 400ms 内多帧合并成一次；完成帧和失败帧立即发出
- 同一变量失败后重试 1 次，再失败转 `error`；错误帧失败只记日志；流式失败后答案仍走一次 webhook markdown
- `turn_id` 不符不写别的卡；同一 `session_id` 的另一个 `turn_id` 也不写这张卡
- 同一 `turn_id`、`session_id` 变了，仍写这张卡
- 两个 `turn_id` 共用一个 `chat_id` 时互不修改
- 没有 `on_session_end` 时，超过 120 秒的孤儿卡在下次建卡时收成 `error`，footer 为 `未正常结束`

钉钉 API 契约不由这些测试覆盖。实现流式发送之前，先做「实现前 spike」。

## 不做

- 不改 Hermes 核心
- 不在运行时调用钉钉建模板接口
- 不与官方单 `content` 卡各发一张
- 不做后台定时清扫
- 不用 `set_status_text` 当工具列表
- 不在 `pre_tool_call` 里发网络请求
- 不在 `pre_gateway_dispatch` 建卡
- 不用 `on_stream_end` 判断回合结束

## 实现前 spike

流式发送的代码依赖这次实测。测完若与「长度预算」或完成帧顺序不一致，先改本规格再写那部分代码。用双变量模板手工建卡，依次打全量流式帧、`UpdateCard`、finalize 帧，记录：

1. 单帧上限、两个流式变量的累计上限、能否同时存在，以及超限时的错误码
2. 流式过程中 `UpdateCard` 会不会清掉或打断流式缓冲
3. 模板能否把色条样式绑定到 `headerColor`；不能则按「色条」一节固定蓝色

## 附录：宿主核对

下列结论来自 Hermes Agent 0.21.3 源码，正文已吸收，这里只留索引。

| 结论 | 位置 |
|---|---|
| 钉钉默认 `streaming: false`，不建 stream consumer | `gateway/display_config.py`，`gateway/run_turn_runner.py` |
| `pre_gateway_dispatch` 在鉴权之前 | `gateway/run_inbound.py` |
| 段边界会清掉 `_message_id`，下一段走 `send()` 并新建卡 | `gateway/stream_consumer.py`，`plugins/platforms/dingtalk/adapter.py` |
| `---` 工具进度只出现在 native streaming | `gateway/stream_consumer.py` |
| `on_stream_end` 是单次模型流，不能当回合结束 | `agent/stream_delivery.py`，`agent/conversation_loop.py` |
| `turn_id` 在流、工具、`post_api_request`、`on_session_end` 上有；`agent_loop_stopped` 没有 | `agent/stream_delivery.py`，`agent/turn_finalizer.py`，`gateway/run_agent_cache.py` |
| 群聊按参与者分 session，同一 `chat_id` 可以并发 | `gateway/session.py`，`gateway/config.py` |
| `on_stream_delta` 在 daemon 线程，队列丢最旧；`pre_tool_call` 超时才 fail-closed | `agent/plugin_stream_hooks.py`，`hermes_cli/plugins_dispatch.py` |
| reasoning 未过 scrubber；`redact_tool_args_for_display` 只处理 `browser_type` | `agent/stream_delivery.py`，`agent/display.py`，`agent/redact.py` |
| 卡片 SDK 不会被 `ensure_dingtalk_deps` 补装 | `plugins/platforms/dingtalk/adapter.py`，`tools/lazy_deps.py` |
| `register_platform` 整条替换；官方 `register()` 传 17 个参数 | `gateway/platform_registry.py`，`plugins/platforms/dingtalk/adapter.py` |
| MCP 工具名形如 `mcp__<server>__<tool>` | `tools/mcp_tool_schema.py` |
| `MAX_MESSAGE_LENGTH` 是单条消息上限，不是整卡预算 | `plugins/platforms/dingtalk/adapter.py` |
