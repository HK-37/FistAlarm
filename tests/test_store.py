from __future__ import annotations

"""持久化的单测。

重点:原子写、坏文件备份而不丢失、增删改的边界。
"""

import json
from datetime import time

from fistalarm.models import Alarm, RepeatKind
from fistalarm.store import AlarmStore


def make_alarm(hh: int = 9, mm: int = 0, msg: str = "") -> Alarm:
    alarm = Alarm(time=time(hh, mm), repeat=RepeatKind.DAILY, message=msg)
    return alarm


class TestSaveAndLoad:
    def test_round_trip(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "alarms.json")
        store.add(make_alarm(9, 30, "起床"))
        store.add(make_alarm(18, 0, "下班"))
        store.save()

        reloaded = AlarmStore(tmp_path / "alarms.json")
        alarms = reloaded.load()
        assert len(alarms) == 2
        assert alarms[0].message == "起床"
        assert alarms[1].time == time(18, 0)

    def test_missing_file_is_empty(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "nope.json")
        assert store.load() == []

    def test_chinese_is_readable_in_file(self, tmp_path) -> None:
        """备注要能在文件里直接看懂,不能被转义成 \\uXXXX。"""
        path = tmp_path / "alarms.json"
        store = AlarmStore(path)
        store.add(make_alarm(9, 0, "早起开会"))
        store.save()
        assert "早起开会" in path.read_text(encoding="utf-8")

    def test_no_tmp_file_left_behind(self, tmp_path) -> None:
        """原子写用的 .tmp 不应该留在磁盘上。"""
        path = tmp_path / "alarms.json"
        store = AlarmStore(path)
        store.add(make_alarm())
        store.save()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_overwrite_existing(self, tmp_path) -> None:
        path = tmp_path / "alarms.json"
        first = AlarmStore(path)
        first.add(make_alarm(9, 0, "旧"))
        first.save()

        second = AlarmStore(path)
        second.load()
        second.alarms.clear()
        second.add(make_alarm(10, 0, "新"))
        second.save()

        third = AlarmStore(path)
        assert len(third.load()) == 1
        assert third.alarms[0].message == "新"


class TestBrokenFile:
    def test_backup_and_recover(self, tmp_path) -> None:
        """坏文件应被备份,而不是让程序崩溃或悄悄覆盖。"""
        path = tmp_path / "alarms.json"
        path.write_text("{ 这不是合法 JSON", encoding="utf-8")

        store = AlarmStore(path)
        assert store.load() == []
        assert store.last_backup is not None
        assert store.last_backup.exists(), "坏文件应被保留成备份"
        assert not path.exists(), "原文件应已让位给备份"

    def test_backup_keeps_original_content(self, tmp_path) -> None:
        path = tmp_path / "alarms.json"
        garbage = "{ 坏掉了"
        path.write_text(garbage, encoding="utf-8")

        store = AlarmStore(path)
        store.load()
        assert store.last_backup is not None
        assert store.last_backup.read_text(encoding="utf-8") == garbage

    def test_valid_after_broken_is_clean(self, tmp_path) -> None:
        """坏文件恢复后,保存新数据应正常。"""
        path = tmp_path / "alarms.json"
        path.write_text("坏", encoding="utf-8")
        store = AlarmStore(path)
        store.load()
        store.add(make_alarm(9, 0, "恢复后新建"))
        store.save()

        again = AlarmStore(path)
        assert len(again.load()) == 1
        assert again.last_backup is None


class TestCrud:
    def test_add_and_get(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        alarm = store.add(make_alarm())
        assert store.get(alarm.id) is alarm

    def test_get_missing_returns_none(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        assert store.get("不存在") is None

    def test_remove(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        alarm = store.add(make_alarm())
        assert store.remove(alarm.id) is True
        assert store.get(alarm.id) is None

    def test_remove_missing_returns_false(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        assert store.remove("没有这个") is False

    def test_replace(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        alarm = store.add(make_alarm(9, 0, "旧"))
        updated = Alarm(
            id=alarm.id, time=time(10, 0), repeat=RepeatKind.DAILY, message="新"
        )
        assert store.replace(updated) is True
        assert store.get(alarm.id).message == "新"

    def test_replace_missing_returns_false(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        assert store.replace(make_alarm()) is False

    def test_enabled_filter(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        on = store.add(make_alarm(9, 0))
        off = store.add(make_alarm(10, 0))
        off.enabled = False
        assert store.enabled_alarms() == [on]

    def test_sorted_by_time(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        store.add(make_alarm(18, 0))
        store.add(make_alarm(7, 30))
        store.add(make_alarm(12, 0))
        times = [a.time for a in store.sorted_by_time()]
        assert times == [time(7, 30), time(12, 0), time(18, 0)]


class TestDefaultMessage:
    """全局默认文案:空备注的闹钟回退到它,且能持久化。"""

    def test_factory_default(self, tmp_path) -> None:
        from fistalarm.store import FACTORY_DEFAULT_MESSAGE

        store = AlarmStore(tmp_path / "a.json")
        assert store.default_message == FACTORY_DEFAULT_MESSAGE

    def test_message_for_uses_own_message(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        alarm = make_alarm(9, 0, "自己的备注")
        assert store.message_for(alarm) == "自己的备注"

    def test_message_for_falls_back_to_default(self, tmp_path) -> None:
        store = AlarmStore(tmp_path / "a.json")
        store.default_message = "该起来活动活动了！"
        empty = make_alarm(9, 0, "")
        assert store.message_for(empty) == "该起来活动活动了！"

    def test_whitespace_message_falls_back(self, tmp_path) -> None:
        """只有空格的备注等同于没填。"""
        store = AlarmStore(tmp_path / "a.json")
        store.default_message = "默认文案"
        blank = make_alarm(9, 0, "   ")
        assert store.message_for(blank) == "默认文案"

    def test_default_message_persists(self, tmp_path) -> None:
        path = tmp_path / "a.json"
        store = AlarmStore(path)
        store.default_message = "起床啦"
        store.add(make_alarm(9, 0))
        store.save()

        reloaded = AlarmStore(path)
        reloaded.load()
        assert reloaded.default_message == "起床啦"

    def test_empty_default_in_file_falls_back_to_factory(self, tmp_path) -> None:
        """文件里 default_message 为空串时,回退出厂值而不是留空。"""
        from fistalarm.store import FACTORY_DEFAULT_MESSAGE

        path = tmp_path / "a.json"
        path.write_text('{"version":1,"default_message":"","alarms":[]}', encoding="utf-8")
        store = AlarmStore(path)
        store.load()
        assert store.default_message == FACTORY_DEFAULT_MESSAGE


class TestFistHoldSeconds:
    """拳头停留时长:夹在 1~3,可持久化。"""

    def test_factory_default(self, tmp_path) -> None:
        from fistalarm.store import FACTORY_FIST_HOLD_SECONDS

        store = AlarmStore(tmp_path / "a.json")
        assert store.fist_hold_seconds == FACTORY_FIST_HOLD_SECONDS

    def test_clamp_below_min(self) -> None:
        from fistalarm.store import clamp_hold_seconds
        assert clamp_hold_seconds(0.3) == 1.0

    def test_clamp_above_max(self) -> None:
        from fistalarm.store import clamp_hold_seconds
        assert clamp_hold_seconds(10) == 3.0

    def test_clamp_within_range(self) -> None:
        from fistalarm.store import clamp_hold_seconds
        assert clamp_hold_seconds(2.5) == 2.5

    def test_clamp_bad_value_falls_back(self) -> None:
        from fistalarm.store import clamp_hold_seconds, FACTORY_FIST_HOLD_SECONDS
        assert clamp_hold_seconds("abc") == FACTORY_FIST_HOLD_SECONDS

    def test_persists(self, tmp_path) -> None:
        path = tmp_path / "a.json"
        store = AlarmStore(path)
        store.fist_hold_seconds = 2.5
        store.save()
        reloaded = AlarmStore(path)
        reloaded.load()
        assert reloaded.fist_hold_seconds == 2.5

    def test_out_of_range_in_file_clamped_on_load(self, tmp_path) -> None:
        path = tmp_path / "a.json"
        path.write_text('{"version":1,"fist_hold_seconds":99,"alarms":[]}', encoding="utf-8")
        store = AlarmStore(path)
        store.load()
        assert store.fist_hold_seconds == 3.0


class TestCrossProcessOverwrite:
    """防回归:两个进程(托盘+管理窗口)共享 alarms.json,
    托盘不能用启动时的旧内存覆盖管理窗口刚存的修改。
    修复手段是托盘 save 前先 reload——这里固化这个契约。
    """

    def test_stale_memory_overwrites_without_reload(self, tmp_path) -> None:
        """没 reload 时确实会覆盖(证明 bug 存在)。"""
        path = tmp_path / "alarms.json"
        tray = AlarmStore(path)
        tray.default_message = "改起来活动啦"
        tray.save()
        tray_mem = AlarmStore(path); tray_mem.load()  # 旧内存

        mgr = AlarmStore(path); mgr.load()
        mgr.default_message = "该起来活动啦"
        mgr.save()

        tray_mem.save()  # 旧内存写回
        reloaded = AlarmStore(path); reloaded.load()
        assert reloaded.default_message == "改起来活动啦", "无 reload 时旧值覆盖(bug 现象)"

    def test_reload_before_save_preserves_changes(self, tmp_path) -> None:
        """reload 后再 save,管理窗口的修改保住(修复)。"""
        path = tmp_path / "alarms.json"
        tray = AlarmStore(path)
        tray.default_message = "改起来活动啦"
        tray.save()
        tray_mem = AlarmStore(path); tray_mem.load()

        mgr = AlarmStore(path); mgr.load()
        mgr.default_message = "该起来活动啦"
        mgr.save()

        tray_mem.load()   # <<< 修复:save 前 reload
        tray_mem.save()
        reloaded = AlarmStore(path); reloaded.load()
        assert reloaded.default_message == "该起来活动啦", "reload 后不该覆盖用户修改"
