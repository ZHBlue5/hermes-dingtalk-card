"""Load dingtalk-streaming-card the same way the Hermes host does.

AI-GENERATED (Cursor)
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "dingtalk-streaming-card"
PKG_NAME = "hermes_plugins.dingtalk_streaming_card"

# Host puts hermes-agent on path; it does NOT put the plugin dir on sys.path.
sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))


def load_plugin_package():
    """importlib spec_from_file_location + submodule_search_locations + __path__."""
    if PKG_NAME in sys.modules and getattr(sys.modules[PKG_NAME], "__path__", None):
        return sys.modules[PKG_NAME]

    if "hermes_plugins" not in sys.modules:
        parent = types.ModuleType("hermes_plugins")
        parent.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_plugins"] = parent

    init_path = PLUGIN_DIR / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        PKG_NAME,
        init_path,
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[PKG_NAME] = module
    spec.loader.exec_module(module)
    return module


load_plugin_package()
