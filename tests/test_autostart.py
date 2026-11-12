"""开机自启的测试。

不碰真注册表:注册表读写通过一个后端对象注入,测试用内存假后端。
"""
from __future__ import annotations

from fistalarm.autostart import RUN_VALUE_NAME, AutoStart


class FakeRegistry:
    """内存版注册表 Run 键,模拟 winreg 的读/写/删。"""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(initial or {})

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def _auto(initial=None, target='"C:\\app\\FistAlarm.exe"', available=True) -> AutoStart:
    return AutoStart(
        registry=FakeRegistry(initial),
        launch_command=lambda: target,
        available=available,
    )


class TestIsEnabled:
    def test_off_by_default(self) -> None:
        assert _auto().is_enabled() is False

    def test_on_when_registry_has_our_value(self) -> None:
        auto = _auto({RUN_VALUE_NAME: '"C:\\app\\FistAlarm.exe"'})
        assert auto.is_enabled() is True

    def test_other_app_value_does_not_count(self) -> None:
        auto = _auto({"SomethingElse": "x"})
        assert auto.is_enabled() is False


class TestEnableDisable:
    def test_enable_writes_launch_command(self) -> None:
        auto = _auto()
        auto.enable()
        assert auto.is_enabled() is True
        assert auto.registry.get(RUN_VALUE_NAME) == '"C:\\app\\FistAlarm.exe"'

    def test_enable_is_idempotent(self) -> None:
        auto = _auto()
        auto.enable()
        auto.enable()
        assert auto.is_enabled() is True

    def test_disable_removes_value(self) -> None:
        auto = _auto({RUN_VALUE_NAME: '"C:\\app\\FistAlarm.exe"'})
        auto.disable()
        assert auto.is_enabled() is False

    def test_disable_when_already_off_is_noop(self) -> None:
        auto = _auto()
        auto.disable()  # 不应抛异常
        assert auto.is_enabled() is False

    def test_enable_refreshes_stale_path(self) -> None:
        """exe 换了位置,重新开启自启要写成新路径(否则开机启的是旧路径)。"""
        auto = _auto(
            {RUN_VALUE_NAME: '"C:\\old\\FistAlarm.exe"'},
            target='"C:\\new\\FistAlarm.exe"',
        )
        auto.enable()
        assert auto.registry.get(RUN_VALUE_NAME) == '"C:\\new\\FistAlarm.exe"'

    def test_set_true_false(self) -> None:
        auto = _auto()
        auto.set(True)
        assert auto.is_enabled() is True
        auto.set(False)
        assert auto.is_enabled() is False


class TestAvailability:
    def test_unavailable_source_run(self) -> None:
        """源码运行(非打包)时自启没意义,available=False;开关应被判为不可用。"""
        auto = _auto(available=False)
        assert auto.available is False

    def test_enable_when_unavailable_does_nothing(self) -> None:
        auto = _auto(available=False)
        auto.enable()
        assert auto.is_enabled() is False
