# This file does one thing: it starts multiproxy.py with the same arguments.
#
# SkyrimNet MultiProxy used to be proxy.py (with proxy.ini and proxy.log beside it). It was renamed to multiproxy.py (2026-10-01) so it
# can't be confused with the original proxy or its forks. Existing players' SkyrimNet MultiProxy mod settings still say "proxy.py" as the
# script to start, and they must keep working without anyone editing a setting: so this small file stays, and just hands over.
# (tests/test_proxy_shim.py checks it.) Nothing else belongs here: change multiproxy.py.
import os
import runpy
import sys

if __name__ == "__main__":
    _real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "multiproxy.py")
    sys.argv[0] = _real
    runpy.run_path(_real, run_name="__main__")
