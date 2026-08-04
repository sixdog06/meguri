"""仓库根 conftest：从根裸跑 `pytest` 只跑行为测试（backend/tests）。

- eval/ 是独立评测 harness（不进默认门禁），显式运行：pytest eval/
- 行为测试的 app 导入需要 backend/ 上 sys.path（backend/pytest.ini 的
  pythonpath 只在 rootdir=backend 时生效），这里补齐并固定测试环境。
"""

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "backend"
sys.path.insert(0, str(BACKEND))

import testsupport  # noqa: E402,F401  (import 即固定测试环境)

collect_ignore = ["eval", ".venv", "frontend", "backend/.venv"]
