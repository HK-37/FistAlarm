"""全局测试设置。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_tray_log(tmp_path, monkeypatch):
    """托盘日志一律写到临时目录,不污染用户真实的 %APPDATA%\\FistAlarm\\tray.log。"""
    from fistalarm import tray

    monkeypatch.setattr(tray, "default_data_dir", lambda: tmp_path)
