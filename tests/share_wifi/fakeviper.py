"""Loads firmware/apps/share_wifi/fastadc.py under CPython, its viper kernels
running as plain Python: a stub `micropython` whose decorators do nothing,
stub `machine` and `rp2` modules, and `ptr8`/`ptr16`/`ptr32`/`uint` as the
identity -- so a kernel handed a list where the badge hands an address
indexes the list. That checks the kernels' logic, not viper: see
test_fastadc_kernels.py for the runs that check viper itself.

Not a test: helpers beside the tests are not named test_*.
"""

import builtins
import importlib
import os
import sys
import types

# Whatever imports this is about to import modules from firmware/apps/,
# and everything under firmware/ is packed onto the badge: no __pycache__.
sys.dont_write_bytecode = True

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "firmware", "apps", "share_wifi")


def _identity(value):
    return value


def load_fastadc():
    """fastadc, imported fresh with the stubs in place."""
    micropython = types.ModuleType("micropython")
    micropython.viper = _identity
    micropython.native = _identity
    machine = types.ModuleType("machine")
    machine.mem32 = {}
    machine.mem16 = {}
    rp2 = types.ModuleType("rp2")
    saved = {}
    for name, module in (("micropython", micropython), ("machine", machine),
                         ("rp2", rp2)):
        saved[name] = sys.modules.get(name)
        sys.modules[name] = module
    for name in ("ptr8", "ptr16", "ptr32", "uint"):
        setattr(builtins, name, _identity)
    if APP not in sys.path:
        sys.path.insert(0, APP)
    try:
        sys.modules.pop("fastadc", None)
        return importlib.import_module("fastadc")
    finally:
        sys.modules.pop("fastadc", None)
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
