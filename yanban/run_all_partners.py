"""一键启动全部研伴 Partner（子进程管理，8031-8035）。"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SERVICES = [
    ("literature", "literature_partner:app", 8031),
    ("experiment", "partner_experiment:app", 8032),
    ("analysis", "partner_analysis:app", 8033),
    ("writing", "partner_writing:app", 8034),
    ("review", "partner_review:app", 8035),
]


def main() -> None:
    env = {**os.environ}
    if "GLM_API_KEY" not in env:
        env_file = HERE.parent / "ACPs-community" / "demo-partner" / ".env"
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("PARTNER_LLM_DEFAULT_API_KEY="):
                env["GLM_API_KEY"] = line.split("=", 1)[1]
                break
    procs: list[tuple[str, subprocess.Popen]] = []
    for name, app, port in SERVICES:
        p = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(HERE), env=env,
            stdout=(HERE / "logs" / f"{name}.stdout.log").open("ab"),
            stderr=subprocess.STDOUT,
        )
        procs.append((name, p))
        print(f"[run-all] {name} pid={p.pid} port={port}")
    try:
        for name, p in procs:
            p.wait()
    except KeyboardInterrupt:
        for name, p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
