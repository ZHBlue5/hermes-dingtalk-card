# 钉钉增强流式卡片

这是 Hermes Agent 的用户插件。启用后，钉钉机器人的回复改成一张 AI 流式卡片：标题、模型、耗时、最近几条工具调用、思考、回答和页脚。每个回合一张卡。

它替换官方钉钉平台适配器，沿用你已经配好的钉钉机器人（Client ID、Secret、Gateway）。官方那张只含 `content` 的卡片不再发送。

宿主版本按 Hermes Agent 0.21.3 核对过。

## 使用前

钉钉机器人已经能在单聊或群里收到消息，并且 `hermes gateway` 能连上。本插件不负责创建机器人。

## 1. 导入卡片模板

1. 打开 [钉钉卡片平台](https://open-dev.dingtalk.com/fe/card)，新建一个模板。
2. 右上角 **更多 → 导入**，选择本仓库的 `templates/hermes-streaming-card.json`。
3. 发布模板，复制模板 ID。

导入后不要改变量名。插件写入这些名字：`status`、`headerColor`、`title`、`model`、`elapsed`、`tool`、`toolCount`、`tokens`、`thinking`、`content`、`footer`。`thinking` 和 `content` 是流式区。

把模板 ID 写进 Hermes 配置 `~/.hermes/config.yaml`（与官方 AI 卡片同一字段）：

```yaml
platforms:
  dingtalk:
    enabled: true
    extra:
      card_template_id: "新模板ID"
```

必须换成这次导入的模板 ID。旧的单 `content` 模板缺少变量，卡片会是空白。

## 2. 安装并启用插件

在本仓库根目录执行：

```bash
ln -s "$(pwd)/dingtalk-streaming-card" ~/.hermes/plugins/dingtalk-streaming-card
hermes plugins enable dingtalk-streaming-card
pip install alibabacloud-dingtalk
```

`pip install` 要装进运行 `hermes gateway` 的那个 Python 环境。宿主在已有 `dingtalk-stream` 和 `httpx` 时不会自动安装卡片 SDK。

## 3. 打开流式相关开关

仍在 `~/.hermes/config.yaml`：

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

- `streaming: true`：回答才会持续写进同一张卡。
- `stream_reasoning_deltas: true`：思考区才有内容。
- `show_reasoning: false`：思考只出现在思考区。
- `tool_progress: off`：工具进度走卡片上的工具列表。打开后会另建一张卡，并关掉当前这张。

群聊是否显示思考，默认显示。要关掉群聊思考、保留单聊思考：

```yaml
plugins:
  entries:
    dingtalk-streaming-card:
      settings:
        group_thinking: false
```

## 4. 重启并试一条消息

```bash
hermes gateway
```

在单聊或已把机器人拉进的群里发一条消息。生成过程中卡片处于输入中，结束后变为完成。中断时页脚为「已停止」，失败时为出错状态。

日志里出现下面这一行，说明增强卡已按模板启用：

```text
Enhanced DingTalk streaming card enabled (template=你的模板ID)
```

若日志是 `Enhanced streaming card disabled`，回复会退回普通 webhook markdown。常见原因是没装 `alibabacloud-dingtalk`，或 `card_template_id` 为空。

## 卡片上有什么

自上而下：蓝色条、标题、模型、耗时、最近 5 条工具（新的在上）、思考、回答、页脚。耗时从本回合开始算，例如 `12.4s`。token 形如 `in 1.2k · out 340`，没有用量时为 `—`。

思考累计超过 1024 字节后停止增长。回答累计超过 2048 字节后，卡片里截断，完整超出部分再用一条 webhook markdown 补发。工具参数里的密钥会打码。

同一会话里开始新一轮时，上一张卡收成「已结束」。群里不同的人各有各的卡，不会互相关掉。超过 10 分钟、已经对不上回合的卡，会在下一次建卡时收成「未完成」。

## 模板文件

`templates/README.md` 说明这份 JSON 的版面，以及需要手工探测钉钉长度上限时怎么做。日常使用只需要完成本文第 1 到第 4 步。
