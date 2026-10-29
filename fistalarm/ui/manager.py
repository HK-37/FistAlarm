"""管理界面 —— 独立进程运行。

**为什么独立进程**:pywebview 的 `webview.start()` 必须在主线程跑,且会独占一个
GUI 事件循环。托盘(pystray)本身也有一个事件循环。若在托盘进程里再开管理窗口,
两个事件循环抢同一个 Windows 消息泵 → 死锁、整个程序假死(实际踩过)。

所以管理窗口由托盘用子进程(`python -m fistalarm --manage`)拉起,自己在自己的
主线程跑 GUI,退出即回收 WebView2,互不干扰。

它直接读写 alarms.json(与托盘共享同一份文件),改完存盘,托盘下次对表自然读到。
"""
from __future__ import annotations

from pathlib import Path

from ..paths import resource
from ..store import AlarmStore

_PAGE = resource("ui", "manager.html")


def run_manager() -> int:
    """作为独立进程打开管理窗口,阻塞直到用户关闭。返回退出码。

    单实例:若已有管理窗口在跑,激活那一个并直接退出,不再开新窗口。
    真正的"不多开"由这里的 mutex 兜底——即使托盘误拉了多个 --manage 进程,
    后启动的都会在这里抢锁失败而退出。
    """
    import webview

    from ..procutil import acquire_single_instance

    if not acquire_single_instance("FistAlarm.Manager.SingleInstance"):
        # 已有管理窗口,尽力把它拉到前台,然后本进程退出
        try:
            from ..player import _activate_existing_manager

            _activate_existing_manager()
        except Exception:
            pass
        return 0

    store = AlarmStore()
    store.load()

    api = _ManagerApi(store)
    window = webview.create_window(
        title="拳头闹钟 — 管理",
        url=_PAGE.as_uri(),
        js_api=api,
        width=880,
        height=620,
        min_size=(680, 480),
        background_color="#12161c",
    )
    api._window = window
    # 不用 WebviewReaper:它按 PID 差集 taskkill 会误杀并发的动画进程(实锤过)。
    # WebView2 子进程随本进程正常退出被系统回收即可。
    webview.start()
    return 0


class _ManagerApi:
    """暴露给管理页面的接口。每个方法都能被 JS 通过 pywebview.api.xxx() 调用。

    只依赖 AlarmStore(读写同一份 alarms.json),不依赖托盘进程里的任何对象——
    这样管理窗口才能作为独立进程存在。
    """

    def __init__(self, store: AlarmStore) -> None:
        self.store = store
        self._window = None

    # ---------- 读 ----------

    def list_alarms(self) -> list[dict]:
        """返回全部闹钟,按时间排序。供页面渲染表格。"""
        from datetime import datetime

        from ..schedule import next_occurrence

        now = datetime.now()
        result = []
        for alarm in self.store.sorted_by_time():
            moment = next_occurrence(alarm, now) if alarm.enabled else None
            data = alarm.to_dict()
            data["repeat_label"] = alarm.repeat_label()
            data["next_at"] = moment.strftime("%m-%d %H:%M") if moment else ""
            data["disabled"] = not alarm.enabled
            result.append(data)
        return result

    def get_default_message(self) -> str:
        """读全局默认文案,供配置界面回填。"""
        return self.store.default_message

    def set_default_message(self, text: str) -> dict:
        """改全局默认文案并落盘。空字符串会被忽略(保持原值)。"""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "默认文案不能为空"}
        self.store.default_message = text
        self.store.save()
        return {"ok": True}

    def get_fist_hold_seconds(self) -> float:
        """读拳头停留秒数,供配置界面回填。"""
        return self.store.fist_hold_seconds

    def set_fist_hold_seconds(self, seconds) -> dict:
        """改拳头停留秒数并落盘。超出 1~3 会被夹到区间。"""
        from ..store import clamp_hold_seconds

        self.store.fist_hold_seconds = clamp_hold_seconds(seconds)
        self.store.save()
        return {"ok": True, "value": self.store.fist_hold_seconds}

    def status_text(self) -> str:
        """状态栏:独立进程里没有 runner 统计,显示闹钟条数与数据文件位置。"""
        total = len(self.store.alarms)
        enabled = len(self.store.enabled_alarms())
        return f"共 {total} 条闹钟，启用 {enabled} 条"

    # ---------- 写 ----------

    def save_alarm(self, payload: dict) -> dict:
        """新增或更新一条闹钟。payload 里带 id 就是更新,不带就是新增。

        返回 {"ok": True} 或 {"ok": False, "error": "..."}。
        用返回体而不是抛异常:页面要能读到错误并显示出来。

        先 reload 再改:独立进程期间托盘可能也动过文件(如贪睡写入),
        避免用内存里的旧快照覆盖掉。
        """
        from ..models import Alarm

        # 诊断:记录前端实际传来的关键字段,排查"设1分钟却存成5"之类
        self._log(
            f"save_alarm payload: repeat={payload.get('repeat')!r} "
            f"interval_minutes={payload.get('interval_minutes')!r} "
            f"time={payload.get('time')!r} active_end={payload.get('active_end')!r}"
        )

        try:
            alarm = Alarm.from_dict(payload)
        except (ValueError, KeyError, TypeError) as exc:
            return {"ok": False, "error": str(exc)}

        self.store.load()
        if self.store.get(alarm.id) is not None:
            self.store.replace(alarm)
        else:
            self.store.add(alarm)
        self.store.save()
        return {"ok": True, "id": alarm.id}

    def delete_alarm(self, alarm_id: str) -> dict:
        self.store.load()
        removed = self.store.remove(alarm_id)
        self.store.save()
        return {"ok": removed}

    def toggle_alarm(self, alarm_id: str, enabled: bool) -> dict:
        self.store.load()
        alarm = self.store.get(alarm_id)
        if alarm is None:
            return {"ok": False, "error": "找不到这条闹钟"}
        alarm.enabled = enabled
        self.store.save()
        return {"ok": True}

    def test_strike(self) -> dict:
        """页面上点"试播"时调用:自己拉一个 --strike 子进程,不阻塞管理窗口。"""
        from .. import capture, player

        try:
            shot = None
            try:
                shot = capture.capture_primary_screen(
                    self.store.path.parent / "shots" / "preview.png"
                )
            except capture.CaptureError:
                shot = None
            # 不等它结束(等的话管理窗口会卡住),发出去即可
            player.run_strike(
                self.store.default_message, shot, hold_seconds=self.store.fist_hold_seconds
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def _log(self, text: str) -> None:
        """写一条诊断日志到 manager.log。"""
        import datetime

        try:
            log = self.store.path.parent / "manager.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as h:
                h.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
        except Exception:
            pass

    def log_js_error(self, text: str) -> None:
        """页面 JS 报错时回传记日志,便于排查黑屏。"""
        self._log(f"JS: {text}")
