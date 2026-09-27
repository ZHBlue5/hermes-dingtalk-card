# 卡片模板

日常安装、配置和发消息见仓库根目录 [README.md](../README.md)。本文件只说明模板 JSON。

`hermes-streaming-card.json` 是钉钉卡片平台的导入文件（`editorData` 为 JSON 字符串，`type` 为 `im`，`mode` 为 `card`）。

## 导入模板

1. 打开 [卡片平台](https://open-dev.dingtalk.com/fe/card)，新建一个卡片模板。
2. 右上角菜单选择 **更多 → 导入**，选中 `hermes-streaming-card.json`。
3. 导入成功后发布模板，把模板 ID 配到钉钉平台已有的 `card_template_id`。

`thinking` 和 `content` 是流式 markdown（`isStreaming: true`）。标题、模型、耗时、工具、token、页脚走普通变量更新。`headerColor` 仍会写入；色条在模板里固定为 `#1677FF`，完成/出错由卡片自身的输入中、完成、出错状态显示。

## 版面

自上而下：色条 → 标题 / 模型 / 耗时 → 工具区 → 思考区 → 回答区 → 页脚。同一版面放在输入中、完成、出错三个状态里，收口后正文还在。

- 色条固定 `#1677FF`。`headerColor` 会写入 `info` / `success` / `danger`，v1 不拿它改色条。
- `thinking`、`content`：流式 markdown，`isFull=true` 全量覆盖。
- 其余变量走 `UpdateCard`（`PUT /v1.0/card/instances`）。

长度：流式单帧 1024 字节，思考累计 1024，回答累计 2048，普通变量每个值 1024。超长回答由插件经 webhook markdown 补发。

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
