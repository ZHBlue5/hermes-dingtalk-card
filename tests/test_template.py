import json
from pathlib import Path


def test_template_is_dingtalk_card_import():
    data = json.loads(Path("templates/hermes-streaming-card.json").read_text())
    assert data["type"] == "im"
    assert data["mode"] == "card"
    assert isinstance(data["editorData"], str)
    editor = json.loads(data["editorData"])
    names = {item["name"] for item in editor["variableList"]}
    assert {
        "status",
        "headerColor",
        "title",
        "model",
        "elapsed",
        "tool",
        "toolCount",
        "tokens",
        "thinking",
        "content",
        "footer",
    } <= names
    streaming = [
        block["props"]["content"]["variable"]
        for block in _walk(editor["schema"]["componentsTree"])
        if block.get("componentName") == "MarkdownBlock" and block["props"].get("isStreaming")
    ]
    assert set(streaming) == {"thinking", "content"}
    usage = Path("README.md").read_text()
    assert "streaming: true" in usage
    assert "tool_progress: off" in usage
    assert "hermes plugins enable dingtalk-streaming-card" in usage
    template_doc = Path("templates/README.md").read_text()
    assert "更多" in template_doc and "导入" in template_doc


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.get("children") or [])
