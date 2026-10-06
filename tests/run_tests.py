#!/usr/bin/env python3
"""Run the badge test suite.

    python3 tests/run_tests.py              # everything
    python3 tests/run_tests.py share_wifi   # one app
    python3 tests/run_tests.py -v           # list every test

No dependencies. A test is a `test_*` function in a `test_*.py` file under
`tests/<app>/`, where `<app>` matches a folder in `firmware/apps/`. Nothing
needs registering -- a folder here is found.

The tests live outside `firmware/` because everything under it is packed onto
the badge. The harness sits here rather than in any one app's folder, because
every app shares it.
"""

import os
import sys
import traceback

# Tests import from firmware/apps/, and everything under firmware/ is packed
# onto the badge -- so leave no __pycache__ behind there.
sys.dont_write_bytecode = True

TESTS = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, TESTS)       # so app tests can `from harness import ...`


def app_test_dirs():
    """Every `tests/<app>/`, in name order."""
    for entry in sorted(os.listdir(TESTS)):
        if entry.startswith((".", "_")):
            continue
        path = os.path.join(TESTS, entry)
        if os.path.isdir(path):
            yield entry, path


def _add_test_dirs():
    # Each app's test directory, so tests there can import helpers that live
    # beside them -- tests/share_wifi/simulator.py, for instance.
    for _, path in app_test_dirs():
        sys.path.insert(0, path)


_add_test_dirs()


def discover(only=None):
    for app, path in app_test_dirs():
        if only and app != only:
            continue
        for name in sorted(os.listdir(path)):
            if name.startswith("test_") and name.endswith(".py"):
                yield app, os.path.join(path, name)


def load(path):
    namespace = {"__file__": path, "__name__": os.path.basename(path)[:-3]}
    with open(path) as handle:
        exec(compile(handle.read(), path, "exec"), namespace)
    return namespace


def main(argv):
    verbose = "-v" in argv
    only = next((a for a in argv if not a.startswith("-")), None)

    passed = []
    failed = []
    for app, path in discover(only):
        module = os.path.basename(path)[:-3]
        try:
            namespace = load(path)
        except Exception:
            failed.append(("%s/%s" % (app, module), traceback.format_exc()))
            continue

        for name in sorted(namespace):
            if not name.startswith("test_") or not callable(namespace[name]):
                continue
            label = "%s/%s.%s" % (app, module, name)
            try:
                namespace[name]()
                passed.append(label)
                if verbose:
                    print("  pass  %s" % label)
            except Exception:
                failed.append((label, traceback.format_exc()))
                print("  FAIL  %s" % label)

    print("\n%d passed, %d failed" % (len(passed), len(failed)))
    for label, tb in failed:
        print("\n" + "=" * 70 + "\n%s\n%s" % (label, tb.rstrip()))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
