"""Package load must match host importlib layout (no bare top-level imports).

AI-GENERATED (Cursor)
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "dingtalk-streaming-card"
PKG_NAME = "hermes_plugins.dingtalk_streaming_card"


def test_plugin_loads_as_package_without_plugin_dir_on_sys_path():
    """Host: spec_from_file_location + submodule_search_locations；插件目录不在 sys.path。"""
    # 清掉包与可能的顶层污染，再按宿主方式重载
    for key in list(sys.modules):
        if key == PKG_NAME or key.startswith(PKG_NAME + "."):
            del sys.modules[key]
    for bare in (
        "adapter",
        "hooks",
        "turnbook",
        "textutil",
        "coalesce",
        "publisher",
        "routing",
    ):
        sys.modules.pop(bare, None)

    saved_path = list(sys.path)
    try:
        sys.path = [p for p in sys.path if Path(p).resolve() != PLUGIN_DIR.resolve()]
        assert str(PLUGIN_DIR) not in sys.path
        assert str(PLUGIN_DIR.resolve()) not in {
            str(Path(p).resolve()) for p in sys.path if p
        }

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

        assert hasattr(module, "register")
        turnbook = importlib.import_module(f"{PKG_NAME}.turnbook")
        assert hasattr(turnbook, "TurnBook")

        # 若插件仍用顶层 `import adapter`，在无插件目录的 path 上会失败；
        # 相对导入时包内模块已可用，顶层 adapter 不应被需要。
        try:
            importlib.import_module("adapter")
            loaded_bare = True
        except ModuleNotFoundError:
            loaded_bare = False
        assert loaded_bare is False, (
            "top-level `import adapter` must not succeed without plugin dir on sys.path"
        )
    finally:
        sys.path = saved_path
