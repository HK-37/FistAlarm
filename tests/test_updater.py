"""检查更新的测试。

不碰网络:取"最新版本跳转地址"的函数通过参数注入,测试里传假的。
"""
from __future__ import annotations

from fistalarm.updater import (
    RELEASES_URL,
    CheckResult,
    check_for_update,
    parse_version,
    tag_from_location,
)

TAG_URL = "https://github.com/HK-37/FistAlarm/releases/tag/v0.2.0"


class TestParseVersion:
    def test_with_v_prefix(self) -> None:
        assert parse_version("v0.1.0") == (0, 1, 0)

    def test_without_prefix(self) -> None:
        assert parse_version("1.2.3") == (1, 2, 3)

    def test_garbage_is_none(self) -> None:
        assert parse_version("latest") is None

    def test_prerelease_suffix_is_none(self) -> None:
        """带 -beta 之类后缀的不认,避免把测试版当正式版提醒。"""
        assert parse_version("v1.0.0-beta") is None

    def test_numeric_not_string_compare(self) -> None:
        """0.1.10 比 0.1.9 新;按字符串比会反过来。"""
        assert parse_version("0.1.10") > parse_version("0.1.9")


class TestTagFromLocation:
    def test_tag_url(self) -> None:
        assert tag_from_location(TAG_URL) == "v0.2.0"

    def test_no_release_redirects_to_list(self) -> None:
        """一个版本都没发过时,GitHub 跳到 Release 列表页而不是某个 tag。"""
        assert tag_from_location(RELEASES_URL) is None

    def test_none(self) -> None:
        assert tag_from_location(None) is None


class TestCheckForUpdate:
    def test_newer_available(self) -> None:
        result = check_for_update("0.1.0", fetch=lambda: TAG_URL)
        assert result.status == "update"
        assert result.latest == "v0.2.0"
        assert result.url == TAG_URL

    def test_same_version_is_latest(self) -> None:
        result = check_for_update("0.2.0", fetch=lambda: TAG_URL)
        assert result.status == "latest"

    def test_local_newer_is_latest(self) -> None:
        """本地比线上还新(比如还没发版的开发版),不提醒。"""
        result = check_for_update("0.3.0", fetch=lambda: TAG_URL)
        assert result.status == "latest"

    def test_trailing_zero_counts_as_same(self) -> None:
        """本地写 0.2、线上 tag 是 v0.2.0,是同一版,不该提醒。"""
        result = check_for_update("0.2", fetch=lambda: TAG_URL)
        assert result.status == "latest"

    def test_no_release_yet_is_latest(self) -> None:
        result = check_for_update("0.1.0", fetch=lambda: RELEASES_URL)
        assert result.status == "latest"

    def test_network_error_is_error_not_latest(self) -> None:
        """断网时必须报"检查失败",不能误报"已是最新"。"""

        def boom() -> str:
            raise OSError("网络不通")

        result = check_for_update("0.1.0", fetch=boom)
        assert result.status == "error"
        assert "网络不通" in (result.error or "")

    def test_unparseable_tag_is_error(self) -> None:
        url = "https://github.com/HK-37/FistAlarm/releases/tag/nightly"
        result = check_for_update("0.1.0", fetch=lambda: url)
        assert result.status == "error"


class TestTrayUpdateCheck:
    """托盘侧:发现新版提醒一次,同版本不重复提醒;手动检查总要给结果。"""

    @staticmethod
    def _app(tmp_path, results):
        from fistalarm.store import AlarmStore
        from fistalarm.tray import TrayApp

        app = TrayApp(AlarmStore(tmp_path / "alarms.json"))
        seq = list(results)
        app.update_checker = lambda: seq.pop(0)
        notes: list[tuple[str, str]] = []
        app.notifier = lambda title, msg: notes.append((title, msg))
        return app, notes

    def test_auto_check_notifies_new_version_once(self, tmp_path) -> None:
        upd = CheckResult("update", latest="v0.2.0", url=TAG_URL)
        app, notes = self._app(tmp_path, [upd, upd])
        app.run_update_check(manual=False)
        app.run_update_check(manual=False)
        assert len(notes) == 1, "同一个新版本只提醒一次"
        assert "v0.2.0" in notes[0][1]
        assert app.update_available is True

    def test_auto_check_silent_when_latest_or_error(self, tmp_path) -> None:
        app, notes = self._app(
            tmp_path, [CheckResult("latest"), CheckResult("error", error="x")]
        )
        app.run_update_check(manual=False)
        app.run_update_check(manual=False)
        assert notes == [], "自动检查没新版或失败时不打扰用户"
        assert app.update_available is False

    def test_manual_check_reports_latest(self, tmp_path) -> None:
        app, notes = self._app(tmp_path, [CheckResult("latest")])
        app.run_update_check(manual=True)
        assert len(notes) == 1
        assert "最新" in notes[0][1]

    def test_manual_check_reports_error(self, tmp_path) -> None:
        app, notes = self._app(tmp_path, [CheckResult("error", error="超时")])
        app.run_update_check(manual=True)
        assert len(notes) == 1
        assert "失败" in notes[0][1]

    def test_manual_check_reports_known_update_again(self, tmp_path) -> None:
        """自动检查已提醒过,用户手动再查时仍要告诉他有新版。"""
        upd = CheckResult("update", latest="v0.2.0", url=TAG_URL)
        app, notes = self._app(tmp_path, [upd, upd])
        app.run_update_check(manual=False)
        app.run_update_check(manual=True)
        assert len(notes) == 2


class TestTrayStatusText:
    """菜单常驻状态项:无论系统气泡弹没弹,结果都要显示在菜单里。

    实际遇到过:点"检查更新"什么都没弹出来——系统气泡被勿扰/专注模式吞了,
    而结果当时只靠气泡告诉用户。状态项是兜底。
    """

    @staticmethod
    def _app(tmp_path, result):
        from fistalarm.store import AlarmStore
        from fistalarm.tray import TrayApp

        app = TrayApp(AlarmStore(tmp_path / "alarms.json"))
        app.update_checker = lambda: result
        app.notifier = None  # 模拟气泡完全不可用
        return app

    def test_initial_status(self, tmp_path) -> None:
        app = self._app(tmp_path, CheckResult("latest"))
        assert "未检查" in app.update_status_text

    def test_latest_shown_without_notifier(self, tmp_path) -> None:
        app = self._app(tmp_path, CheckResult("latest"))
        app.run_update_check(manual=True)
        assert "最新" in app.update_status_text

    def test_update_shown_without_notifier(self, tmp_path) -> None:
        app = self._app(tmp_path, CheckResult("update", latest="v0.3.0", url=TAG_URL))
        app.run_update_check(manual=True)
        assert "v0.3.0" in app.update_status_text

    def test_error_shown_without_notifier(self, tmp_path) -> None:
        app = self._app(tmp_path, CheckResult("error", error="超时"))
        app.run_update_check(manual=True)
        assert "失败" in app.update_status_text and "超时" in app.update_status_text

    def test_auto_check_also_updates_status(self, tmp_path) -> None:
        """自动检查虽然不弹气泡,但菜单状态也要更新,用户点开就能看到。"""
        app = self._app(tmp_path, CheckResult("latest"))
        app.run_update_check(manual=False)
        assert "最新" in app.update_status_text


class TestCheckNeverFailsSilently:
    """exe 不弹控制台,后台线程里抛的异常会直接消失——用户看到的就是"点了没反应"。
    所以检查过程中任何异常都必须变成菜单上的"检查失败",并写进 tray.log。
    """

    def test_checker_exception_becomes_failed_status(self, tmp_path, monkeypatch) -> None:
        from fistalarm import tray as tray_mod
        from fistalarm.store import AlarmStore
        from fistalarm.tray import TrayApp

        monkeypatch.setattr(tray_mod, "default_data_dir", lambda: tmp_path)
        app = TrayApp(AlarmStore(tmp_path / "alarms.json"))

        def boom():
            raise RuntimeError("证书错误")

        app.update_checker = boom
        app.run_update_check(manual=True)  # 不能把异常抛出去
        assert "失败" in app.update_status_text
        assert "证书错误" in app.update_status_text
        assert "证书错误" in (tmp_path / "tray.log").read_text(encoding="utf-8")

    def test_check_logs_result(self, tmp_path, monkeypatch) -> None:
        from fistalarm import tray as tray_mod
        from fistalarm.store import AlarmStore
        from fistalarm.tray import TrayApp

        monkeypatch.setattr(tray_mod, "default_data_dir", lambda: tmp_path)
        app = TrayApp(AlarmStore(tmp_path / "alarms.json"))
        app.update_checker = lambda: CheckResult("latest", latest="v0.2.0")
        app.run_update_check(manual=True)
        log = (tmp_path / "tray.log").read_text(encoding="utf-8")
        assert "latest" in log


class TestCheckUpdateCli:
    """--check-update:不开托盘,直接跑一次检查并把结果写进 tray.log,用来诊断打包后的 exe。"""

    def test_cli_writes_result_to_log(self, tmp_path, monkeypatch) -> None:
        from fistalarm import __main__ as entry
        from fistalarm import tray as tray_mod
        from fistalarm import updater

        monkeypatch.setattr(tray_mod, "default_data_dir", lambda: tmp_path)
        monkeypatch.setattr(
            updater, "check_for_update", lambda: CheckResult("update", latest="v9.9.9", url=TAG_URL)
        )
        assert entry.main(["--check-update"]) == 0
        log = (tmp_path / "tray.log").read_text(encoding="utf-8")
        assert "v9.9.9" in log


class TestManualCheckDialog:
    """手动检查用对话框给结果(弹窗通知在 Win11 上会被吞,实际遇到过"点了没反应")。

    dialog(title, message, ask) -> bool:ask=True 时是"是/否"对话框,返回用户是否点了"是"。
    """

    @staticmethod
    def _app(tmp_path, monkeypatch, result, answer=False):
        from fistalarm import tray as tray_mod
        from fistalarm.store import AlarmStore
        from fistalarm.tray import TrayApp

        monkeypatch.setattr(tray_mod, "default_data_dir", lambda: tmp_path)
        app = TrayApp(AlarmStore(tmp_path / "alarms.json"))
        app.update_checker = lambda: result
        shown: list[tuple[str, bool]] = []

        def dialog(title, message, ask):
            shown.append((message, ask))
            return answer

        app.dialog = dialog
        notes: list[str] = []
        app.notifier = lambda title, msg: notes.append(msg)
        opened: list[bool] = []
        app.open_download_page = lambda: opened.append(True)
        return app, shown, notes, opened

    def test_manual_latest_shows_dialog(self, tmp_path, monkeypatch) -> None:
        app, shown, notes, _ = self._app(tmp_path, monkeypatch, CheckResult("latest"))
        app.run_update_check(manual=True)
        assert len(shown) == 1 and "最新" in shown[0][0] and shown[0][1] is False
        assert notes == [], "有对话框时不再重复发弹窗通知"

    def test_manual_error_shows_dialog(self, tmp_path, monkeypatch) -> None:
        app, shown, _, _ = self._app(tmp_path, monkeypatch, CheckResult("error", error="超时"))
        app.run_update_check(manual=True)
        assert "失败" in shown[0][0] and "超时" in shown[0][0]

    def test_manual_update_asks_and_opens_on_yes(self, tmp_path, monkeypatch) -> None:
        upd = CheckResult("update", latest="v0.3.0", url=TAG_URL)
        app, shown, _, opened = self._app(tmp_path, monkeypatch, upd, answer=True)
        app.run_update_check(manual=True)
        assert shown[0][1] is True and "v0.3.0" in shown[0][0]
        assert opened == [True], "点了「是」要打开下载页"

    def test_manual_update_no_does_not_open(self, tmp_path, monkeypatch) -> None:
        upd = CheckResult("update", latest="v0.3.0", url=TAG_URL)
        app, _, _, opened = self._app(tmp_path, monkeypatch, upd, answer=False)
        app.run_update_check(manual=True)
        assert opened == []

    def test_auto_check_never_shows_dialog(self, tmp_path, monkeypatch) -> None:
        """自动检查是后台行为,不能突然弹对话框打断用户。"""
        upd = CheckResult("update", latest="v0.3.0", url=TAG_URL)
        app, shown, notes, _ = self._app(tmp_path, monkeypatch, upd)
        app.run_update_check(manual=False)
        assert shown == []
        assert len(notes) == 1
