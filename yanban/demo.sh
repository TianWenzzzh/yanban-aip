#!/usr/bin/env bash
# 研伴一键演示（2026-09-18 工作台夜班 P6）
# 流程：栈未起则拉起（优先 Docker，引擎未启动先拉起 Docker Desktop，最多等 5 分钟）
#      → 7 端点探活 → 提交一条演示任务（确定性 SOP，命中 paper-pipeline 工作流）
#      → 提示打开工作台（8090 dev / 8041 单端口静态版 / 8041/ui 旧版兜底）
# 用法：Git Bash 里  bash demo.sh
set -euo pipefail
cd "$(dirname "$0")"

check() { curl -s -m 3 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null || echo "000"; }

PORTS=(8031 8032 8033 8034 8035 8021 8041)

all_up() {
  for p in "${PORTS[@]}"; do
    [ "$(check "http://127.0.0.1:$p/health")" = "200" ] || return 1
  done
  return 0
}

if all_up; then
  echo "[demo] 栈已在线（7/7）"
else
  echo "[demo] 栈未就绪，尝试拉起…"
  if ! docker info > /dev/null 2>&1; then
    echo "[demo] Docker 引擎未启动，拉起 Docker Desktop（最多等 5 分钟）…"
    powershell -NoProfile -Command "Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'" || true
    ok=0
    for i in $(seq 1 60); do
      if docker info > /dev/null 2>&1; then ok=1; break; fi
      sleep 5
    done
    [ "$ok" = "1" ] || { echo "[demo] Docker 引擎 5 分钟未就绪，退出"; exit 1; }
  fi
  echo "[demo] docker compose up -d --build（首次构建较慢属正常）…"
  docker compose up -d --build
fi

# 逐端点探活（最多再等 2 分钟）
for p in "${PORTS[@]}"; do
  ok=0
  for i in $(seq 1 60); do
    [ "$(check "http://127.0.0.1:$p/health")" = "200" ] && { ok=1; break; }
    sleep 2
  done
  [ "$ok" = "1" ] || { echo "[demo] 端口 $p 探活失败"; exit 1; }
  echo "[demo] 端口 $p ✓"
done
echo "[demo] 7/7 服务在线"

# 提交演示任务（UTF-8 由 heredoc 文件字节保证；命中 workflows.toml paper-pipeline SOP）
TASK=$(curl -s -m 30 -X POST http://127.0.0.1:8041/task \
  -H "Content-Type: application/json" \
  --data-binary @- <<'EOF'
{"query":"论文全流程：主题：多智能体协作提升科研效率"}
EOF
)
echo "[demo] 演示任务已提交：$TASK"
echo ""
echo "[demo] =========================================================="
echo "[demo] 打开工作台（新版）：  http://localhost:8041/"
echo "[demo] 开发模式（如已起）：  http://localhost:8090"
echo "[demo] 旧版兜底 UI：         http://localhost:8041/ui"
echo "[demo] 任务约 5-8 分钟跑完（4 阶段 DAG + 质量闭环），流水线面板实时刷新"
echo "[demo] =========================================================="
