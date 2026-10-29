from __future__ import annotations

"""调度核心的单测。

重点覆盖容易错的边界:跨日、跨周、月末、闰日、贪睡叠加、单次过期。
测试全部用显式构造的时间,不等真实时钟。
"""

from datetime import date, datetime, time, timedelta

import pytest

from fistalarm.models import Alarm, RepeatKind, parse_hhmm
from fistalarm.schedule import (
    clear_snooze,
    is_due,
    is_expired,
    next_occurrence,
    previous_occurrence,
    snooze,
)

# 2026-10-16 是周五,10-17 周六,10-18 周日,10-19 周一
FRIDAY = datetime(2026, 10, 16, 8, 0, 0)
SATURDAY = datetime(2026, 10, 17, 8, 0, 0)
MONDAY = datetime(2026, 10, 19, 8, 0, 0)


def daily(hh: int, mm: int) -> Alarm:
    return Alarm(time=time(hh, mm), repeat=RepeatKind.DAILY)


class TestParseHHMM:
    def test_normal(self) -> None:
        assert parse_hhmm("09:30") == time(9, 30)

    def test_single_digit_hour(self) -> None:
        assert parse_hhmm("9:05") == time(9, 5)

    def test_midnight_and_last_minute(self) -> None:
        assert parse_hhmm("00:00") == time(0, 0)
        assert parse_hhmm("23:59") == time(23, 59)

    @pytest.mark.parametrize("bad", ["25:00", "12:60", "1230", "12:", "ab:cd", ""])
    def test_invalid(self, bad: str) -> None:
        with pytest.raises(ValueError):
            parse_hhmm(bad)


class TestAlarmValidation:
    def test_once_requires_date(self) -> None:
        with pytest.raises(ValueError, match="单次"):
            Alarm(time=time(9, 0), repeat=RepeatKind.ONCE)

    def test_custom_requires_weekdays(self) -> None:
        with pytest.raises(ValueError, match="自选星期"):
            Alarm(time=time(9, 0), repeat=RepeatKind.CUSTOM, weekdays=[])

    def test_custom_weekdays_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="0-6"):
            Alarm(time=time(9, 0), repeat=RepeatKind.CUSTOM, weekdays=[7])

    def test_custom_weekdays_deduped_and_sorted(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.CUSTOM, weekdays=[4, 0, 4])
        assert alarm.weekdays == [0, 4]


class TestNextOccurrenceDaily:
    def test_later_today(self) -> None:
        assert next_occurrence(daily(9, 0), FRIDAY) == datetime(2026, 10, 16, 9, 0)

    def test_earlier_today_rolls_to_tomorrow(self) -> None:
        assert next_occurrence(daily(7, 0), FRIDAY) == datetime(2026, 10, 17, 7, 0)

    def test_exact_time_is_not_repeated(self) -> None:
        """正好等于当前时刻不算"下次"——否则会原地重复触发。"""
        at_nine = datetime(2026, 10, 16, 9, 0)
        assert next_occurrence(daily(9, 0), at_nine) == datetime(2026, 10, 17, 9, 0)

    def test_month_end_rolls_over(self) -> None:
        oct31 = datetime(2026, 10, 31, 23, 0)
        assert next_occurrence(daily(1, 0), oct31) == datetime(2026, 11, 1, 1, 0)

    def test_year_end_rolls_over(self) -> None:
        dec31 = datetime(2026, 12, 31, 23, 30)
        assert next_occurrence(daily(0, 15), dec31) == datetime(2027, 1, 1, 0, 15)


class TestNextOccurrenceWeekdays:
    def test_from_friday_morning_to_same_day(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS)
        assert next_occurrence(alarm, FRIDAY) == datetime(2026, 10, 16, 9, 0)

    def test_friday_evening_skips_weekend_to_monday(self) -> None:
        """周五晚上设定的闹钟,下一个应该跳到周一,不能落在周六。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS)
        friday_night = datetime(2026, 10, 16, 22, 0)
        assert next_occurrence(alarm, friday_night) == datetime(2026, 10, 19, 9, 0)

    def test_saturday_goes_to_monday(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS)
        assert next_occurrence(alarm, SATURDAY) == datetime(2026, 10, 19, 9, 0)

    def test_sunday_goes_to_monday(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS)
        sunday = datetime(2026, 10, 18, 8, 0)
        assert next_occurrence(alarm, sunday) == datetime(2026, 10, 19, 9, 0)


class TestNextOccurrenceCustom:
    def test_only_configured_day(self) -> None:
        """只在周三响:周五问,应跳到下周三。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.CUSTOM, weekdays=[2])
        assert next_occurrence(alarm, FRIDAY) == datetime(2026, 10, 21, 9, 0)

    def test_weekend_only(self) -> None:
        alarm = Alarm(time=time(10, 0), repeat=RepeatKind.CUSTOM, weekdays=[5, 6])
        assert next_occurrence(alarm, FRIDAY) == datetime(2026, 10, 17, 10, 0)

    def test_every_day_via_custom(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.CUSTOM, weekdays=list(range(7)))
        assert next_occurrence(alarm, FRIDAY) == datetime(2026, 10, 16, 9, 0)


class TestNextOccurrenceOnce:
    def test_future_date(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 20))
        assert next_occurrence(alarm, FRIDAY) == datetime(2026, 10, 20, 9, 0)

    def test_same_day_later(self) -> None:
        alarm = Alarm(time=time(18, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 16))
        assert next_occurrence(alarm, FRIDAY) == datetime(2026, 10, 16, 18, 0)

    def test_same_day_already_passed_returns_none(self) -> None:
        alarm = Alarm(time=time(7, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 16))
        assert next_occurrence(alarm, FRIDAY) is None

    def test_past_date_returns_none(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 1))
        assert next_occurrence(alarm, FRIDAY) is None


class TestPreviousOccurrence:
    def test_daily_just_fired(self) -> None:
        at_nine = datetime(2026, 10, 16, 9, 0, 3)
        assert previous_occurrence(daily(9, 0), at_nine) == datetime(2026, 10, 16, 9, 0)

    def test_daily_before_first_fire_today_uses_yesterday(self) -> None:
        morning = datetime(2026, 10, 16, 7, 0)
        assert previous_occurrence(daily(9, 0), morning) == datetime(2026, 10, 15, 9, 0)

    def test_weekdays_monday_morning_uses_friday(self) -> None:
        """周一早上 7 点,上一次工作日闹钟应该是上周五 9 点,不是周日。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS)
        monday_morning = datetime(2026, 10, 19, 7, 0)
        assert previous_occurrence(alarm, monday_morning) == datetime(2026, 10, 16, 9, 0)

    def test_once_never_fired(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 20))
        assert previous_occurrence(alarm, FRIDAY) is None


class TestIsDue:
    def test_right_at_time(self) -> None:
        assert is_due(daily(9, 0), datetime(2026, 10, 16, 9, 0), grace=timedelta(seconds=5))

    def test_slightly_late_within_grace(self) -> None:
        assert is_due(daily(9, 0), datetime(2026, 10, 16, 9, 0, 3), grace=timedelta(seconds=5))

    def test_too_late_outside_grace(self) -> None:
        assert not is_due(daily(9, 0), datetime(2026, 10, 16, 9, 0, 30), grace=timedelta(seconds=5))

    def test_disabled_never_due(self) -> None:
        alarm = daily(9, 0)
        alarm.enabled = False
        assert not is_due(alarm, datetime(2026, 10, 16, 9, 0), grace=timedelta(seconds=5))

    def test_wrong_day_not_due(self) -> None:
        assert not is_due(daily(9, 0), datetime(2026, 10, 16, 9, 30), grace=timedelta(seconds=5))

    def test_weekdays_alarm_not_due_on_saturday(self) -> None:
        """工作日闹钟周六不该响——这是最容易出错的一条。"""
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS)
        assert not is_due(alarm, datetime(2026, 10, 17, 9, 0), grace=timedelta(seconds=5))


class TestSnooze:
    def test_snooze_overrides_rule(self) -> None:
        alarm = daily(9, 0)
        now = datetime(2026, 10, 16, 9, 0)
        snooze(alarm, now, minutes=5)
        assert next_occurrence(alarm, now) == datetime(2026, 10, 16, 9, 5)

    def test_snooze_does_not_fire_before_until(self) -> None:
        alarm = daily(9, 0)
        now = datetime(2026, 10, 16, 9, 0)
        snooze(alarm, now, minutes=5)
        assert not is_due(alarm, datetime(2026, 10, 16, 9, 2), grace=timedelta(seconds=5))

    def test_snooze_fires_at_until(self) -> None:
        alarm = daily(9, 0)
        now = datetime(2026, 10, 16, 9, 0)
        snooze(alarm, now, minutes=5)
        assert is_due(alarm, datetime(2026, 10, 16, 9, 5), grace=timedelta(seconds=5))

    def test_snooze_stacked_twice(self) -> None:
        """连续贪睡两次应各自基于当时的时间往后推。"""
        alarm = daily(9, 0)
        t0 = datetime(2026, 10, 16, 9, 0)
        snooze(alarm, t0, minutes=5)
        t1 = datetime(2026, 10, 16, 9, 5)
        snooze(alarm, t1, minutes=5)
        assert next_occurrence(alarm, t1) == datetime(2026, 10, 16, 9, 10)

    def test_clear_snooze_returns_to_rule(self) -> None:
        alarm = daily(9, 0)
        now = datetime(2026, 10, 16, 9, 0)
        snooze(alarm, now, minutes=5)
        clear_snooze(alarm)
        assert next_occurrence(alarm, now) == datetime(2026, 10, 17, 9, 0)

    def test_expired_snooze_is_ignored(self) -> None:
        """贪睡时刻已经过去,应回到正常规则,不能被旧值卡住。"""
        alarm = daily(9, 0)
        alarm.snooze_until = datetime(2026, 10, 16, 9, 5)
        later = datetime(2026, 10, 16, 10, 0)
        assert next_occurrence(alarm, later) == datetime(2026, 10, 17, 9, 0)

    def test_negative_minutes_rejected(self) -> None:
        with pytest.raises(ValueError):
            snooze(daily(9, 0), FRIDAY, minutes=0)


class TestIsExpired:
    def test_once_past(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 15))
        assert is_expired(alarm, FRIDAY)

    def test_once_future(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 20))
        assert not is_expired(alarm, FRIDAY)

    def test_once_snoozed_is_not_expired(self) -> None:
        """单次的闹钟响了、用户点贪睡,在贪睡到点前不能算过期。"""
        alarm = Alarm(
            time=time(9, 0), repeat=RepeatKind.ONCE, on_date=date(2026, 10, 16)
        )
        alarm.snooze_until = datetime(2026, 10, 16, 9, 5)
        assert not is_expired(alarm, datetime(2026, 10, 16, 9, 1))

    def test_repeating_never_expires(self) -> None:
        assert not is_expired(daily(9, 0), datetime(2030, 1, 1))


class TestSerialization:
    def test_round_trip_daily(self) -> None:
        alarm = daily(9, 30)
        alarm.message = "起床"
        restored = Alarm.from_dict(alarm.to_dict())
        assert restored.id == alarm.id
        assert restored.time == alarm.time
        assert restored.repeat == alarm.repeat
        assert restored.message == "起床"

    def test_round_trip_once(self) -> None:
        alarm = Alarm(
            time=time(9, 0),
            repeat=RepeatKind.ONCE,
            on_date=date(2026, 10, 20),
            message="开会",
        )
        restored = Alarm.from_dict(alarm.to_dict())
        assert restored.on_date == date(2026, 10, 20)
        assert restored.repeat is RepeatKind.ONCE

    def test_round_trip_custom_weekdays(self) -> None:
        alarm = Alarm(time=time(9, 0), repeat=RepeatKind.CUSTOM, weekdays=[0, 2, 4])
        restored = Alarm.from_dict(alarm.to_dict())
        assert restored.weekdays == [0, 2, 4]

    def test_round_trip_snooze(self) -> None:
        alarm = daily(9, 0)
        alarm.snooze_until = datetime(2026, 10, 16, 9, 5)
        restored = Alarm.from_dict(alarm.to_dict())
        assert restored.snooze_until == datetime(2026, 10, 16, 9, 5)

    def test_repeat_label(self) -> None:
        assert daily(9, 0).repeat_label() == "每天"
        assert Alarm(time=time(9, 0), repeat=RepeatKind.WEEKDAYS).repeat_label() == "工作日"
        custom = Alarm(time=time(9, 0), repeat=RepeatKind.CUSTOM, weekdays=[0, 2])
        assert custom.repeat_label() == "周一、周三"


def interval(start_h, start_m, minutes, end=None):
    """便捷构造间隔闹钟。"""
    return Alarm(
        time=time(start_h, start_m),
        repeat=RepeatKind.INTERVAL,
        interval_minutes=minutes,
        active_end=end,
    )


class TestIntervalValidation:
    def test_requires_positive_interval(self) -> None:
        with pytest.raises(ValueError, match="频率"):
            Alarm(time=time(9, 0), repeat=RepeatKind.INTERVAL)

    def test_rejects_zero_interval(self) -> None:
        with pytest.raises(ValueError, match="频率"):
            Alarm(time=time(9, 0), repeat=RepeatKind.INTERVAL, interval_minutes=0)

    def test_end_must_be_after_start(self) -> None:
        with pytest.raises(ValueError, match="结束时间"):
            Alarm(time=time(10, 0), repeat=RepeatKind.INTERVAL,
                  interval_minutes=30, active_end=time(9, 0))


class TestIntervalNext:
    def test_before_start_returns_start(self) -> None:
        a = interval(9, 0, 30)
        assert next_occurrence(a, datetime(2026, 10, 22, 8, 30)) == datetime(2026, 10, 22, 9, 0)

    def test_at_start_returns_next_boundary(self) -> None:
        """正好在开始时刻,返回下一个边界(不原地重复)。"""
        a = interval(9, 0, 30)
        assert next_occurrence(a, datetime(2026, 10, 22, 9, 0)) == datetime(2026, 10, 22, 9, 30)

    def test_mid_interval(self) -> None:
        a = interval(9, 0, 30)
        assert next_occurrence(a, datetime(2026, 10, 22, 9, 15)) == datetime(2026, 10, 22, 9, 30)

    def test_crosses_hour(self) -> None:
        a = interval(9, 0, 30)
        assert next_occurrence(a, datetime(2026, 10, 22, 9, 45)) == datetime(2026, 10, 22, 10, 0)

    def test_crosses_midnight_back_to_start(self) -> None:
        """没设结束时间:当天延伸到午夜,次日回开始时间。"""
        a = interval(9, 0, 30)
        assert next_occurrence(a, datetime(2026, 10, 22, 23, 40)) == datetime(2026, 10, 23, 9, 0)

    def test_odd_interval(self) -> None:
        """自填奇数间隔(7 分钟)也要算对。"""
        a = interval(9, 0, 7)
        assert next_occurrence(a, datetime(2026, 10, 22, 9, 10)) == datetime(2026, 10, 22, 9, 14)

    def test_with_end_within_window(self) -> None:
        a = interval(9, 0, 30, end=time(10, 0))
        assert next_occurrence(a, datetime(2026, 10, 22, 9, 45)) == datetime(2026, 10, 22, 10, 0)

    def test_with_end_past_window_jumps_next_day(self) -> None:
        """设了结束时间,当天到点停,次日从开始时间再来。"""
        a = interval(9, 0, 30, end=time(10, 0))
        assert next_occurrence(a, datetime(2026, 10, 22, 10, 0)) == datetime(2026, 10, 23, 9, 0)

    def test_before_start_with_end(self) -> None:
        a = interval(9, 0, 30, end=time(10, 0))
        assert next_occurrence(a, datetime(2026, 10, 22, 8, 0)) == datetime(2026, 10, 22, 9, 0)


class TestIntervalPrevious:
    def test_before_start_returns_yesterday_last(self) -> None:
        """当天还没到开始时间,上一次是昨天最后一个边界。"""
        a = interval(9, 0, 30)
        # 昨天 9:00 起每 30 分,到 23:30 是最后一个(23:59:59 内)
        prev = previous_occurrence(a, datetime(2026, 10, 22, 8, 30))
        assert prev == datetime(2026, 10, 21, 23, 30)

    def test_at_boundary(self) -> None:
        a = interval(9, 0, 30)
        assert previous_occurrence(a, datetime(2026, 10, 22, 9, 30)) == datetime(2026, 10, 22, 9, 30)

    def test_mid_interval(self) -> None:
        a = interval(9, 0, 30)
        assert previous_occurrence(a, datetime(2026, 10, 22, 9, 29)) == datetime(2026, 10, 22, 9, 0)

    def test_with_end_after_window_uses_last_in_window(self) -> None:
        """过了结束时间,上一次是窗口内最后一个边界。"""
        a = interval(9, 0, 30, end=time(10, 0))
        assert previous_occurrence(a, datetime(2026, 10, 22, 14, 0)) == datetime(2026, 10, 22, 10, 0)


class TestIntervalIsDue:
    def test_due_at_boundary(self) -> None:
        a = interval(9, 0, 30)
        assert is_due(a, datetime(2026, 10, 22, 9, 30), grace=timedelta(seconds=5))

    def test_due_within_grace(self) -> None:
        a = interval(9, 0, 30)
        assert is_due(a, datetime(2026, 10, 22, 9, 30, 3), grace=timedelta(seconds=5))

    def test_not_due_mid_interval(self) -> None:
        a = interval(9, 0, 30)
        assert not is_due(a, datetime(2026, 10, 22, 9, 15), grace=timedelta(seconds=5))

    def test_not_due_outside_window(self) -> None:
        """设了结束时间,窗口外不该响(即使正好踩在间隔网格上)。"""
        a = interval(9, 0, 30, end=time(10, 0))
        # 10:30 在网格上但超出 10:00 窗口
        assert not is_due(a, datetime(2026, 10, 22, 10, 30), grace=timedelta(seconds=5))


class TestIntervalMisc:
    def test_never_expires(self) -> None:
        a = interval(9, 0, 30)
        assert not is_expired(a, datetime(2030, 1, 1))

    def test_snooze_overrides(self) -> None:
        a = interval(9, 0, 30)
        now = datetime(2026, 10, 22, 9, 30)
        snooze(a, now, minutes=5)
        assert next_occurrence(a, now) == datetime(2026, 10, 22, 9, 35)

    def test_round_trip(self) -> None:
        a = interval(9, 0, 30, end=time(18, 0))
        a.message = "起来动动"
        restored = Alarm.from_dict(a.to_dict())
        assert restored.repeat is RepeatKind.INTERVAL
        assert restored.interval_minutes == 30
        assert restored.active_end == time(18, 0)
        assert restored.time == time(9, 0)

    def test_round_trip_no_end(self) -> None:
        a = interval(9, 0, 15)
        restored = Alarm.from_dict(a.to_dict())
        assert restored.interval_minutes == 15
        assert restored.active_end is None

    def test_label_with_end(self) -> None:
        assert interval(9, 0, 30, end=time(18, 0)).repeat_label() == "09:00~18:00 每30分钟"

    def test_label_no_end(self) -> None:
        assert interval(9, 0, 30).repeat_label() == "09:00 起每30分钟"
