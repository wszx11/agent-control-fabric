# Agent Control Fabric (v0.4.0)

**Agents drive the computer. Humans keep the mouse.**

**中文**: 智能体负责开电脑干活，人类独享鼠标。一个零依赖、焦点安全的多智能体电脑控制底座（execution fabric）：
每次点击/按键/滚动都路由到具体窗口句柄后台投递——人类的前台、光标、键盘永远不被打扰。

A lightweight, dependency-free, focus-safe execution fabric for multi-agent
computer control on Windows. Agents plan (any visual or semantic planner — plug in yours),
ACF executes: every click, keystroke, and scroll is routed to a specific window
handle and delivered **in the background** — the human's foreground, cursor, and
keyboard are never touched.

> PyAutoGUI moves *your* mouse. ACF moves *nobody's* mouse.

## Why ACF exists

Every computer-use agent framework today fights the human for the physical
desktop: the agent clicks, your cursor jumps; the agent types, your typing
collides; the agent restores a window, it lands on top of your work.

ACF was built on a different axiom: **the desktop is shared territory, and the
human owns it.** Agents work through message-level channels (PostMessage/SendMessage,
UIA patterns) straight into target windows — windows don't need focus, don't need
to be visible, don't even need to be un-minimized. Multiple agents work
simultaneously on different windows; two agents targeting the same window are
serialized by cross-process file leases.

## Feature Matrix — what was fused from where

| Capability | Origin | What ACF kept / added |
|---|---|---|
| Coordinate-based action dialect (normalized-1000 boxes) | common in visual GUI agents | Full parser for `click(start_box='[..]')` serializations, scaled to any window's client pixels |
| String-call action DSL + alias dictionary | common in agent DSLs | AST-based parser, `left_click→click`, `input→type`, `sleep→wait`, … |
| Semantic selectors `{"name","role"}` | accessibility-tree agents | Routed straight into Windows UI Automation: `InvokePattern`, `ValuePattern`, `SelectionItemPattern`, `TogglePattern` |
| *(none of the above had this)* | **ACF** | Focus-safe Win32 backend: `PostMessageW`/`SendMessageW` with Sciter `H-SMILE-FRAME` hover handshake, per-monitor DPI aware |
| *(none of the above had this)* | **ACF** | Cross-process `ResourceLock` (TOCTOU-hardened, dead-owner takeover) |
| *(none of the above had this)* | **ACF** | Offscreen `PrintWindow(PW_RENDERFULLCONTENT)` capture + pure-stdlib PNG encoder |
| *(none of the above had this)* | **ACF** | `CreateDesktop` virtual-desktop isolation: agent windows live on a separate desktop object, invisible to the human |
| *(none of the above had this)* | **ACF** | Automated trajectory retrospect: 500+ action logs → bottleneck findings → next-iteration backlog |

## What you get

**中文（功能一览）**:
- **三方言统一**: 坐标框 / 调用串别名 / 语义选择器 → 一套动作协议，接哪个规划器都行
- **多智能体·多"鼠标"**: 每个智能体的输入走自己的消息通道直达窗口，物理鼠键始终在人类手里
- **窗口永不跳前台**: SW_SHOWNOACTIVATE 恢复 / SWP_NOACTIVATE 边条停靠 / 遮挡窗口离屏抓图
- **真·虚拟桌面隔离**: agent 窗口建在独立 Windows 桌面对象上，人类桌面看不见它们
- **自我改进**: 全量动作轨迹落盘，`acf retrospect` 自动产出瓶颈发现与下一轮迭代清单


- **Multi-agent, multi-"mouse"**: every agent's click/keystone travels through its
  own message channel directly to the target window — N agents work as if each
  had a private mouse & keyboard, while the physical ones stay with the human.
- **Windows that never jump**: restore via `SW_SHOWNOACTIVATE`, park to screen
  strips via `SWP_NOACTIVATE`, capture occluded/background windows offscreen.
- **True isolation (optional)**: `virtual_desktop.py` creates a Windows desktop
  object where agent windows are created, enumerated, and captured — the human's
  `Default` desktop never shows them.
- **Self-improving**: every action is logged atomically; `acf retrospect` turns
  the log into concrete findings and a next-iteration backlog.

## Install

```bash
pip install -e .          # zero runtime dependencies, Python >= 3.11, Windows for live control
```

## 30-second demo

**中文（30 秒上手）**: `router.execute(act)` 后台执行点击——你的光标纹丝不动；
`VirtualDesktop` 里跑的是 agent 自己的桌面世界。两个 agent 操作两个窗口并发执行，
同窗口则经跨进程锁排队。

```python
from agent_control_fabric import Action, Target, ActionRouter, Win32Backend
from agent_control_fabric.virtual_desktop import VirtualDesktop, run_on_virtual_desktop

router = ActionRouter(Win32Backend())
act = Action(
    action_id="demo-1", agent_id="agent-a", source="semantic", kind="click",
    target=Target(kind="window", pid=1234, window_id=5678),
    parameters={"x": 40, "y": 12},
    delivery_mode="background",      # foreground is rejected by default
)
print(router.execute(act).status)    # executed — your cursor never moved

# Or fully isolate agent windows on a separate desktop:
with VirtualDesktop("acf-agents") as vd:
    run_on_virtual_desktop(vd, lambda: print("agent world runs here"))
```

Two agents on two windows run concurrently; two agents on one window queue up
through `ResourceLock` — see `examples/two-agents.json`, run with
`acf run examples/two-agents.json --dry-run`.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v     # 27 tests, zero deps
PYTHONPATH=src python -m agent_control_fabric pc list-windows
PYTHONPATH=src python -m agent_control_fabric cloudpc status
PYTHONPATH=src python -m agent_control_fabric retrospect
```

## Safety Model

**中文（安全模型）**: foreground 投递默认全后端拒绝；窗口恢复用 SW_SHOWNOACTIVATE，永不用
SetForegroundWindow；UserInputGuard 校验前台与光标零位移；人类编辑中的文档窗口（Notepad/Word…）
一律禁止注入按键与点击；虚拟桌面只切线程亲和，绝不切走人类的输入桌面。

- `delivery_mode="foreground"` is **rejected by default** in every backend.
- Window restore uses `SW_SHOWNOACTIVATE` — **never** `SetForegroundWindow`.
- `UserInputGuard` snapshots foreground window + cursor position and verifies
  both unchanged after every guarded block.
- Sanitized bundle staging refuses `*password*`, `*.key`, `*.pem`, `*.env`.
- Virtual-desktop mode: only thread affinity is switched — the input desktop
  (the human's) is never switched away.

## Status

Battle-tested in daily multi-agent desktop operation (500+ recorded actions,
98% success rate). Windows-first: UIA/Win32 backends need Windows; other modules
are pure stdlib and cross-platform. CI on Ubuntu + Windows.

## License

MIT — see [LICENSE](LICENSE). Third-party notes in [THIRD_PARTY.md](THIRD_PARTY.md).
