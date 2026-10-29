"""触发流程编排 —— 程序的大脑。

把「什么时候响」「响了做什么」串起来:

    每秒对表 → 发现到点 → 截屏 → 拉起播放器 → 按返回码处理贪睡

设计上的两个关键决定:

1. **对表,不用 sleep 计时**。每秒拿系统时钟比较。这样电脑睡眠/休眠唤醒后
   不会因为计时器停摆而漏掉闹钟(睡 8 小时醒来,sleep 方案会少走 8 小时)。

2. **依赖可注入**。这个类不直接调用 mss / subprocess,而是接受函数。
   测试时传假的,就能不开窗口、不截图地验证整条编排逻辑。
"""
from __future__ import annotations

import time as time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import capture, player
from .models import Alarm, RepeatKind
from .schedule import (
    clear_snooze,
    is_due,
    next_occurrence,
    previous_occurrence,
    snooze,
)
from .store import AlarmStore

# 程序启动/唤醒时,允许补响多久之内错过的闹钟
DEFAULT_GRACE_SECONDS = 120

# 对表间隔
TICK_SECONDS = 1.0


@dataclass
class RunnerHooks:
    """可注入的副作用。默认真实实现,测试传假实现。"""

    capture_screen: Callable[[Path], Path] = capture.capture_primary_screen
    play_strike: Callable[..., player.StrikeResult] = player.run_strike
    now: Callable[[], datetime] = datetime.now
    sleep: Callable[[float], None] = time_module.sleep
    # 每次触发后回调,供界面刷新;参数是刚触发的闹钟
    on_fired: Callable[[Alarm], None] | None = None
    # 出问题时回调,供日志/界面提示
    on_error: Callable[[str], None] | None = None


@dataclass
class RunnerStats:
    """运行统计,便于排查"闹钟到底有没有被检查"。"""

    ticks: int = 0
    fired_count: int = 0
    capture_failures: int = 0
    failed_alarms: list[str] = field(default_factory=list)


class Runner:
    """调度循环。

    用法:构造后反复调 tick_once(),或调 run_forever() 一直跑。
    tick 与 run_forever 分开是为了测试能精确控制推进。
    """

    def __init__(
        self,
        store: AlarmStore,
        hooks: RunnerHooks | None = None,
        grace_seconds: int = DEFAULT_GRACE_SECONDS,
    ) -> None:
        self.store = store
        self.hooks = hooks or RunnerHooks()
        self.grace = timedelta(seconds=grace_seconds)
        self.stats = RunnerStats()
        self._running = False
        # 每条闹钟最近一次已处理的触发时刻。用来防止"同一个触发点响两次"——
        # 光靠 grace 窗口不够:用户秒关窗口时,窗口还没过去,tick 会再判一次 due。
        self._handled: dict[str, datetime] = {}

    # ---------- 单次推进 ----------

    def tick_once(self, now: datetime | None = None) -> list[Alarm]:
        """检查一遍所有闹钟,返回本次触发的那些。

        测试直接调它,不用等真实时间。
        """
        moment = now or self.hooks.now()
        self.stats.ticks += 1
        fired: list[Alarm] = []

        for alarm in list(self.store.alarms):
            if not is_due(alarm, moment, grace=self.grace):
                continue

            # 同一个触发点只处理一次。用 previous_occurrence 认出"这次到底是哪个点",
            # 已经处理过就跳过;换了触发点(如贪睡到点、下一天)则放行。
            trigger_point = previous_occurrence(alarm, moment)
            if trigger_point is not None and self._handled.get(alarm.id) == trigger_point:
                continue
            if trigger_point is not None:
                self._handled[alarm.id] = trigger_point

            fired.append(alarm)
            self._fire(alarm, moment)

        return fired

    def _fire(self, alarm: Alarm, now: datetime) -> None:
        """触发一条闹钟:截屏 → 播放器 → 处理结果。"""
        self.stats.fired_count += 1

        shot: Path | None = None
        try:
            shot = self.hooks.capture_screen(self._shot_path(alarm, now))
        except capture.CaptureError as exc:
            # 截图失败不该取消提醒——降级成纯黑背景继续播
            self.stats.capture_failures += 1
            self._error(f"截屏失败,将用纯黑背景播放：{exc}")
            shot = None

        # hold 必须用关键字传:run_strike 的签名是 (message, shot, timeout, hold_seconds),
        # 第三个位置参数是 timeout 不是 hold。之前用位置传把停留时长喂给了 timeout,
        # 导致自动触发 hold=None、动画节奏和试播不一致(实锤过)。
        result = self.hooks.play_strike(
            self.store.message_for(alarm), shot, hold_seconds=self.store.fist_hold_seconds
        )

        if result.failed:
            self.stats.failed_alarms.append(alarm.id)
            self._error(f"播放器异常退出(返回码 {result.returncode})")

        if result.snoozed:
            snooze(alarm, now, minutes=result.snooze_minutes)
        else:
            clear_snooze(alarm)
            self._advance_once(alarm)

        self.store.save()
        if self.hooks.on_fired is not None:
            self.hooks.on_fired(alarm)

    def _advance_once(self, alarm: Alarm) -> None:
        """单次闹钟响过就关掉,避免第二天又响。"""
        if alarm.repeat is RepeatKind.ONCE:
            alarm.enabled = False

    def _shot_path(self, alarm: Alarm, now: datetime) -> Path:
        return self.store.path.parent / "shots" / f"{now:%Y%m%d-%H%M%S}-{alarm.id}.png"

    def _error(self, message: str) -> None:
        if self.hooks.on_error is not None:
            self.hooks.on_error(message)

    # ---------- 常驻循环 ----------

    def run_forever(self) -> None:
        """一直跑,直到 stop()。托盘子进程用这个。"""
        self._running = True
        while self._running:
            try:
                self.tick_once()
            except Exception as exc:  # 单次 tick 出错不该让整个循环死掉
                self._error(f"调度循环出错：{exc}")
            self.hooks.sleep(TICK_SECONDS)

    def stop(self) -> None:
        self._running = False

    # ---------- 查询 ----------

    def upcoming(self, limit: int = 5) -> list[tuple[Alarm, datetime | None]]:
        """列出接下来要响的几条,供托盘菜单/界面展示。"""
        moment = self.hooks.now()
        pairs = [
            (alarm, next_occurrence(alarm, moment))
            for alarm in self.store.enabled_alarms()
        ]
        # 不会再响的(单次已过期)排最后
        pairs.sort(key=lambda pair: (pair[1] is None, pair[1] or moment))
        return pairs[:limit]
