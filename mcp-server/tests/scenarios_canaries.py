import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scenarios" / "_generator"))
from common import PII_CANARIES  # noqa: E402,F401
