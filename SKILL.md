# Project: rebot-b601-102-record

## 概述

**StarAi Violin / Arm-102 主臂 → reBot B601-DM 从臂** 的遥操 / 录制 / 回放常驻服务。主臂驱动对齐 LeRobot `rebot_102_leader`（`motorbridge-smart-servo`）；从臂沿用本仓库 `u2can/DM_CAN`。

**当前状态（2026-08-20）**：已部署到现场 Raspberry Pi 5（`192.168.1.82:1882`）。在上游 `fee8dfd` 之上完成本地增强：关节校准、电机映射（含正反向）、停止运行/自由拖动、急停恢复、回放过渡修复、**双臂串口探活与就地重连**等。详见 [`docs/变更记录.md`](docs/变更记录.md)。

| 项 | 值 |
|---|---|
| 本仓库 | `D:\Leaf\AI_Work\Project\rebot-b601-102-record\` |
| 上游 | https://github.com/Love4yzp/rebot-b601-102-record-demo |
| 上游 commit | `fee8dfd`（2026-05-13） |
| 现场主机 | Raspberry Pi 5，`192.168.1.82`，用户 `seeed` / `seeed` |
| 现场项目 | `/home/seeed/seeed/Project/rebot-b601-102-record/` |
| 现场环境 | `/home/seeed/seeed/ENV/rebot-b601-102-record.env` |
| 访问 | http://192.168.1.82:1882 |
| 运行环境 | **Linux + Docker**（Web UI 跨平台） |
| 对外端口 | **1882** → 容器内 8000 |

业务细节、API、排障：[`docs/功能说明.md`](docs/功能说明.md)。变更摘要：[`docs/变更记录.md`](docs/变更记录.md)。上游说明：[`README.md`](README.md)。架构笔记：[`CLAUDE.md`](CLAUDE.md)。来源：[`docs/来源.md`](docs/来源.md)。

---

## 适用 / 不适用

**适用**

- 现场演示：主臂拖动、从臂跟随（含校准映射与电机重映射）
- 录制 / 循环或单次回放动作库
- 停止运行后手掰从臂，再恢复跟随
- 无硬件时用 `REBOT_MOCK=1` 联调 UI

**不适用**

- 多从臂协同、力控、视觉伺服
- Windows 上直连 USB 真机控制（官方路径是 Linux Docker）

---

## 硬件

| 角色 | 产品 | USB 特征 | 设备节点 |
|------|------|----------|----------|
| 主臂（只读） | StarAi Violin / Arm-102，1M baud | CH340 `1a86:7523` | `/dev/rebot-master`（major 188） |
| 从臂（写电机） | reBot B601-DM，921600 | HDSC CDC / CH343 | `/dev/rebot-slave`（major 166/170） |

- udev：[`deploy/udev/99-rebot-arms.rules`](deploy/udev/99-rebot-arms.rules)
- 换 USB 口一般仍可用（按芯片身份识别，不靠插口序号）
- 电源：主臂侧常 12V；从臂 **24V**（J1 故障僵硬时可能需断电重上电）

---

## 目录

| 路径 | 说明 |
|------|------|
| `SKILL.md` | **AI 入口（本文件）** |
| `docs/功能说明.md` | 功能、架构、API、部署、排障 |
| `docs/变更记录.md` | 相对上游的本地改动清单 |
| `docs/来源.md` | 上游仓库与 commit |
| `README.md` | 给人看的上游风格说明 |
| `CLAUDE.md` | 架构笔记 |
| `backend/controller.py` | 状态机（含 calibrate / free_move / motor map） |
| `backend/calibration.py` | 行程校准 |
| `backend/motor_map.py` | 电机映射 + 正反向 |
| `backend/pipermate.py` | 主臂 FashionStar |
| `backend/u2can/` | 从臂 DM_CAN |
| `frontend/` | React SPA |
| `deploy/docker-compose.seeed.yml` | 现场 compose |
| `deploy/udev/` | 稳定设备名规则 |
| `recordings/` | 动作库 + `calibration.json` + `motor_map.json`（volume，常不进 git） |

---

## 快速上手

### A. 无硬件联调 UI（Windows 也可）

```powershell
cd D:\Leaf\AI_Work\Project\rebot-b601-102-record
$env:REBOT_MOCK=1
uv sync
uv run uvicorn backend.app:app --reload --port 8000
# 另开终端
cd frontend; npm install; npm run dev
```

### B. 现场 Pi

```bash
cd /home/seeed/seeed/Project/rebot-b601-102-record
docker compose -f deploy/docker-compose.seeed.yml up -d --build
# 浏览器 http://192.168.1.82:1882
```

首次使用：中间栏「开始校准」扫满行程 → 完成 → 跟随。可选配置电机映射表。

### C. 从本机同步代码到 Pi（开发约定）

- 改 `backend/`：SCP 后 `docker compose ... up -d --force-recreate`
- 改 `frontend/`：SCP 后 `up -d --build`
- **不要**用测试脚本覆盖现场 `recordings/motor_map.json` / `calibration.json`

---

## 状态机速查

`idle` →（校准）→ `follow` ↔ `record` / `playback`（经 `transition`）  
`follow` → `free_move`（停止运行）→ `resume` → `follow`  
`follow` → `paused`（急停）→ `resume` → `follow`  
`calibrate`：双臂自由拖动采 min/max

回放 `transition` **必须在从臂空间插值**（见变更记录 E），否则 J1 会猛甩。

串口/断电：底栏「机械臂状态」；恢复后需「解除锁定」再跟随（见变更记录 H）。
