import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dingtalk-streaming-card"))
sys.path.insert(0, str(Path.home() / ".hermes" / "hermes-agent"))
