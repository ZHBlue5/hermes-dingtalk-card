import json
from pathlib import Path

def test_template_lists_every_variable():
    data = json.loads(Path("templates/hermes-streaming-card.json").read_text())
    keys = set(data["variables"])
    assert keys == {
        "status", "headerColor", "title", "model", "elapsed", "tool",
        "toolCount", "tokens", "thinking", "content", "footer",
    }
    assert data["streaming"] == ["thinking", "content"]
    assert data["header"]["fallbackColor"] == "#1677FF"
    assert "streaming: true" in Path("templates/README.md").read_text()
    assert "tool_progress: off" in Path("templates/README.md").read_text()
