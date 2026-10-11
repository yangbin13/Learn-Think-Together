"""Application package bootstrap for stable Windows runtime detection."""

import os
import platform
import sys


def _prime_windows_platform_cache():
    """Avoid platform.py's blocking WMI query in this Windows application.

    Python 3.12's platform.system()/machine() can query WMI when their cache is
    empty. This host's WMI query stalls, which blocks imports such as pypdfium2
    and the OpenAI SDK before their own timeouts can run. Seed the same portable
    uname cache from process metadata before application modules import those
    dependencies; leave non-Windows hosts and an existing cache untouched.
    """
    if sys.platform != "win32" or platform._uname_cache is not None:
        return

    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "")
    if not machine:
        machine = "AMD64" if sys.maxsize > 2**32 else "x86"
    result = platform.uname_result("Windows", "", "", "", machine)
    # processor is lazy in Python 3.12 and may trigger another WMI lookup.
    result.__dict__["processor"] = ""
    platform._uname_cache = result


_prime_windows_platform_cache()
