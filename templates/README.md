# DingTalk 增强版 AI Streaming Card — 模板与安装

本目录提供 `hermes-streaming-card.json`（版面与变量清单）及安装说明。能导入则导入；否则按 JSON 中的 `layout` 在钉钉卡片控制台手工搭建，**变量名必须与 JSON 完全一致**。

## 安装插件

1. 将本仓库中的 `dingtalk-streaming-card` 目录软链到 Hermes 插件目录：

   ```bash
   ln -s "$(pwd)/dingtalk-streaming-card" ~/.hermes/plugins/dingtalk-streaming-card
   ```

2. 启用插件：

   ```bash
   hermes plugins enable dingtalk-streaming-card
   ```

3. 安装卡片 SDK（宿主 `ensure_dingtalk_deps` 不会自动补装）：

   ```bash
   pip install alibabacloud-dingtalk
   ```

## 宿主配置（`~/.hermes/config.yaml` 或项目配置）

`display.platforms.dingtalk`：

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

- `streaming: true`：否则网关不为钉钉建立 stream consumer，回答不会经 `edit_message` 更新。
- `stream_reasoning_deltas: true`：否则没有 `kind=reasoning` 的思考流。
- `show_reasoning: false`：避免思考被再贴进最终回答。
- `tool_progress: off`：钉钉没有 native streaming；开启进度会另建一张卡，并在每次 `send()` 时关掉该会话里还开着的卡。

插件配置（同文件 `plugins.entries`）：

```yaml
plugins:
  entries:
    dingtalk-streaming-card:
      settings:
        group_thinking: true
```

## 模板 ID

模板 ID 仍写在钉钉平台已有的 `card_template_id` 字段，**不要**写进本仓库。

- 换用本增强模板后，**必须**在配置里换成新模板 ID。旧的单 `content` 模板缺少变量时会渲染成空白。
- 控制台手工建模板时，变量名必须与 `hermes-streaming-card.json` 的 `variables` 列表一致（含 `thinking`、`content` 流式 markdown 区）。

## 控制台手工搭建要点

自上而下：色条 → 标题/模型/耗时 → 工具区 → 思考区 → 回答区 → Footer。

- 色条：v1 固定 `#1677FF`（`header.fallbackColor`）。若控制台支持样式绑定变量，可将 `headerColor` 的 `success` / `danger` 绑为 `#00B578` / `#F53F3F`（见 JSON `header.bindings`）；不支持则保持 fallback。
- `thinking`、`content`：流式 markdown，`isFull=true` 全量覆盖。
- 其余变量走 `UpdateCard`（`PUT /v1.0/card/instances`）。

长度上限见 JSON `limits`；超长部分由插件经 webhook markdown 补发。

## Spike（手动，不跑 pytest）

用于探测钉钉 API 实际上限，以及 `UpdateCard` 是否会清空流式缓冲。

1. 在控制台建一个**仅含两个流式变量**的测试模板（例如 `thinking`、`content`）。
2. 依次调用：
   - 一帧 **1024 字节**全量流式更新（`isFull=true`）；
   - 一次 **`UpdateCard`** 写入任意普通变量；
   - 一次 **`isFinalize=true`** 终态帧。
3. 记录：单帧上限、累计上限、两个流式变量能否并存、**流式过程中 `UpdateCard` 是否清掉未 finalize 的缓冲**。

若 `UpdateCard` **会**清缓冲：

- 将 `dingtalk-streaming-card/publisher.py` 的 `TERMINAL_ORDER` 改为 `("stream", "params")`；
- 并更新规格状态机第 5 步：完成时先对流式变量发 `isFinalize`，再 `UpdateCard` 写入 `status=done` 等普通变量。

Spike 结果只上调长度表数字，不取消 webhook 补发。
