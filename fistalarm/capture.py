"""把桌面的样子截下来。

单独成模块的原因:这是唯一依赖第三方库(mss)的地方,而且它在
无显示器、远程会话、全屏独占程序等场景下会失败。调用方需要能拿到
"失败"这个结果并降级,而不是让整个闹钟崩掉。
"""
from __future__ import annotations

from pathlib import Path


class CaptureError(RuntimeError):
    """截图失败。调用方应降级为纯黑背景,而不是取消本次提醒。"""


def capture_primary_screen(dest: Path) -> Path:
    """截主屏,存成 PNG,返回文件路径。

    失败时抛 CaptureError——包括 mss 未安装、没有可用显示器、权限不足。
    刻意不在这里吞异常:让调用方显式决定怎么降级。
    """
    try:
        import mss  # type: ignore[import-untyped]
        import mss.tools  # type: ignore[import-untyped]
    except ImportError as exc:
        raise CaptureError("未安装 mss,无法截屏") from exc

    try:
        with mss.mss() as sct:
            # monitors[0] 是所有显示器的合并区域,monitors[1] 才是主屏。
            # 我们要主屏。
            if len(sct.monitors) < 2:
                raise CaptureError("没有检测到可用显示器")
            monitor = sct.monitors[1]
            shot = sct.grab(monitor)
            dest.parent.mkdir(parents=True, exist_ok=True)
            mss.tools.to_png(shot.rgb, shot.size, output=str(dest))
    except CaptureError:
        raise
    except Exception as exc:  # 驱动的各种失败都归到这一类
        raise CaptureError(f"截屏失败：{exc}") from exc

    return dest


def screen_size() -> tuple[int, int]:
    """返回主屏分辨率 (宽, 高)。取不到时抛 CaptureError。"""
    try:
        import mss  # type: ignore[import-untyped]
    except ImportError as exc:
        raise CaptureError("未安装 mss,无法获取屏幕尺寸") from exc
    try:
        with mss.mss() as sct:
            if len(sct.monitors) < 2:
                raise CaptureError("没有检测到可用显示器")
            monitor = sct.monitors[1]
            return int(monitor["width"]), int(monitor["height"])
    except CaptureError:
        raise
    except Exception as exc:
        raise CaptureError(f"获取屏幕尺寸失败：{exc}") from exc
