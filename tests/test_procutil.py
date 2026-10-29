"""procutil 的纯逻辑测试:不真起进程,只验证 PID 解析与差集回收逻辑。"""
from __future__ import annotations

from fistalarm import procutil


def test_webview_pids_parses_csv(monkeypatch) -> None:
    """tasklist 的 CSV 输出应被正确解析成 PID 集合。"""
    sample = (
        '"msedgewebview2.exe","12345","Console","1","50,000 K"\n'
        '"msedgewebview2.exe","67890","Console","1","40,000 K"\n'
    )

    class _R:
        stdout = sample

    monkeypatch.setattr(procutil.sys, "platform", "win32")
    monkeypatch.setattr(procutil.subprocess, "run", lambda *a, **k: _R())
    assert procutil.webview_pids() == {12345, 67890}


def test_webview_pids_empty_on_non_windows(monkeypatch) -> None:
    monkeypatch.setattr(procutil.sys, "platform", "linux")
    assert procutil.webview_pids() == set()


def test_reaper_kills_only_new_pids(monkeypatch) -> None:
    """只回收窗口期间新增的 PID,已存在的不动(避免误伤他人的 WebView2)。"""
    killed: list[set[int]] = []
    seq = [{1, 2}, {1, 2, 3, 4}]  # 进入时已有 1,2;退出时多了 3,4

    monkeypatch.setattr(procutil, "webview_pids", lambda: seq.pop(0))
    monkeypatch.setattr(procutil, "kill_pids", lambda pids: killed.append(pids))

    with procutil.WebviewReaper():
        pass

    assert killed == [{3, 4}]


def test_kill_pids_empty_is_noop(monkeypatch) -> None:
    """空集合不该起 taskkill 进程。"""
    calls = []
    monkeypatch.setattr(procutil.sys, "platform", "win32")
    monkeypatch.setattr(procutil.subprocess, "run", lambda *a, **k: calls.append(a))
    procutil.kill_pids(set())
    assert calls == []


def test_kill_pids_batches_one_call(monkeypatch) -> None:
    """多个 PID 应合并成一次 taskkill 调用(而不是每个一次)。"""
    calls = []
    monkeypatch.setattr(procutil.sys, "platform", "win32")
    monkeypatch.setattr(procutil.subprocess, "run",
                        lambda *a, **k: calls.append(a[0]) or type("R", (), {"stdout": ""})())
    procutil.kill_pids({111, 222, 333})
    assert len(calls) == 1, "应只调一次 taskkill"
    args = calls[0]
    assert args.count("/PID") == 3, "三个 PID 都在同一条命令里"
