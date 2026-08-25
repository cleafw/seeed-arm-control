# seeed-arm-control

> **文档**：[`SKILL.md`](./SKILL.md)　[`docs/功能说明.md`](./docs/功能说明.md)　[`docs/来源.md`](./docs/来源.md)　[`docs/变更记录.md`](./docs/变更记录.md)  
> **上游基准**：https://github.com/Love4yzp/rebot-b601-102-record-demo （`fee8dfd`）  
> **原目录名**：`rebot-b601-102-record`

主臂（StarAi Violin / Arm-102）→ 从臂（reBot B601-DM）遥操、录制、回放的常驻服务（正演进为可插拔臂型的通用控制台）。
浏览器即客户端：装好之后，机器人主机开机自启，运维人员从局域网内任何电脑打开网页就能用。

> **Linux + Docker**。Web UI 跨平台（任何浏览器）。  
> Docker 部署对外端口 **1882**（`1882:8000`）；容器内 / 本机 `uvicorn` 仍是 **8000**。本地验收：`start-local.bat` → http://localhost:5173

---

## 功能

- **跟随**（默认）：主臂 → 校准映射 + 电机映射 → 从臂 30Hz 遥操
- **关节校准**：双臂自由扫行程，生成持久化行程映射（首次使用必做）
- **电机映射**：主臂关节 ↔ 从臂电机（可「无」）+ 正向/反向，写入 `motor_map.json`
- **录制**：跟随的同时把动作存进动作库（动态数量、可命名）
- **回放（Loop / 执行一次）**：播放前平滑过渡到首帧；Once 结束后慢回主臂当前姿态
- **停止运行 / 恢复跟随**：解锁电机自由拖动（无阻尼），再缓移回遥操
- **急停 / 解除锁定**：保持位姿锁定；恢复时重新使能并缓移
- **常驻**：Docker 开机自启 + `unless-stopped`；串口/断电就地重连（底栏双臂状态）
- **Web UI**：模式驱动单页（关节遥测 + 校准 + 映射表 + 动作库 + 机械臂状态）

更完整的说明与排障见 [`docs/功能说明.md`](./docs/功能说明.md)；本地改动见 [`docs/变更记录.md`](./docs/变更记录.md)。

---

## 快速部署

### 1. 检查硬件 & udev majors

部署机上确认 USB 设备 char major 号（影响 docker 的 `device_cgroup_rules`）：

```bash
ls -l /dev/ttyUSB0 /dev/ttyACM0
# 输出形如 "crw-rw---- 1 root dialout 188, 0 ..." → 188 是 CH340
#                                       "166, 0 ..." → 166 是 CDC ACM
```

CH340（B601-DM 主臂）一般是 188，HDSC CDC（SO102 从臂）一般是 166。
如果你的内核不同，编辑 `deploy/docker-compose.yml` 里的 `device_cgroup_rules`。

### 2. 启动

```bash
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml logs -f
```

第一次构建需要几分钟（npm + uv sync）。之后 `up -d` 秒级。

### 3. 打开 Web UI

任意 LAN 内电脑浏览器：

```
http://<部署机IP>:1882
```

（compose 映射 `1882:8000`：主机 1882 → 容器内 uvicorn 8000。）

---

## 使用流程

1. **首次**：中间栏「开始校准」→ 手掰主臂、从臂扫满各轴 →「完成校准」。
2. 默认 **跟随**（可按需改左侧「电机映射」；改完会永久保存）。
3. 录制：输入名称 → **开始录制** → 圆形停止键结束。
4. 回放：动作库点「循环」或「执行」；开头应平滑过渡，不应猛甩。
5. **停止运行**：解锁从臂可自由拖动；再点 **恢复跟随** 缓移回遥操。
6. **急停**：从臂锁住当前姿态；**解除锁定** 后继续跟随。
7. 改名 / 删除 / 搜索：同原 UI（inline 改名、二次确认删除、「搜索全部」）。

---

## 架构

```
┌──────────────────────────────────────────────┐
│ Browser (任何 LAN 内电脑)                    │
│  └─ React + Tailwind SPA (mode-driven UI)    │
│       │ HTTP REST + WebSocket                │
└───────┼──────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────┐
│ Container: rebot-record                      │
│  ├─ FastAPI (uvicorn)                        │
│  │   ├─ /api/*  REST                         │
│  │   ├─ /ws     state push @10Hz             │
│  │   └─ /       静态前端                     │
│  └─ Controller thread (30Hz)                 │
│       ├─ master read (CH340 /dev/ttyUSB*)    │
│       └─ slave write (HDSC CDC /dev/ttyACM*) │
└──────────────────────────────────────────────┘
```

- 控制循环跑在独立 Python 线程；FastAPI handler 持锁微秒级调用 Controller 命令。
- 状态推送：控制线程 → `asyncio.Queue` → WS 广播。任何客户端 buffer 满直接丢帧。
- USB 断开 → `SerialException` → 进程退出 → `restart: unless-stopped` 拉起。
- `docker stop` 触发 SIGTERM handler，先跑 `safe_shutdown`（缓慢回零 + 失能）再退。

---

## 配置（环境变量）

全部可在 compose 文件的 `environment:` 设置。

| 变量 | 默认 | 说明 |
|------|------|------|
| `MASTER_PORT` | 自动检测 | 强制指定主臂端口（VID/PID 检测多候选时有用） |
| `SLAVE_PORT` | 自动检测 | 强制指定从臂端口 |
| `REBOT_BAUDRATE` | `921600` | 从臂 DM_CAN 波特率 |
| `REBOT_UPDATE_HZ` | `30` | 控制循环频率 |
| `REBOT_RETURN_TIME` | `2.0` | "执行一次"后慢回主臂的时长（秒） |
| `REBOT_TRANSITION_TIME` | `0.6` | 播放前从当前姿态过渡到动作首帧的时长（秒） |
| `REBOT_LOOP_BLEND_TIME` | `0.30` | Loop 模式末尾→开头的平滑过渡（秒） |
| `REBOT_END_HOLD_TIME` | `0.15` | 录制末尾保持帧（秒） |
| `REBOT_GRIPPER` | `1` | 是否带夹爪 |
| `REBOT_RECORDINGS_DIR` | `recordings/` | 动作库根目录 |
| `REBOT_WS_PUSH_HZ` | `10` | WebSocket 推送频率 |
| `REBOT_MOCK` | `0` | `1` = 合成关节数据、跳过串口 I/O，仅用于 UI 联调 / macOS 本地开发 |

---

## 数据 & 持久化

```
recordings/
└── actions/
    └── <id>.json   # 一个动作一个文件（id 是 UUID）
```

每个 JSON：

```json
{
  "id": "01J9...",
  "name": "wave_hello",
  "created_at": "2026-05-07T03:14:00Z",
  "default_play_mode": "once",
  "duration_s": 4.832,
  "frames": [{"t": 0.0, "joint_states": {"joint1": 0.0, "...": 0.0}}, "..."]
}
```

直接 cp 到别处就是备份。

### 老数据迁移（5 槽 → 动作库）

如果之前用过老 demo，根目录有 `slot_<N>.json`。**首次启动**容器时如果 `actions/` 是空的会自动导入，命名 `slot_<N> (imported)`，**不删除**老文件。已经迁移过就跳过（幂等）。

---

## 开发模式

```bash
# 后端（有真机 / Linux 上跑）
uv sync
uv run uvicorn backend.app:app --reload --port 8000

# 后端（无硬件 / macOS 联调 UI）
REBOT_MOCK=1 uv run uvicorn backend.app:app --reload --port 8000

# 前端（Vite dev，:5173 → 代理 /api 和 /ws 到 :8000）
cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。修改 `frontend/src/*.tsx` 热重载。

**Mock 模式**：合成 7 路关节的慢正弦运动，slave 端 no-op。完整状态机（follow/record/transition/playback/return_to_follow）行为正常，时长/动作库 IO 都真跑，唯一区别是关节数据和电机控制都是假的。专门给 UI 联调用，**不要用于真机回归**。

---

## 故障排查

**容器启动后立刻退出 / 反复重启**

```bash
docker compose -f deploy/docker-compose.yml logs --tail 50
```

常见原因：
- `Master arm port not found` → 主臂未连接或 VID/PID 不匹配。检查 `lsusb`，必要时 `MASTER_PORT=/dev/ttyUSB1` 强制指定。
- `Slave arm port not found` → 从臂未连接（HDSC CDC）。
- `Permission denied: '/dev/ttyXXX'` → `device_cgroup_rules` 的 char major 不对，按上面"检查硬件"那步重新看一遍。

**Web UI 显示"离线" / 一直转圈**
- 后端进程死了/没起。`docker ps` 看容器状态。
- 防火墙挡了 **1882**（Docker 对外端口；不是容器内的 8000）。

**"执行一次"末尾没有平滑回主臂**
- 主臂在 `return_to_follow` 期间读失败 → 进程退出重启。看日志确认。
- 如果 `fashionstar_uart_sdk` 在 playback 期间长时间不读会超时，把 `REBOT_RETURN_TIME` 调低试试，或者改代码在 playback 期间也读但丢弃数据。

---

## License

MIT。`backend/u2can/` 来自 [cmjang/DM_CAN](https://github.com/cmjang/DM_CAN)，MIT。
