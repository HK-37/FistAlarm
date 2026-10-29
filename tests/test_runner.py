from __future__ import annotations

"""触发编排的单测。

用假依赖(假截图、假播放器、假时钟)验证整条链路:
到点才响、截图失败要降级、贪睡要回写、单次要自动关掉。
"""

from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from fistalarm import player
from fistalarm.capture import CaptureError
from fistalarm.models import Alarm, RepeatKind
from fistalarm.player import StrikeResult, build_strike_command
from fistalarm.runner import Runner, RunnerHooks
from fistalarm.store import AlarmStore

# 2026-10-16 是周五
FRIDAY = datetime(2026, 10, 16)


class FakeEnv:
    """一套假依赖,记录被调用了什么。"""

    def __init__(self, strike_result: StrikeResult | None = None, capture_ok: bool = True):
        self.captured: list[Path] = []
        self.played: list[tuple[str, Path | None]] = []
        self.errors: list[str] = []
        self.fired: list[Alarm] = []
        self._strike_result = strike_result or StrikeResult(0)
        self._capture_ok = capture_ok

    def capture(self, dest: Path) -> Path:
        if not self._capture_ok:
            raise CaptureError("测试：故意失败")
        self.captured.append(dest)
        return dest

    def play(self, message: str, shot: Path | None, hold_seconds=None) -> StrikeResult:
        self.played.append((message, shot))
        self.last_hold = hold_seconds
        return self._strike_result

    def hooks(self, now: datetime) -> RunnerHooks:
        return RunnerHooks(
            capture_screen=self.capture,
            play_strike=self.play,
            now=lambda: now,
            sleep=lambda _seconds: None,
            on_fired=self.fired.append,
            on_error=self.errors.append,
        )


def make_store(tmp_path, *alarms: Alarm) -> AlarmStore:
    store = AlarmStore(tmp_path / "alarms.json")
    for alarm in alarms:
        store.add(alarm)
    return store


class TestTickOnce:
    def test_fires_at_exact_time(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY, message="起床")
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        fired = runner.tick_once()

        assert fired == [alarm]
        assert len(env.played) == 1
        assert env.played[0][0] == "起床"
        assert runner.stats.fired_count == 1

    def test_does_not_fire_early(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 8, 59)), grace_seconds=120)

        assert runner.tick_once() == []
        assert env.played == []

    def test_does_not_fire_twice_in_same_minute(self, tmp_path) -> None:
        """同一分钟内 tick 多次,只响一次。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        now = datetime(2026, 10, 16, 9, 0)
        env = FakeEnv()
        runner = Runner(store, env.hooks(now), grace_seconds=5)

        runner.tick_once()
        runner.tick_once()
        runner.tick_once()

        # grace 只有 5 秒,后续 tick 时已经超出窗口
        assert runner.stats.fired_count == 1

    def test_disabled_alarm_never_fires(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY, enabled=False)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        assert runner.tick_once() == []

    def test_weekend_skipped_for_weekdays_alarm(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        # 2026-10-17 是周六
        runner = Runner(store, env.hooks(datetime(2026, 10, 17, 9, 0)), grace_seconds=120)

        assert runner.tick_once() == []
        assert env.played == []


class TestGraceCatchUp:
    def test_fires_within_grace_window(self, tmp_path) -> None:
        """程序启动晚了 1 分钟,应补响 120 秒窗口内的闹钟。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(
            store, env.hooks(datetime(2026, 10, 16, 9, 1)), grace_seconds=120
        )

        assert runner.tick_once() == [alarm]

    def test_does_not_fire_outside_grace(self, tmp_path) -> None:
        """晚了 10 分钟就不要再半夜补响早上的闹钟。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(
            store, env.hooks(datetime(2026, 10, 16, 9, 10)), grace_seconds=120
        )

        assert runner.tick_once() == []


class TestCaptureFailure:
    def test_degrades_to_black_background(self, tmp_path) -> None:
        """截图失败也要照常提醒,只是没有背景图。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY, message="起床")
        store = make_store(tmp_path, alarm)
        env = FakeEnv(capture_ok=False)
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        fired = runner.tick_once()

        assert fired == [alarm], "截图失败不该取消提醒"
        assert len(env.played) == 1
        assert env.played[0][1] is None, "没有截图时应传 None"
        assert runner.stats.capture_failures == 1
        assert any("截屏失败" in message for message in env.errors)

    def test_screenshot_path_passed_when_ok(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        runner.tick_once()

        assert len(env.captured) == 1
        assert env.played[0][1] == env.captured[0]


class TestSnooze:
    def test_snooze_sets_until(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv(strike_result=StrikeResult(2))
        now = datetime(2026, 10, 16, 9, 0)
        runner = Runner(store, env.hooks(now), grace_seconds=120)

        runner.tick_once()

        assert alarm.snooze_until == now + timedelta(minutes=5)
        assert alarm.enabled, "贪睡不该关掉闹钟"

    def test_snoozed_alarm_refires_after_delay(self, tmp_path) -> None:
        """贪睡到点后要再响一次——这是贪睡的核心价值。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)

        snooze_result = FakeEnv(strike_result=StrikeResult(2))
        runner = Runner(
            store, snooze_result.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=5
        )
        runner.tick_once()
        assert runner.stats.fired_count == 1

        # 5 分钟后:换一套正常返回的假依赖,应再触发一次
        later = datetime(2026, 10, 16, 9, 5)
        normal = FakeEnv(strike_result=StrikeResult(0))
        runner.hooks = normal.hooks(later)
        runner.tick_once()

        assert normal.played, "贪睡到点后应再次提醒"
        assert alarm.snooze_until is None, "再次提醒后贪睡状态应被清掉"

    def test_closed_clears_snooze(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        alarm.snooze_until = datetime(2026, 10, 16, 9, 5)
        store = make_store(tmp_path, alarm)
        env = FakeEnv(strike_result=StrikeResult(0))
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 5)), grace_seconds=5)

        runner.tick_once()

        assert alarm.snooze_until is None


class TestOnceAlarmLifecycle:
    def test_disabled_after_firing(self, tmp_path) -> None:
        """单次闹钟响过就该关掉,不能第二天再响。"""
        alarm = Alarm(
            time=time(9, 0), repeat=RepeatKind.ONCE, on_date=FRIDAY.date()
        )
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        runner.tick_once()

        assert alarm.enabled is False

    def test_stays_enabled_when_snoozed(self, tmp_path) -> None:
        """单次闹钟点了贪睡,还不能关——5 分钟后要再响。"""
        alarm = Alarm(
            time=time(9, 0), repeat=RepeatKind.ONCE, on_date=FRIDAY.date()
        )
        store = make_store(tmp_path, alarm)
        env = FakeEnv(strike_result=StrikeResult(2))
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        runner.tick_once()

        assert alarm.enabled is True
        assert alarm.snooze_until == datetime(2026, 10, 16, 9, 5)

    def test_periodic_alarm_not_disabled(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        runner.tick_once()

        assert alarm.enabled is True


class TestPlayerFailure:
    def test_bad_exit_code_recorded(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv(strike_result=StrikeResult(1))
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        runner.tick_once()

        assert alarm.id in runner.stats.failed_alarms
        assert any("播放器异常" in message for message in env.errors)

    def test_crash_treated_as_closed_not_snooze(self, tmp_path) -> None:
        """播放器崩溃不能等于"用户点了贪睡",否则闹钟会莫名其妙消失 5 分钟。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        env = FakeEnv(strike_result=StrikeResult(137))
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        runner.tick_once()

        assert alarm.snooze_until is None


class TestPersistence:
    def test_fired_state_is_saved(self, tmp_path) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.ONCE, on_date=FRIDAY.date())
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 9, 0)), grace_seconds=120)

        runner.tick_once()

        reloaded = AlarmStore(tmp_path / "alarms.json")
        reloaded.load()
        assert reloaded.alarms[0].enabled is False


class TestUpcoming:
    def test_lists_next_occurrences(self, tmp_path) -> None:
        morning = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        evening = Alarm(time=time(20, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, morning, evening)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 8, 0)), grace_seconds=120)

        upcoming = runner.upcoming()

        assert [alarm for alarm, _ in upcoming] == [morning, evening]

    def test_excludes_disabled(self, tmp_path) -> None:
        on = Alarm(time=time(9, 0), repeat=RepeatKind.DAILY)
        off = Alarm(time=time(10, 0), repeat=RepeatKind.DAILY, enabled=False)
        store = make_store(tmp_path, on, off)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 8, 0)), grace_seconds=120)

        assert [alarm for alarm, _ in runner.upcoming()] == [on]

    def test_expired_once_sorted_last(self, tmp_path) -> None:
        expired = Alarm(time=time(9, 0), repeat=RepeatKind.ONCE, on_date=FRIDAY.date())
        future = Alarm(time=time(10, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, expired, future)
        env = FakeEnv()
        runner = Runner(store, env.hooks(datetime(2026, 10, 16, 12, 0)), grace_seconds=120)

        upcoming = runner.upcoming()
        assert upcoming[-1][0] is expired
        assert upcoming[-1][1] is None


class TestBuildStrikeCommand:
    def test_includes_strike_flag_and_message(self) -> None:
        command = build_strike_command("起床了", None)
        assert "--strike" in command
        assert command[command.index("--strike") + 1] == "起床了"
        assert "--shot" not in command

    def test_includes_shot_path_when_given(self) -> None:
        command = build_strike_command("起床", Path("C:/tmp/shot.png"))
        assert "--shot" in command
        index = command.index("--shot")
        assert command[index + 1] == str(Path("C:/tmp/shot.png"))

    def test_empty_message_is_still_passed(self) -> None:
        """没填备注的闹钟也要能正常拉起,不能因为空字符串少个参数。"""
        command = build_strike_command("", None)
        assert command[command.index("--strike") + 1] == ""

    def test_hold_seconds_passed(self) -> None:
        command = build_strike_command("起床", None, 2.5)
        assert "--hold" in command
        assert command[command.index("--hold") + 1] == "2.5"

    def test_no_hold_flag_when_none(self) -> None:
        command = build_strike_command("起床", None, None)
        assert "--hold" not in command


class TestCommandRoundTrip:
    """拼出的命令必须能被 __main__ 的 argparse 正确解析回来。

    这一层之前漏测:管理界面点试播时,拼出的命令被 argparse 判为
    'unrecognized arguments' 而崩溃,但只测拼装发现不了——必须测往返。
    """

    @staticmethod
    def _parse(command: list[str]):
        from fistalarm.__main__ import build_parser

        # 去掉解释器前缀(python -m fistalarm 或 exe 路径),只留程序自己的参数
        idx = command.index("--strike")
        return build_parser().parse_args(command[idx:])

    def test_message_with_shot_and_hold(self) -> None:
        cmd = build_strike_command("该起来活动活动了！", Path("C:/x/shot.png"), 2.0)
        args = self._parse(cmd)
        assert args.strike == "该起来活动活动了！"
        assert args.shot == "C:/x/shot.png" or args.shot == str(Path("C:/x/shot.png"))
        assert args.hold == 2.0

    def test_message_only(self) -> None:
        args = self._parse(build_strike_command("起床", None, None))
        assert args.strike == "起床"
        assert args.shot is None
        assert args.hold is None

    def test_empty_message(self) -> None:
        args = self._parse(build_strike_command("", Path("C:/x/s.png"), 1.5))
        assert args.strike == ""
        assert args.hold == 1.5


class TestStrikeResult:
    def test_snooze_code(self) -> None:
        assert StrikeResult(player.EXIT_SNOOZE).snoozed is True

    def test_closed_code(self) -> None:
        result = StrikeResult(player.EXIT_CLOSED)
        assert result.snoozed is False
        assert result.failed is False

    def test_crash_is_failed_and_not_snooze(self) -> None:
        result = StrikeResult(137)
        assert result.failed is True
        assert result.snoozed is False


class TestBuildManageCommand:
    """管理窗口命令拼装:必须带 --manage,能被 argparse 解析。"""

    def test_has_manage_flag(self) -> None:
        from fistalarm.player import build_manage_command
        cmd = build_manage_command()
        assert "--manage" in cmd

    def test_parses_back(self) -> None:
        from fistalarm.player import build_manage_command
        from fistalarm.__main__ import build_parser
        cmd = build_manage_command()
        idx = cmd.index("--manage")
        args = build_parser().parse_args(cmd[idx:])
        assert args.manage is True
        assert args.strike is None


class TestIntervalRunner:
    """间隔闹钟在 runner 层:相邻两个边界各触发一次。"""

    def test_fires_at_each_boundary(self, tmp_path) -> None:
        from datetime import time as _time
        alarm = Alarm(
            time=_time(9, 0), repeat=RepeatKind.INTERVAL, interval_minutes=30
        )
        store = make_store(tmp_path, alarm)

        # 第一个边界 9:30
        env1 = FakeEnv()
        r = Runner(store, env1.hooks(datetime(2026, 10, 22, 9, 30)), grace_seconds=5)
        r.tick_once()
        assert len(env1.played) == 1, "9:30 应触发"

        # 同一边界再 tick,不重复
        r.tick_once(datetime(2026, 10, 22, 9, 30, 2))
        assert len(env1.played) == 1, "同一边界不重复"

        # 下一个边界 10:00,换新 env 换时间
        env2 = FakeEnv()
        r.hooks = env2.hooks(datetime(2026, 10, 22, 10, 0))
        r.tick_once(datetime(2026, 10, 22, 10, 0))
        assert len(env2.played) == 1, "10:00 应再触发"

    def test_not_fired_mid_interval(self, tmp_path) -> None:
        from datetime import time as _time
        alarm = Alarm(time=_time(9, 0), repeat=RepeatKind.INTERVAL, interval_minutes=30)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        r = Runner(store, env.hooks(datetime(2026, 10, 22, 9, 15)), grace_seconds=5)
        r.tick_once()
        assert env.played == [], "间隔中间不该响"

    def test_interval_not_disabled_after_fire(self, tmp_path) -> None:
        """间隔闹钟是周期性的,触发后不能像单次那样被关掉。"""
        from datetime import time as _time
        alarm = Alarm(time=_time(9, 0), repeat=RepeatKind.INTERVAL, interval_minutes=30)
        store = make_store(tmp_path, alarm)
        env = FakeEnv()
        r = Runner(store, env.hooks(datetime(2026, 10, 22, 9, 30)), grace_seconds=5)
        r.tick_once()
        assert alarm.enabled is True


class TestHoldPassedToPlayer:
    """防回归:自动触发必须把 store.fist_hold_seconds 传给播放器。

    之前 runner 用位置参数把 hold 传到了 run_strike 的 timeout 位,
    导致自动触发 hold=None、动画节奏不对(实锤过,测试当时是假绿)。
    """

    def test_hold_reaches_player(self, tmp_path) -> None:
        from datetime import time as _time
        alarm = Alarm(time=_time(9, 0), repeat=RepeatKind.DAILY)
        store = make_store(tmp_path, alarm)
        store.fist_hold_seconds = 2.5
        env = FakeEnv()
        r = Runner(store, env.hooks(datetime(2026, 10, 22, 9, 0)), grace_seconds=5)
        r.tick_once()
        assert env.last_hold == 2.5, "停留时长必须原样传到播放器"
