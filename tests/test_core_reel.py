"""Intégration contre le vrai herdr-core. Lecture seule : hello, events.subscribe, terminal.list."""
import shutil
import subprocess

import pytest

from herdr_web.core import HcClient


def _hc_repond():
    if shutil.which("hc") is None:
        return False
    try:
        p = subprocess.run(
            ["hc", "connect"], input='{"jsonrpc":"2.0","id":1,"method":"hello","params":{}}\n',
            capture_output=True, text=True, timeout=5)
    except Exception:
        return False
    return '"id":1' in p.stdout.replace(" ", "")


pytestmark = pytest.mark.skipif(not _hc_repond(), reason="hc absent ou muet")


def test_terminal_list_reel():
    c = HcClient()
    c.start()
    assert c.connected
    res = c.request("terminal.list", {}, timeout=10)
    assert "terminals" in res
