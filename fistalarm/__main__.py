"""FistAlarm 入口。

两种模式,靠启动参数区分:

    FistAlarm.exe                          托盘常驻(默认)
    FistAlarm.exe --strike "文字" [--shot 图]  播放碎屏动画后退出
    FistAlarm.exe --test                   立刻试播一次动画(调试用)
    FistAlarm.exe --list                   打印现有闹钟(调试用)

托盘端到点时会用 --strike 拉起自己的一个子进程,所以两种模式共用同一个 exe。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .models import Alarm, RepeatKind, parse_hhmm
from .store import AlarmStore

EXIT_CLOSED = 0
EXIT_SNOOZE = 2
EXIT_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fistalarm", description="拳头闹钟")
    parser.add_argument(
        "--strike",
        metavar="文字",
        nargs="?",
        const="",
        help="播放碎屏动画后退出(托盘端内部调用)",
    )
    parser.add_argument("--shot", metavar="图片路径", help="碎屏动画要用的桌面截图")
    parser.add_argument("--hold", metavar="秒", type=float, default=None,
                        help="拳头停留秒数(配合 --strike,1~3)")
    parser.add_argument("--test", action="store_true", help="立刻试播一次动画")
    parser.add_argument("--manage", action="store_true", help="打开管理窗口(托盘内部调用)")
    parser.add_argument("--list", action="store_true", help="打印现有闹钟")
    parser.add_argument("--add", metavar="HH:MM", help="新增一条每天重复的闹钟")
    parser.add_argument("--message", metavar="文字", default="", help="配合 --add 使用")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK,直接 print 中文会乱码甚至抛异常。
    # 这里统一改成 UTF-8,顺带处理 emoji。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.strike is not None:
        return _run_strike(
            args.strike, Path(args.shot) if args.shot else None, args.hold
        )
    if args.test:
        return _run_strike(_default_message(), _try_capture(), _default_hold())
    if args.manage:
        return _run_manage()
    if args.list:
        return _run_list()
    if args.add:
        return _run_add(args.add, args.message)
    return _run_tray()


# ------------------------------ 播放模式 ------------------------------

def _try_capture() -> Path | None:
    """给试播/触发用:截主屏,失败就返回 None 让动画走纯黑降级。"""
    from . import capture

    try:
        return capture.capture_primary_screen(
            AlarmStore().path.parent / "shots" / "preview.png"
        )
    except capture.CaptureError:
        return None


def _default_message() -> str:
    """读全局默认文案(试播时用),读不到就用出厂值。"""
    store = AlarmStore()
    store.load()
    return store.default_message


def _default_hold() -> float:
    """读全局拳头停留秒数(试播时用)。"""
    store = AlarmStore()
    store.load()
    return store.fist_hold_seconds


def _run_strike(message: str, shot: Path | None, hold: float | None = None) -> int:
    """播放一次碎屏动画。任何异常都降级成"正常关闭",不让托盘端看到崩溃。"""
    from . import strike_player

    try:
        return strike_player.play(message, shot, hold)
    except Exception as exc:
        # 播放器起不来时,至少把提醒内容打到日志,别让闹钟静默消失
        print(f"[FistAlarm] 播放提醒失败：{exc}", file=sys.stderr)
        return EXIT_CLOSED


# ------------------------------ 托盘模式 ------------------------------

def _run_tray() -> int:
    try:
        from .tray import run_tray
    except ImportError as exc:
        print(f"[FistAlarm] 托盘模式需要 pystray 与 Pillow：{exc}", file=sys.stderr)
        return EXIT_ERROR
    return run_tray()


# ------------------------------ 管理窗口模式 ------------------------------

def _run_manage() -> int:
    """独立进程打开管理窗口。托盘用 --manage 拉起它,避免两个 GUI 循环冲突。"""
    try:
        from .ui.manager import run_manager
    except ImportError as exc:
        print(f"[FistAlarm] 管理窗口需要 pywebview：{exc}", file=sys.stderr)
        return EXIT_ERROR
    return run_manager()


# ------------------------------ 调试命令 ------------------------------

def _run_list() -> int:
    store = AlarmStore()
    alarms = store.load()
    if not alarms:
        print("（没有任何闹钟）")
        return 0
    for alarm in store.sorted_by_time():
        flag = "启用" if alarm.enabled else "停用"
        note = f"  {alarm.message}" if alarm.message else ""
        print(f"{alarm.time:%H:%M}  {alarm.repeat_label():<12} [{flag}]{note}")
    return 0


def _run_add(hhmm: str, message: str) -> int:
    store = AlarmStore()
    store.load()
    try:
        alarm = Alarm(time=parse_hhmm(hhmm), repeat=RepeatKind.DAILY, message=message)
    except ValueError as exc:
        print(f"[FistAlarm] 时间格式不对：{exc}", file=sys.stderr)
        return EXIT_ERROR
    store.add(alarm)
    store.save()
    print(f"已添加：{alarm.time:%H:%M} 每天  {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
