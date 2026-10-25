#!/usr/bin/env bash
# ============================================================
# 研伴系统 · 一键部署（干净机器适用）
# 部署拓扑：mcp-gateway(8021) + 5 Partner(8031-8035) + Leader(8041) = 7 服务
# 依赖：Docker + Docker Compose；GLM_API_KEY（见 .env.example）
# 用法：bash deploy.sh
# 说明：demo.sh = 本脚本 + 自动提交一条演示任务；只想起服务用本脚本
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

PORTS=(8021 8031 8032 8033 8034 8035 8041)
LEADER="http://127.0.0.1:8041"
check() { curl -s -m 3 -o /dev/null -w "%{http_code}" "$1" 2>/dev/null || echo "000"; }

echo "══ [1/4] 前置检查 ══"
if ! docker info > /dev/null 2>&1; then
  echo "  Docker 引擎未启动，尝试拉起 Docker Desktop（最多等 5 分钟）…"
  powershell -NoProfile -Command "Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'" 2>/dev/null || true
  ok=0
  for i in $(seq 1 60); do
    docker info > /dev/null 2>&1 && { ok=1; break; }
    sleep 5
  done
  [ "$ok" = "1" ] || { echo "  ❌ Docker 引擎 5 分钟未就绪，请手动启动 Docker 后重试"; exit 1; }
fi
echo "  ✓ Docker 引擎在线"

if [ ! -f .env ]; then
  echo "  ⚠ 未发现 .env，正在从 .env.example 创建…"
  cp .env.example .env
  echo "  ❌ 请编辑 .env 填入真实 GLM_API_KEY 后重新运行本脚本"
  exit 1
fi
grep -q "^GLM_API_KEY=.\+" .env && ! grep -q "在此填入" .env \
  && echo "  ✓ .env 已配置" \
  || { echo "  ❌ .env 中 GLM_API_KEY 未填真实值（当前是占位符）"; exit 1; }

echo "══ [2/4] 构建并启动 7 个服务（首次构建 5-15 分钟属正常） ══"
docker compose up -d --build

echo "══ [3/4] 健康探活（每端点最多等 2 分钟） ══"
fail=0
for p in "${PORTS[@]}"; do
  ok=0
  for i in $(seq 1 60); do
    [ "$(check "http://127.0.0.1:$p/health")" = "200" ] && { ok=1; break; }
    sleep 2
  done
  if [ "$ok" = "1" ]; then echo "  ✓ $p /health 200"; else echo "  ✗ $p 探活失败"; fail=1; fi
done
[ "$fail" = "0" ] || { echo "══ 部署未完全成功，排查：docker compose ps && docker compose logs --tail=50 ══"; exit 1; }

echo "══ [4/4] 部署完成 ══"
echo "  工作台（推荐）:  $LEADER/"
echo "  双协议演示页:    $LEADER/  → 侧栏「协议演示」"
echo "  旧版兜底 UI:     $LEADER/ui"
echo "  网关 AIP 面:     http://127.0.0.1:8021/rpc"
echo "  网关 A2A 面:     http://127.0.0.1:8021/  (well-known: /.well-known/agent-card)"
echo "  常用命令: docker compose ps | docker compose logs -f leader | docker compose down"
