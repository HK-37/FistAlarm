"""检查更新:问 GitHub「最新正式版是哪个」,和本地版本比较。

只检查、只提醒,不自动下载替换(Windows 上运行中的 exe 不能覆盖自己,
且"程序自己下 exe 换掉自己"容易被安全软件拦截)。

取最新版本走网页跳转而不是 GitHub API:
    GET https://github.com/<repo>/releases/latest
    → 302 跳到 .../releases/tag/v0.2.0   (有正式版)
    → 302 跳到 .../releases              (一个版本都没发过)
API 匿名调用按出口 IP 限每小时 60 次,公司网络出口 IP 是共用的,
实测早被用光(返回 rate limit exceeded);网页跳转不受这个限制。
预发布(Pre-release)版本不会被 GitHub 当成 latest,所以不会被提醒。
"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import unquote

from . import __version__

REPO = "HK-37/FistAlarm"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_URL = f"{RELEASES_URL}/latest"

_TAG_RE = re.compile(r"/releases/tag/([^/?#]+)$")
_VERSION_RE = re.compile(r"v?(\d+(?:\.\d+)*)")


@dataclass
class CheckResult:
    """一次检查的结果。

    status:
        "update"  有比本地新的正式版,url 指向它的下载页
        "latest"  本地已是最新(或线上还没有任何发布)
        "error"   检查失败(断网、超时等),error 里是原因
    """

    status: str
    latest: str | None = None
    url: str = RELEASES_URL
    error: str | None = None


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """"v0.1.10" → (0, 1, 10)。认不出(含 -beta 等后缀)返回 None。"""
    if not text:
        return None
    m = _VERSION_RE.fullmatch(text.strip())
    if not m:
        return None
    return tuple(int(part) for part in m.group(1).split("."))


def tag_from_location(location: str | None) -> str | None:
    """从跳转地址里取 tag;跳到的不是某个 tag(还没发过版)返回 None。"""
    if not location:
        return None
    m = _TAG_RE.search(location)
    return unquote(m.group(1)) if m else None


def _padded(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """补零到等长,让 0.2 和 0.2.0 相等(元组直接比较时 (0,2) < (0,2,0))。"""
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随跳转:我们要的就是跳转地址本身。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def fetch_latest_location(timeout: float = 10.0) -> str:
    """请求 releases/latest,返回 GitHub 给的跳转地址。网络问题直接抛异常。"""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        LATEST_URL, method="HEAD", headers={"User-Agent": f"FistAlarm/{__version__}"}
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            # 没有跳转(GitHub 改了行为),退化为返回最终地址
            return resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get("Location")
            if location:
                return location
        raise


def check_for_update(
    current: str = __version__,
    fetch: Callable[[], str] = fetch_latest_location,
) -> CheckResult:
    """比较线上最新正式版与本地版本。任何网络异常都归为 status="error"。"""
    try:
        location = fetch()
    except Exception as exc:  # 断网/超时/证书等,统一当"检查失败"
        return CheckResult("error", error=str(exc) or exc.__class__.__name__)

    tag = tag_from_location(location)
    if tag is None:
        return CheckResult("latest")

    remote, local = parse_version(tag), parse_version(current)
    if remote is None or local is None:
        return CheckResult("error", error=f"无法识别的版本号：{tag}")

    remote, local = _padded(remote, local)
    if remote > local:
        return CheckResult("update", latest=tag, url=location)
    return CheckResult("latest", latest=tag)
