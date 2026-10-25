# BOP 数据与工具获取清单（H8 · 固化备查；本脚本不执行大下载）

> 用途：机械臂论文阶段一键开跑。原则：先"小循环"（bop_toolkit+权重+自采集），benchmark 大集按需再下。
> 体积为官方页面/仓库公布口径，下载前请以 bop.felk.cvut.cz 当日页面复核。

## 一、小循环清单（第 1 周必做，总下载 <5GB）

| 项 | 获取方式 | 体积 | 落位 |
|---|---|---|---|
| bop_toolkit | `git clone https://github.com/thodan/bop_toolkit` | <50MB | `~/datasets/bop_toolkit` |
| FoundationPose 权重 | NVIDIA 官方 release 页（已精读项目，链接在手） | ~2GB | `~/weights/foundationpose/` |
| 分割前端（SAM/YOLO-World 按 FreeZeV2 README 指定） | HF/官方源 | 0.5-2GB | `~/weights/seg/` |
| FreeZeV2 代码+权重 | 其 GitHub release | 2-6GB | `~/code/freezev2/` |
| 自采物体集 | RealSense 采集脚本（FR3 仓库已有） | 本地生成 | `~/datasets/self_captures/` |

## 二、benchmark 大集（仅复现榜单数字时按需）

| 数据集 | 体积（约） | 优先级 |
|---|---|---|
| LM-O | 4GB | ★（ Household 场景，最贴近自采条件） |
| TUD-L | 2GB | ★（光照变化维度，对应实验的"三档光照"） |
| ITODD | 6GB | ★★（工业件，反光金属——与我们"反光档"呼应） |
| HB | 15GB+ | 复现 BOP 全榜才需要 |

## 三、目录约定与校验

```
~/datasets/bop/
├── bop_toolkit/
├── lm_o/          # test/ + camera.json + dataset_info.md
├── tud_l/
├── itodd/
└── models/        # 各集 CAD 统一放 BOP 官网 models 包
```

- 每个数据集下载后：`sha256sum` 记录到本目录 `bop_checksums.txt`（评测可复现）
- 评测输出统一走 `bop_toolkit/scripts/eval_bop19_pose.py` 口径，结果 CSV 落 `~/experiments/<date>_<method>/`

## 四、带宽与磁盘预算

- 小循环 <5GB：校园网一晚；**不要落 E 盘 U 盘**（R6）
- 全量 benchmark ≈30GB：确认 `~/` 分区 ≥50GB 余量再动手
- 下载统一挂夜间（实验室网络空闲时段），用 `wget -c` 断点续传
