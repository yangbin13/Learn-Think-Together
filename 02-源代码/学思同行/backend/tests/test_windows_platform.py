import platform
import sys

from backend import app as app_package


def test_windows_platform_cache_avoids_wmi_for_dependency_imports(monkeypatch):
    def forbidden_wmi(*args, **kwargs):
        raise AssertionError("Windows platform detection must not call WMI")

    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "win32")
        patch.setattr(platform, "_uname_cache", None)
        patch.setattr(platform, "_wmi_query", forbidden_wmi)

        app_package._prime_windows_platform_cache()

        assert platform.system() == "Windows"
        assert platform.machine()
        assert platform.processor() == ""
