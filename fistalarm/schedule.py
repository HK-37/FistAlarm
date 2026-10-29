"""下一次什么时候响 —— 纯函数,不碰文件、不碰 UI、不碰时钟。

这个模块是整个程序最容易出错的地方(跨日、跨周、月末、贪睡叠加),
所以刻意做成纯函数:输入"当前时刻",输出"下次触发的时刻",
调用方负责提供时间。这样测试不需要等真实时间流过。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from .models import Alarm, RepeatKind

# 一次触发后,后续重新计算时往前看的最小步长
_ONE_DAY = timedelta(days=1)

# 自选星期时最多向后找多少天。7 天足够覆盖任何配置,
# 留 8 是防御性写法(理论上第 8 天必然回到同一个星期几)。
_LOOKAHEAD_DAYS = 8


def next_occurrence(alarm: Alarm, after: datetime) -> datetime | None:
    """求该闹钟严格晚于 ``after`` 的下一次触发时刻。

    返回 None 表示此后不会再响——目前只有"单次"闹钟过了那一天会这样。

    贪睡优先:若设置了 snooze_until 且它晚于 after,直接以它为准,
    不管重复规则(正在贪睡中,不该按原规则算)。
    """
    if alarm.snooze_until is not None and alarm.snooze_until > after:
        return alarm.snooze_until

    if alarm.repeat is RepeatKind.ONCE:
        if alarm.on_date is None:  # 构造函数已挡,这里防御
            return None
        moment = datetime.combine(alarm.on_date, alarm.time)
        return moment if moment > after else None

    if alarm.repeat is RepeatKind.INTERVAL:
        return _interval_next(alarm, after)

    # 以下都是周期性规则:从 after 所在的那天往后找,取第一个"该响"的时刻。
    candidate = datetime.combine(after.date(), alarm.time)
    if candidate <= after:
        candidate += _ONE_DAY

    if alarm.repeat is RepeatKind.DAILY:
        return candidate

    if alarm.repeat is RepeatKind.WEEKDAYS:
        # 周一=0 … 周五=4,周六周日跳过
        while candidate.weekday() >= 5:
            candidate += _ONE_DAY
        return candidate

    if alarm.repeat is RepeatKind.CUSTOM:
        for _ in range(_LOOKAHEAD_DAYS):
            if candidate.weekday() in alarm.weekdays:
                return candidate
            candidate += _ONE_DAY
        # 走不到这里:weekdays 非空时 7 天内必有命中
        return None

    raise ValueError(f"未知的重复规则：{alarm.repeat!r}")


def is_due(alarm: Alarm, now: datetime, grace: timedelta = timedelta(seconds=0)) -> bool:
    """判断这条闹钟此刻是否该响。

    ``grace`` 是容忍窗口:程序启动或系统唤醒时,允许补响刚刚过去不久的那次。
    默认 0 表示只认准点及之后。

    只有启用中、且没在贪睡中的闹钟才可能 due。
    """
    if not alarm.enabled:
        return False
    if alarm.snooze_until is not None and now < alarm.snooze_until:
        # 还在贪睡窗口内,不响
        return False

    # 从"now - grace"往回看一步:若上一次触发时刻落在窗口内,就算到点。
    previous = previous_occurrence(alarm, now)
    if previous is None:
        return False
    return now - previous <= grace


def previous_occurrence(alarm: Alarm, before: datetime) -> datetime | None:
    """求不晚于 ``before`` 的最近一次触发时刻;没有则 None。

    实现是**显式向后扫描**:从 before 所在那天算起,一天天往回找第一个
    符合规则的时刻。不用"倒推下一次"的取巧写法——那种写法在触发时刻
    紧贴 before 时会把候选滚到第二天,直接漏掉(测试抓到过)。
    """
    if alarm.repeat is RepeatKind.ONCE:
        if alarm.on_date is None:
            return None
        moment = datetime.combine(alarm.on_date, alarm.time)
        if moment > before:
            return None
        # 贪睡可能把触发点推到更晚
        if alarm.snooze_until is not None and before >= alarm.snooze_until > moment:
            return alarm.snooze_until
        return moment

    if alarm.repeat is RepeatKind.INTERVAL:
        rule_moment = _interval_prev(alarm, before)
        if alarm.snooze_until is not None and is_expired_snooze(alarm, before):
            if rule_moment is None or alarm.snooze_until > rule_moment:
                return alarm.snooze_until
        return rule_moment

    # 周期性规则:先取 before 当天,晚于 before 就退一天
    candidate = datetime.combine(before.date(), alarm.time)
    if candidate > before:
        candidate -= _ONE_DAY

    if alarm.repeat is RepeatKind.DAILY:
        rule_moment: datetime | None = candidate
    elif alarm.repeat is RepeatKind.WEEKDAYS:
        while candidate.weekday() >= 5:
            candidate -= _ONE_DAY
        rule_moment = candidate
    elif alarm.repeat is RepeatKind.CUSTOM:
        for _ in range(_LOOKAHEAD_DAYS):
            if candidate.weekday() in alarm.weekdays:
                break
            candidate -= _ONE_DAY
        else:
            return None
        rule_moment = candidate
    else:
        raise ValueError(f"未知的重复规则：{alarm.repeat!r}")

    # 贪睡点是"最近一次该响的时刻"里更晚的那个
    if alarm.snooze_until is not None and is_expired_snooze(alarm, before):
        if rule_moment is None or alarm.snooze_until > rule_moment:
            return alarm.snooze_until
    return rule_moment


def is_expired_snooze(alarm: Alarm, now: datetime) -> bool:
    """贪睡时刻是否已经到点(或已过)。用于判断它还算不算"最近一次触发"。"""
    return alarm.snooze_until is not None and alarm.snooze_until <= now


def snooze(alarm: Alarm, now: datetime, minutes: int = 5) -> datetime:
    """把闹钟推迟 ``minutes`` 分钟,返回新的触发时刻。

    贪睡是"一次性"的延后,不改动重复规则本身。
    """
    if minutes <= 0:
        raise ValueError("贪睡分钟数必须为正")
    alarm.snooze_until = now + timedelta(minutes=minutes)
    return alarm.snooze_until


def clear_snooze(alarm: Alarm) -> None:
    """清掉贪睡状态(触发之后调用)。"""
    alarm.snooze_until = None


# ----------------------------- 间隔模式 -----------------------------
#
# 触发点 = 开始时间(alarm.time)起,每 interval_minutes 一个:
#   start, start+N, start+2N, …
# 可选 active_end 作为当天上限(含);没设就到当天 23:59:59,次日从 start 再来。
# 用数学算第 k 个边界,不用逐分钟循环。


def _day_start(alarm: Alarm, day: datetime) -> datetime:
    return datetime.combine(day.date(), alarm.time)


def _day_end(alarm: Alarm, day: datetime) -> datetime:
    """当天最后一个允许的时刻(含)。没设 active_end 则为当天 23:59:59。"""
    if alarm.active_end is not None:
        return datetime.combine(day.date(), alarm.active_end)
    return datetime.combine(day.date(), time(23, 59, 59))


def _interval_next(alarm: Alarm, after: datetime) -> datetime | None:
    """严格晚于 after 的下一个间隔边界。"""
    step = timedelta(minutes=alarm.interval_minutes or 1)
    start = _day_start(alarm, after)
    end = _day_end(alarm, after)

    if after < start:
        # 当天还没到开始时间:第一个边界就是 start
        return start

    # after 在 [start, ...]:算严格大于 after 的第一个 start + k*step
    elapsed = after - start
    k = int(elapsed // step) + 1
    candidate = start + k * step
    if candidate <= end:
        return candidate
    # 当天边界用尽 → 次日的开始时间
    return _day_start(alarm, after + _ONE_DAY)


def _interval_prev(alarm: Alarm, before: datetime) -> datetime | None:
    """不晚于 before 的最近一个间隔边界;没有则 None。"""
    step = timedelta(minutes=alarm.interval_minutes or 1)
    start = _day_start(alarm, before)
    end = _day_end(alarm, before)

    if before < start:
        # 当天还没开始 → 最近一次是昨天的最后一个边界
        return _interval_last_of_day(alarm, before - _ONE_DAY)

    # before >= start:当天内 <= before 的最后一个边界,但不超过 end
    cap = min(before, end)
    k = int((cap - start) // step)
    return start + k * step


def _interval_last_of_day(alarm: Alarm, day: datetime) -> datetime:
    """某天内最后一个间隔边界(<= 当天 end)。"""
    step = timedelta(minutes=alarm.interval_minutes or 1)
    start = _day_start(alarm, day)
    end = _day_end(alarm, day)
    k = int((end - start) // step)
    return start + k * step



def is_expired(alarm: Alarm, now: datetime) -> bool:
    """单次闹钟是否已彻底过期(不会再响)。

    界面用它把这些闹钟标灰,避免用户以为没生效。
    """
    if alarm.repeat is not RepeatKind.ONCE or alarm.on_date is None:
        return False
    if alarm.snooze_until is not None and alarm.snooze_until > now:
        return False
    return datetime.combine(alarm.on_date, alarm.time) <= now
