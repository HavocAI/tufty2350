"""Dev Mode inside share_wifi, under the harness: revealed by holding C, its
three items, the USB dev link's screen -- Ctrl-C off and back on, B, the
instant built-ins, a subtool's save landing in the measurements file --
and the autorun a host launches it with. The tools themselves are dev link
subtools, tested in test_devtools.py."""

import os
import sys
import tempfile
import types

from devlink_fakes import FakePort, Host, dl, streams
from harness import load_app
import held

LINK_MODULES = ("devlink", "devserver", "usbport")


class LinkPort(FakePort):
    """FakePort with the open/close the app calls, recording Ctrl-C."""

    def __init__(self):
        FakePort.__init__(self)
        self.ctrl_c = True
        self.opens = 0

    def open(self):
        self.ctrl_c = False
        self.opens += 1
        self.inbox = bytearray()        # drained

    def close(self):
        self.ctrl_c = True


def link_app(files=None, held=None):
    """The app with a fake USB port and a temp /devtests. `held` is a set
    of buttons the fake badge reports held, frame after frame."""
    port = LinkPort()
    module = types.SimpleNamespace(badge_port=lambda pin: port)
    namespace = load_app("share_wifi", modules={"usbport": module})
    root = tempfile.mkdtemp(prefix="devtests")
    for name, text in (files or {}).items():
        with open(os.path.join(root, name), "w") as handle:
            handle.write(text)
    namespace["LINK_ROOT"] = root
    badge = namespace["badge"]
    badge.usb_connected = lambda: True
    if held is not None:
        pressed = badge.held
        badge.held = lambda button=None: button in held or pressed(button)
    return namespace, namespace["app"], port


def frame(namespace, *buttons):
    badge, screen = namespace["badge"], namespace["screen"]
    screen.drawn = []
    badge.press(*buttons)
    namespace["app"].frame()
    badge.tick()
    return screen.drawn


def hold_c(namespace, ms):
    """C held for `ms` -- the first frame that sees it down to the last --
    a frame (badge.ticks_delta) at a time."""
    held = namespace["_held"]
    held.add("C")
    delta = namespace["badge"].ticks_delta
    for _ in range(-(-ms // delta) + 1):
        frame(namespace)
    held.discard("C")
    frame(namespace)                # released


def dev_app(files=None):
    held = set()
    namespace, app, port = link_app(files, held)
    namespace["_held"] = held
    return namespace, app, port


def to_dev(namespace):
    hold_c(namespace, namespace["DEV_HOLD_MS"])
    app = namespace["app"]
    assert app.menu_items[-1] == namespace["DEV_MODE"]
    frame(namespace, "A")
    assert app.screen == namespace["DEV"]


def open_link(namespace, app, port):
    to_dev(namespace)
    for _ in range(namespace["LINK_INDEX"]):
        frame(namespace, "DOWN")
    frame(namespace, "A")
    assert app.screen == namespace["DEV_LINK"]
    host = Host(app.link, port)
    host.send({"t": "hello", "id": "n", "proto": dl.PROTO})
    frame(namespace)
    assert host.frames()[0]["t"] == "hello"
    return host


def ask(namespace, host, obj):
    host.send(obj)
    frame(namespace)
    return host.frames()


# --- revealing Dev Mode -------------------------------------------------------------

def test_dev_mode_is_hidden_until_c_is_held_five_seconds():
    namespace, app, port = dev_app()
    assert namespace["DEV_HOLD_MS"] == 5000
    frame(namespace, "C")
    assert namespace["DEV_MODE"] not in app.menu_items, "a press is not a hold"
    hold_c(namespace, 4900)
    assert namespace["DEV_MODE"] not in app.menu_items
    hold_c(namespace, 4900)
    assert namespace["DEV_MODE"] not in app.menu_items, \
        "two short holds are not one long one"
    hold_c(namespace, 5000)
    assert app.menu_items[-1] == namespace["DEV_MODE"]
    assert app.cursor == len(app.menu_items) - 1
    hold_c(namespace, 5000)
    assert app.menu_items.count(namespace["DEV_MODE"]) == 1


def test_dev_mode_has_its_three_items_and_b_goes_back():
    namespace, app, port = dev_app()
    to_dev(namespace)
    assert namespace["DEV_ITEMS"] == ("Display Saved Measurements",
                                      "Clear Saved Measurements",
                                      "USB Dev Link")
    drawn = " ".join(str(d) for d in frame(namespace))
    for item in namespace["DEV_ITEMS"]:
        assert item in drawn
    frame(namespace, "B")
    assert app.screen == namespace["MAIN"]


def test_clear_deletes_the_file_and_says_so():
    namespace, app, port = dev_app()
    namespace["State"].files["measurements"] = {"mirror": {}}
    to_dev(namespace)
    frame(namespace, "DOWN")
    frame(namespace, "A")
    assert "measurements" not in namespace["State"].files
    assert app.screen == namespace["DEV_DONE"]
    assert namespace["CLEARED"] in " ".join(str(d) for d in frame(namespace))
    frame(namespace, "A")
    assert app.screen == namespace["DEV"]
    assert app.cursor == namespace["CLEAR_INDEX"]


# --- the link ---------------------------------------------------------------------

def test_opening_takes_the_usb_and_b_gives_it_back():
    namespace, app, port = dev_app()
    host = open_link(namespace, app, port)
    assert not port.ctrl_c and port.opens == 1
    drawn = " ".join(str(d) for d in frame(namespace))
    assert "USB dev link" in drawn and "Host: connected" in drawn
    assert "B - Exit" in drawn
    frame(namespace, "B")
    assert port.ctrl_c, "Ctrl-C back on"
    assert app.screen == namespace["DEV"]
    assert app.cursor == namespace["LINK_INDEX"]
    assert app.link is None
    assert host.frames()[-1] == {"t": "bye", "reason": "B", "rid": None}


def test_home_gives_the_usb_back_too():
    namespace, app, port = dev_app()
    open_link(namespace, app, port)
    namespace["on_exit"]()
    assert port.ctrl_c


def test_the_screen_draws_without_alignment_constants():
    namespace, app, port = dev_app()           # the harness has none
    to_dev(namespace)
    for _ in range(namespace["LINK_INDEX"]):
        frame(namespace, "DOWN")
    frame(namespace, "A")
    drawn = " ".join(str(d) for d in frame(namespace))
    assert "waiting (or power only)" in drawn


def test_the_link_lists_two_built_ins_and_the_subtools():
    namespace, app, port = dev_app({"hi.py": "def run(api, args):\n  pass\n"})
    host = open_link(namespace, app, port)
    tools = ask(namespace, host, {"t": "list"})[0]["tools"]
    assert [(t["name"], t["builtin"]) for t in tools] == [
        ("measurements", True), ("clear", True), ("hi", False)]


def test_run_clear_deletes_and_reports():
    namespace, app, port = dev_app()
    namespace["State"].files["measurements"] = {"mirror": {}}
    host = open_link(namespace, app, port)
    got = ask(namespace, host, {"t": "run", "tool": "clear", "args": []})
    assert [f["t"] for f in got] == ["started", "result"]
    assert got[-1]["saved"] is True and got[-1]["text"] == namespace["CLEARED"]
    assert "measurements" not in namespace["State"].files
    assert app.screen == namespace["DEV_LINK"]


def test_run_measurements_sends_the_file():
    namespace, app, port = dev_app()
    namespace["State"].files["measurements"] = {"mirror": {"dark": 1}}
    host = open_link(namespace, app, port)
    got = ask(namespace, host, {"t": "run", "tool": "measurements"})
    data = list(streams(got).values())[0][1]
    assert data == b'{"mirror": {"dark": 1}}'
    assert got[-1]["t"] == "result"


def test_a_subtools_saves_merge_into_the_measurements_file():
    namespace, app, port = dev_app({"m.py": (
        "def run(api, args):\n"
        "    return api.save({args[0]: {'n': 1}}, 'mirror')\n")})
    namespace["State"].files["measurements"] = {
        "mirror": {"dark": {"n": 0}}, "frame_probe": {"x": 1}}
    host = open_link(namespace, app, port)
    got = ask(namespace, host, {"t": "run", "tool": "m", "args": ["lit"]})
    assert got[-1]["t"] == "result"
    saved = namespace["State"].files["measurements"]
    assert saved == {"mirror": {"dark": {"n": 0}, "lit": {"n": 1}},
                     "frame_probe": {"x": 1}}
    ask(namespace, host, {"t": "run", "tool": "m", "args": ["dark"]})
    assert namespace["State"].files["measurements"]["mirror"]["dark"] == \
        {"n": 1}, "a condition run again replaces its own"


def test_a_save_that_fails_says_so():
    namespace, app, port = dev_app({"m.py": (
        "def run(api, args):\n"
        "    return {'ok': api.save({'a': 1})}\n")})

    def fail(data):
        raise OSError(28, "no space")
    namespace["write_measurements"] = fail
    host = open_link(namespace, app, port)
    got = ask(namespace, host, {"t": "run", "tool": "m"})
    data = list(streams(got).values())[0][1]
    assert data == b'{"ok": false}'


def test_a_custom_run_disarms_b_and_restores_the_panel():
    namespace, app, port = dev_app({"hi.py": (
        "def run(api, args):\n"
        "    api.status('working')\n"
        "    api.backlight(0.0)\n"
        "    api.send('hi')\n")})
    host = open_link(namespace, app, port)
    got = ask(namespace, host, {"t": "run", "tool": "hi"})
    assert got[-1]["t"] == "result" and port.arms == port.disarms == 1
    assert not app.link_b_armed
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    assert namespace["badge"].default_clear == namespace["color"].black


def test_a_launch_and_a_transfer_never_load_the_link():
    # The link's modules stay out of a transfer's heap [measured-v2].
    saved = {name: sys.modules.pop(name) for name in LINK_MODULES
             if name in sys.modules}
    try:
        namespace = load_app("share_wifi",
                             networks={"HomeNet": "hunter2!!"})
        app = namespace["app"]
        readers = namespace["readers"]
        namespace["_READERS"]["ring"] = readers.RingReader(
            held.ListRing(4096, 20))
        namespace["_READERS"]["note"] = None
        from test_app import give_pacer
        give_pacer(namespace)
        namespace["TRANSFER_PASSES"] = 1
        frame(namespace)
        for _ in range(4):                      # Share, a network, the line
            if app.transfer is not None:
                break
            frame(namespace, "A")
        assert app.transfer is not None, app.screen
        for _ in range(20):
            frame(namespace)
        loaded = [name for name in LINK_MODULES if name in sys.modules]
        assert not loaded, loaded
    finally:
        sys.modules.update(saved)


def test_opening_the_link_loads_it():
    saved = {name: sys.modules.pop(name) for name in LINK_MODULES
             if name in sys.modules}
    try:
        namespace, app, port = dev_app()
        assert "devserver" not in sys.modules
        open_link(namespace, app, port)
        assert "devserver" in sys.modules and "devlink" in sys.modules
    finally:
        for name in LINK_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# --- autorun ----------------------------------------------------------------------

def autorun_app(value):
    import builtins
    builtins.DEV_AUTORUN = value
    try:
        return link_app()
    finally:
        del builtins.DEV_AUTORUN


def test_from_the_menu_update_never_quits():
    namespace, app, port = link_app()
    for _ in range(5):
        assert namespace["update"]() is None
    assert app.autorun == ""


def test_under_autorun_link_opens_the_link_and_quits_when_it_closes():
    import builtins
    builtins.DEV_AUTORUN = "link"
    try:
        namespace, app, port = link_app()
        assert namespace["update"]() is None
    finally:
        del builtins.DEV_AUTORUN
    assert app.screen == namespace["DEV_LINK"] and not port.ctrl_c
    namespace["badge"].tick()
    assert namespace["update"]() is None
    namespace["badge"].press("B")
    assert namespace["update"]() is None
    assert app.link is None and port.ctrl_c
    assert namespace["update"]() is True


def test_an_autorun_the_app_does_not_know_quits_at_once():
    import builtins
    for value in ("probe", "mirror_dark", "fast"):
        builtins.DEV_AUTORUN = value
        try:
            namespace, app, port = link_app()
            assert namespace["update"]() is True, value
        finally:
            del builtins.DEV_AUTORUN
        assert app.link is None


# --- stored dev tools, run from Dev Mode ------------------------------------------

MENU_TOOL = (
    "MENU = (('say one', ['1']), ('say two', ['2']))\n"
    "def run(api, args):\n"
    "    api.backlight(0.0)\n"
    "    api.send({'x': 1})\n"
    "    return api.save({args[0]: 'said'}, 'say')\n")
NO_MENU_TOOL = "def run(api, args):\n    pass\n"


def test_stored_tools_with_a_menu_appear_in_dev_mode_after_the_three():
    namespace, app, port = dev_app({"say.py": MENU_TOOL,
                                    "plain.py": NO_MENU_TOOL,
                                    "broken.py": "def run(:\n"})
    to_dev(namespace)
    assert app.dev_items == list(namespace["DEV_ITEMS"]) + ["say one",
                                                            "say two"]
    drawn = " ".join(str(d) for d in frame(namespace))
    assert "say one" in drawn and "say two" in drawn


def test_a_tool_runs_with_no_host_and_saves_its_result():
    namespace, app, port = dev_app({"say.py": MENU_TOOL})
    to_dev(namespace)
    for _ in range(len(namespace["DEV_ITEMS"]) + 1):     # "say two"
        frame(namespace, "DOWN")
    frame(namespace, "A")
    assert app.screen == namespace["DEV_DONE"]
    assert namespace["State"].files["measurements"] == {"say": {"2": "said"}}
    assert port.writes == 0 and port.opens == 0, "no USB without a host"
    assert namespace["display"].backlights[-1] == namespace["BACKLIGHT_NORMAL"]
    assert "Done" in " ".join(str(d) for d in frame(namespace))
    frame(namespace, "A")
    assert app.screen == namespace["DEV"]
    assert app.cursor == len(namespace["DEV_ITEMS"]) + 1


def test_a_tool_that_raises_says_so_and_does_not_reset():
    namespace, app, port = dev_app({"bad.py": (
        "MENU = (('bad', []),)\n"
        "def run(api, args):\n"
        "    raise ValueError('nope')\n")})
    to_dev(namespace)
    for _ in range(len(namespace["DEV_ITEMS"])):
        frame(namespace, "DOWN")
    frame(namespace, "A")
    assert app.done_message == "ValueError: nope"


def test_clear_deletes_the_measurements_and_every_stored_tool():
    namespace, app, port = dev_app({"say.py": MENU_TOOL})
    lib = os.path.join(namespace["LINK_ROOT"], "lib")
    os.mkdir(lib)
    with open(os.path.join(lib, "kit.py"), "w") as handle:
        handle.write("X = 1\n")
    namespace["State"].files["measurements"] = {"mirror": {}}
    to_dev(namespace)
    frame(namespace, "DOWN")
    frame(namespace, "A")                       # Clear Saved Measurements
    assert "measurements" not in namespace["State"].files
    assert os.listdir(namespace["LINK_ROOT"]) == ["lib"]
    assert os.listdir(lib) == []
    assert app.dev_items == list(namespace["DEV_ITEMS"])
    assert namespace["CLEARED"] in " ".join(str(d) for d in frame(namespace))


def test_the_links_clear_deletes_the_stored_tools_too():
    namespace, app, port = dev_app({"say.py": MENU_TOOL})
    host = open_link(namespace, app, port)
    got = ask(namespace, host, {"t": "run", "tool": "clear"})
    assert got[-1]["saved"] is True
    assert os.listdir(namespace["LINK_ROOT"]) == []


# --- B leaves the app ---------------------------------------------------------

def test_b_on_the_main_menu_leaves_the_app():
    namespace, app, port = dev_app()
    namespace["badge"].press("B")
    assert namespace["update"]() is True
    namespace, app, port = dev_app()
    open_link(namespace, app, port)
    frame(namespace, "B")                       # the link closes: Dev Mode
    frame(namespace, "B")                       # main menu
    assert app.screen == namespace["MAIN"]
    namespace["badge"].press("B")
    assert namespace["update"]() is True and port.ctrl_c
