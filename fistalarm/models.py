"""闹钟的数据结构。

只描述"一条闹钟是什么",不含任何计算逻辑(计算在 schedule.py)。
设计原则:纯数据 + 可序列化,不依赖 UI、不依赖任何第三方库。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import StrEnum


class RepeatKind(StrEnum):
    """重复规则。用字符串枚举,序列化成 JSON 时可读。"""

    ONCE = "once"          # 单次:只在指定日期响一次
    DAILY = "daily"        # 每天
    WEEKDAYS = "weekdays"  # 工作日(周一至周五)
    CUSTOM = "custom"      # 自选星期,见 Alarm.weekdays
    INTERVAL = "interval"  # 每隔 N 分钟,见 Alarm.interval_minutes(久坐提醒)


# 星期编号与 Python 的 date.weekday() 一致:周一=0 … 周日=6
WEEKDAY_NAMES: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


@dataclass
class Alarm:
    """一条闹钟。

    time 与 repeat 是必填的,其余都有合理默认值。
    snooze_until 由"贪睡"功能写入:一个绝对时刻,早于它不再触发。
    """

    time: time
    repeat: RepeatKind = RepeatKind.DAILY
    message: str = ""
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # 仅 RepeatKind.ONCE 使用:哪一天响
    on_date: date | None = None

    # 仅 RepeatKind.CUSTOM 使用:哪几个星期几,0=周一
    weekdays: list[int] = field(default_factory=list)

    # 仅 RepeatKind.INTERVAL 使用:
    #   time 复用为"每天的开始时间";interval_minutes 是频率(每 N 分钟);
    #   active_end 是当天的结束时间(None=到午夜,次日从 time 再来)。
    interval_minutes: int | None = None
    active_end: time | None = None

    # 贪睡:绝对时刻。设置后,在它之前不触发。
    snooze_until: datetime | None = None

    def __post_init__(self) -> None:
        # 允许从 JSON 传字符串进来(JSON 里没有 enum 和 time 对象)
        if isinstance(self.repeat, str):
            self.repeat = RepeatKind(self.repeat)
        if isinstance(self.time, str):
            self.time = parse_hhmm(self.time)
        if isinstance(self.on_date, str):
            self.on_date = date.fromisoformat(self.on_date)
        if isinstance(self.active_end, str):
            self.active_end = parse_hhmm(self.active_end)
        if isinstance(self.snooze_until, str):
            self.snooze_until = datetime.fromisoformat(self.snooze_until)

        if self.repeat is RepeatKind.ONCE and self.on_date is None:
            raise ValueError("单次闹钟必须指定日期（on_date）")
        if self.repeat is RepeatKind.CUSTOM and not self.weekdays:
            raise ValueError("自选星期的闹钟必须至少指定一天（weekdays）")
        if self.repeat is RepeatKind.CUSTOM:
            bad = [d for d in self.weekdays if d not in range(7)]
            if bad:
                raise ValueError(f"weekdays 取值必须是 0-6（周一至周日），收到 {bad}")
            # 去重并排序,便于比较与展示
            self.weekdays = sorted(set(self.weekdays))
        if self.repeat is RepeatKind.INTERVAL:
            if not self.interval_minutes or self.interval_minutes <= 0:
                raise ValueError("间隔闹钟必须指定正的频率分钟数（interval_minutes）")
            if self.active_end is not None and self.active_end <= self.time:
                raise ValueError("间隔闹钟的结束时间必须晚于开始时间")

    # ---------- 序列化 ----------

    def to_dict(self) -> dict:
        """转成可json.dumps 的普通 dict。只写非空字段,让文件保持可读。"""
        data: dict = {
            "id": self.id,
            "time": self.time.strftime("%H:%M"),
            "repeat": str(self.repeat),
            "message": self.message,
            "enabled": self.enabled,
        }
        if self.on_date is not None:
            data["on_date"] = self.on_date.isoformat()
        if self.weekdays:
            data["weekdays"] = list(self.weekdays)
        if self.interval_minutes is not None:
            data["interval_minutes"] = self.interval_minutes
        if self.active_end is not None:
            data["active_end"] = self.active_end.strftime("%H:%M")
        if self.snooze_until is not None:
            data["snooze_until"] = self.snooze_until.isoformat(timespec="seconds")
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Alarm:
        """从 JSON 读回的 dict 还原。未知字段忽略,缺失字段用默认值。"""
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:12],
            time=data["time"],
            repeat=data.get("repeat", RepeatKind.DAILY),
            message=data.get("message", ""),
            enabled=bool(data.get("enabled", True)),
            on_date=data.get("on_date"),
            weekdays=list(data.get("weekdays") or []),
            interval_minutes=data.get("interval_minutes"),
            active_end=data.get("active_end"),
            snooze_until=data.get("snooze_until"),
        )

    # ---------- 展示 ----------

    def repeat_label(self) -> str:
        """给人看的重复规则描述。"""
        if self.repeat is RepeatKind.ONCE:
            return f"{self.on_date.isoformat()} 单次" if self.on_date else "单次"
        if self.repeat is RepeatKind.DAILY:
            return "每天"
        if self.repeat is RepeatKind.WEEKDAYS:
            return "工作日"
        if self.repeat is RepeatKind.INTERVAL:
            start = self.time.strftime("%H:%M")
            if self.active_end is not None:
                return f"{start}~{self.active_end.strftime('%H:%M')} 每{self.interval_minutes}分钟"
            return f"{start} 起每{self.interval_minutes}分钟"
        return "、".join(WEEKDAY_NAMES[d] for d in self.weekdays)


def parse_hhmm(text: str) -> time:
    """解析 "HH:MM" 或 "H:MM" 为 time。非法输入抛 ValueError。"""
    parts = text.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 HH:MM，收到 {text!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间超出范围：{text!r}")
    return time(hour, minute)
