"""Automated trajectory logging, post-round retrospect, and self-iteration engine.

Replaces repetitive passive health checks with concrete execution review:
1. Logs every routed Action execution (latency, backend, status, error code)
   into `_locks/trajectories.jsonl`.
2. Analyzes recent trajectories and lock audit events (`_locks/events.jsonl`)
   to identify bottlenecks, failure modes, and focus/coordinate drift.
3. Outputs structured retrospect findings and actionable next-iteration items.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .locks import lock_dir
from .models import Action, BackendResult


def trajectory_path() -> Path:
    return Path(lock_dir()) / "trajectories.jsonl"


def record_trajectory(
    action: Action,
    result: BackendResult,
    elapsed_ms: float,
) -> None:
    """Append a single action execution trajectory record atomically."""
    entry = {
        "ts": time.time(),
        "action_id": action.action_id,
        "agent_id": action.agent_id,
        "source": action.source,
        "kind": action.kind,
        "target_kind": action.target.kind,
        "pid": action.target.pid,
        "window_id": action.target.window_id,
        "delivery_mode": action.delivery_mode,
        "backend": result.backend,
        "status": result.status,
        "elapsed_ms": round(float(elapsed_ms), 2),
        "code": result.details.get("code"),
        "dispatch": result.details.get("dispatch"),
    }
    p = trajectory_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(p), os.O_CREAT | os.O_WRONLY | os.O_APPEND)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
    except OSError:
        pass


@dataclass
class RetrospectReport:
    generated_at: float
    window_Analyze_count: int
    total_actions: int
    succeeded: int
    failed: int
    blocked: int
    success_rate: float
    avg_latency_ms: float
    max_latency_ms: float
    slow_actions_count: int
    lock_takeovers: int
    failure_codes: Dict[str, int] = field(default_factory=dict)
    findings: List[str] = field(default_factory=list)
    next_iterations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def run_retrospect(limit: int = 200, slow_threshold_ms: float = 500.0) -> RetrospectReport:
    """Analyze recent action trajectories and lock events to generate a retrospect."""
    t_path = trajectory_path()
    records: List[Dict[str, Any]] = []
    if t_path.exists():
        try:
            lines = t_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-max(1, limit):]:
                if line.strip():
                    records.append(json.loads(line))
        except Exception:
            pass

    e_path = Path(lock_dir()) / "events.jsonl"
    takeovers = 0
    if e_path.exists():
        try:
            for line in e_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]:
                if '"takeover"' in line:
                    takeovers += 1
        except Exception:
            pass

    total = len(records)
    succeeded = sum(1 for r in records if r.get("status") in ("executed", "verified"))
    failed = sum(1 for r in records if r.get("status") == "failed")
    blocked = sum(1 for r in records if r.get("status") == "blocked")
    latencies = [float(r.get("elapsed_ms", 0.0)) for r in records]
    avg_lat = round(sum(latencies) / total, 2) if total else 0.0
    max_lat = round(max(latencies), 2) if latencies else 0.0
    slow_count = sum(1 for x in latencies if x >= slow_threshold_ms)

    codes: Counter[str] = Counter()
    for r in records:
        if r.get("status") in ("failed", "blocked"):
            c = str(r.get("code") or r.get("status"))
            codes[c] += 1

    findings: List[str] = []
    next_iterations: List[str] = []

    if total == 0:
        findings.append("尚无轨迹记录（trajectories.jsonl 为空），需通过实战任务产出真实操控轨迹。")
        next_iterations.append("将本机高频窗口操控任务（如 ToDesk 传输、弹窗清理）统一接入 ActionRouter 产生轨迹。")
    else:
        rate = round(succeeded / total, 4)
        if codes.get("target_unavailable", 0) > 0:
            findings.append(
                f"检测到 {codes['target_unavailable']} 次窗口句柄失效/不可用（target_unavailable），说明目标进程重启或关闭后旧 HWND 缓存失效。"
            )
            next_iterations.append("在 ActionRouter 中增加按进程名+标题自动重解析 HWND 的自愈重试。")
        if codes.get("point_outside_client", 0) > 0:
            findings.append(
                f"检测到 {codes['point_outside_client']} 次坐标越界（point_outside_client），系高 DPI 缩放或窗口还原前后尺寸变化所致。"
            )
            next_iterations.append("对所有坐标点击动作强制前置 Per-Monitor DPI 校准与客户区边界钳制（clamp）。")
        if codes.get("background_unavailable", 0) > 0:
            findings.append(
                f"检测到 {codes['background_unavailable']} 次后台投递被拒绝（background_unavailable）。"
            )
            next_iterations.append("将该类控件切换至 Win32 SendMessageW 同步消息或 UIA Pattern 直接调用，严禁盲目升前台抢焦点。")
        if slow_count > 0:
            findings.append(f"检测到 {slow_count} 个慢动作（耗时 >= {slow_threshold_ms}ms，最高 {max_lat}ms）。")
            next_iterations.append("排查慢动作对应的字符输入间距（char_pacing）或外部 CLI 子进程启动开销，优先走进程内 Win32Backend。")
        if takeovers > 0:
            findings.append(f"锁审计发现 {takeovers} 次死锁/超时抢占（takeover），存在中途异常退出的持锁进程。")
            next_iterations.append("缩短短动作的锁 TTL 并确保所有异常分支在 finally 中释放 ResourceLock。")
        if not findings:
            findings.append(f"最近 {total} 步操控全部健康（成功率 {rate*100:.1f}%，平均耗时 {avg_lat}ms，零焦点争抢）。")
            next_iterations.append("扩展下一批本机高频操控场景（如常用软件后台状态回读与无感操作模板）。")

    return RetrospectReport(
        generated_at=time.time(),
        window_Analyze_count=limit,
        total_actions=total,
        succeeded=succeeded,
        failed=failed,
        blocked=blocked,
        success_rate=round(succeeded / total, 4) if total else 1.0,
        avg_latency_ms=avg_lat,
        max_latency_ms=max_lat,
        slow_actions_count=slow_count,
        lock_takeovers=takeovers,
        failure_codes=dict(codes),
        findings=findings,
        next_iterations=next_iterations,
    )
