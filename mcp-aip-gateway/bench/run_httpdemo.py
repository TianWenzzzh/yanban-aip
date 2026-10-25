"""httpdemo 演示上游的托管启动器：把服务进程自身 PID 落盘，供精确清理（R6 红线）。

用法：uv run python bench/run_httpdemo.py [port]   # 默认 8022
停止：kill -9 -W $(cat logs/httpdemo.pid)          # 只按落盘的精确 PID
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
port = sys.argv[1] if len(sys.argv) > 1 else "8022"
(ROOT / "logs" / "httpdemo.pid").write_text(str(os.getpid()), encoding="ascii")
sys.argv = ["demo_mcp_server.py", "--http", port]
runpy.run_path(str(ROOT / "demo_mcp_server.py"), run_name="__main__")
