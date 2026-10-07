"""Load a badge app under CPython by supplying the globals the firmware injects.

A badge app is a plain module that reads names which simply do not exist off
the badge -- `badge`, `screen`, `color`, `run` and so on. Providing stand-ins
for them is enough to exec() an app here and drive its logic directly. That
covers codecs, CRCs, framing and state machines. It says nothing about whether
anything is drawn correctly or whether the real timing holds.
"""

import builtins
import importlib
import os
import sys
import time

# MicroPython's tick helpers, which CPython does not have.
if not hasattr(time, "ticks_ms"):
    time.ticks_ms = lambda: int(time.monotonic() * 1000)
    time.ticks_us = lambda: int(time.monotonic() * 1000000)
    time.ticks_diff = lambda a, b: a - b
    time.ticks_add = lambda a, b: a + b

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Every app is a folder here, which is also what the firmware image packs
# into /system/apps.
APPS = os.path.join(REPO, "firmware", "apps")


class FrameLimit(Exception):
    """Raised to break out of an app's own `while True` loop."""


class Stub:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, name):
        return lambda *a, **k: None


class StrictStub:
    """A stub that raises on anything it was not given, as the firmware does.

    `Stub` hands back a callable for any attribute, which is right for `badge.*`
    -- most of that is fire-and-forget and a test does not care. It is wrong for
    anything whose attributes are VALUES, because a name this firmware does not
    have silently becomes a lambda and gets passed into the drawing code.

    `image.CENTER` did exactly that, back when the API notes were read as saying
    the constant might not exist: a missing name became a lambda and was passed
    into the drawing code, and the whole suite passed. Anything modelled as a
    namespace of constants belongs here.
    """

    def __init__(self, name, **kw):
        self.__dict__["_name"] = name
        self.__dict__.update(kw)

    def __getattr__(self, name):
        raise AttributeError("type object %s has no attribute %s"
                             % (self.__dict__["_name"], name))


class FakeBadge(Stub):
    def __init__(self, max_frames=0):
        self.ticks = 0
        self.ticks_delta = 16
        self.light = 0
        self.frames = 0
        self.max_frames = max_frames
        self.queued = []
        self.lights = 0.0
        # Every mode set, in order, and the one in force. default_clear is
        # what run() clears to before update(); None skips the clear.
        self.modes = []
        self.current_mode = None
        self.default_clear = 0      # color.black in make_globals' stub

    def mode(self, *a):
        if a:
            self.modes.append(a[0])
            self.current_mode = a[0]
        return None

    def light_level(self):
        return self.light

    def caselights(self, level):
        self.lights = level

    def press(self, *buttons):
        """Make these buttons read as newly pressed on the next frame only."""
        self.queued = list(buttons)

    def pressed(self, button=None):
        if button is None:
            return list(self.queued)
        return button in self.queued

    def released(self, button=None):
        return [] if button is None else False

    def held(self, button=None):
        # A button newly pressed is down, so held too, as on the badge:
        # pressed is held & changed.
        if button is None:
            return list(self.queued)
        return button in self.queued

    def changed(self, button=None):
        return [] if button is None else False

    def update(self):
        self.tick()

    def tick(self):
        self.queued = []
        self.frames += 1
        self.ticks += self.ticks_delta
        if self.max_frames and self.frames > self.max_frames:
            raise FrameLimit()


class FakeScreen(Stub):
    width = 320
    height = 240

    def __init__(self):
        self.pen = None
        self.font = None
        self.cursor = None
        self.alpha = 255
        self.drawn = []      # every string passed to text(), in order
        self.fills = []      # every pen colour clear() was called with
        self.rects = []      # (pen, x, y, w, h) for every rectangle()
        self.boxes = []      # (message, x, y, w, h) for every text() placement
        self.blits = []      # (source, destination) for every blit()

    def clear(self):
        self.fills.append(self.pen)

    def text(self, message, *a, **k):
        self.drawn.append(message)
        # Where it landed, whichever form was used: (rect) or (x, y). The rect
        # stub is a plain tuple, so both arrive as numbers here.
        if a and isinstance(a[0], tuple) and len(a[0]) == 4:
            x, y, w, h = a[0]
        elif len(a) >= 2:
            x, y = a[0], a[1]
            w, h = self.measure_text(message)
        else:
            x = y = 0
            w, h = self.measure_text(message)
        self.boxes.append((message, x, y, w, h))
        return (x, y, len(str(message)) * 8, 12)

    def measure_text(self, message, *a):
        # Longest line and total height, as the firmware does for a "\n".
        lines = str(message).split("\n")
        return (max(len(line) for line in lines) * 8, 12 * len(lines))

    def blit(self, source, *a):
        # (sprite, x, y) or (sprite, rect). The rect stub is a plain tuple, so
        # record whichever arrived and let the test say which it wanted.
        self.blits.append((source, a[0] if len(a) == 1 else tuple(a)))
        return None

    def rectangle(self, *a):
        if len(a) == 4:
            self.rects.append((self.pen,) + tuple(a))
        return None

    def circle(self, *a):
        return None

    def line(self, *a):
        return None


class FakeImage:
    """What `image.load(path)` returns.

    No spritesheet, no window: this badge's firmware predates both. `image` is
    a StrictStub, so an app reaching for a method this does not have fails in
    the suite the way it failed on the badge.
    """

    loaded = []      # every path load() was asked for, in order

    def __init__(self, width=160, height=90, path=None):
        self.width = width
        self.height = height
        self.alpha = 255
        self.path = path


def make_image_stub(width, height):
    """The injected `image` namespace.

    Strict, because it is a namespace of values: a name this firmware does not
    carry must raise here rather than become a lambda. Two app-launch panics
    came from names that were documented and absent -- `spritesheet` as a
    global, then `image.spritesheet` as a method -- so nothing goes in here
    that has not been seen working on the badge.
    """

    def load(path, *a):
        FakeImage.loaded.append(path)
        return FakeImage(width=width, height=height, path=path)

    return StrictStub("image", load=load)


class FakeWifi(Stub):
    def __init__(self):
        self.connected = False
        self.state = (0, "idle")
        self.calls = []
        self.disconnects = 0
        self.status_raises = False

    def connect(self, ssid=None, key=None, **kw):
        self.calls.append((ssid, key))

    def disconnect(self):
        # The real one drops the module-level WLAN, which is what lets the next
        # connect() honour the SSID it is given and start on a full retry
        # budget. Counted, because "did the app reset the radio" is the
        # behaviour under test.
        self.disconnects += 1
        self.connected = False

    def is_connected(self):
        return self.connected

    def status(self):
        if self.status_raises:
            # What a badge actually did: `NameError: name 'wifi' isn't
            # defined`, raised from inside wifi.py's own status(), at its line
            # 88, on the branch only reached once a connection is live.
            raise NameError("name 'wifi' isn't defined")
        return self.state


class FakeDisplay(Stub):
    """The ST7789 driver. Only backlight() is documented, and it has no getter.

    `display.backlight(level)` takes 0.0-1.0. The firmware sets 230/255 at init
    and nothing else touches it, so an app that raises it owes a restore.
    """

    def __init__(self):
        self.backlights = []

    def backlight(self, level):
        self.backlights.append(level)

    # An app may present a frame itself, inside update(), when it has to read
    # the panel straight after -- share_wifi's settle captures do.
    updates = 0

    def update(self):
        self.updates += 1


class FakeState(Stub):
    """Persistent JSON storage.

    `returns_flag` models what the badge actually does, which is not what the
    API notes say: `load()` returns a bool and fills in the dict it was handed,
    rather than returning the data. Tests run both ways, because a stub that
    only models the documented contract is how the real one got missed --
    `sorted()` on that bool crashed App.__init__ on the badge while 139 tests
    passed.
    """

    def __init__(self, data=None, returns_flag=False):
        self.data = dict(data or {})
        self.returns_flag = returns_flag
        # Flash is ~256 blocks of 4KB and wears out, so "did this write at all"
        # is a thing tests need to assert on, not just what it wrote.
        self.writes = 0
        self.deleted = []
        # One file per name, as the firmware keeps /state/<name>.json. `data`
        # is the first name an app touches -- its config -- so tests written
        # before an app had a second file still read it there; every other
        # name lives in `files`.
        self.primary = None
        self.files = {}

    def _get(self, name):
        if self.primary is None:
            self.primary = name
        return self.data if name == self.primary else self.files.get(name, {})

    def _put(self, name, value):
        if self.primary is None:
            self.primary = name
        if name == self.primary:
            self.data = value
        else:
            self.files[name] = value

    def load(self, name, defaults):
        stored = self._get(name)
        if self.returns_flag:
            defaults.update(stored)
            return bool(stored)
        return dict(stored) if stored else dict(defaults)

    def save(self, name, data):
        self.writes += 1
        self._put(name, dict(data))

    def modify(self, name, data):
        # The firmware's: load, update the top-level keys, save.
        self.writes += 1
        merged = dict(self._get(name))
        merged.update(data)
        self._put(name, merged)

    def delete(self, name):
        self.deleted.append(name)
        self._put(name, {})

    # An app that writes a file itself rather than through State.save() -- the
    # firmware's swallows OSError -- is pointed here instead of at the real
    # filesystem. `fail_writes` makes it raise the way flash can.
    fail_writes = False

    # `fail_deletes` leaves the file in place, as a delete flash refused
    # would: the app's delete_measurements() then finds it still there.
    fail_deletes = False

    def delete_file(self, name):
        """True if the file is gone afterwards, as delete_measurements()
        reports."""
        if self.fail_deletes:
            return name not in self.files and name != self.primary
        self.files.pop(name, None)
        return True

    def write_file(self, name, data):
        if self.fail_writes:
            raise OSError(28, "No space left on device")
        self.writes += 1
        self._put(name, dict(data))


class FakeSecrets:
    """`modules/common/secrets.py`: the badge's own provisioning.

    A real module, not an injected global -- it imports /secrets.py and falls
    back to /system/secrets.py, copying the contents to module scope. Both
    fields are "" on a badge nobody has configured, which is the default the
    firmware ships.
    """

    def __init__(self, ssid="", password="", region="eu", timezone=0):
        self.WIFI_SSID = ssid
        self.WIFI_PASSWORD = password
        self.REGION = region
        self.TIMEZONE = timezone

    def require(self, *keys):
        # The real one calls fatal_error(), which resets the badge. Nothing in
        # an app should reach for it, so make that loud rather than plausible.
        raise AssertionError("secrets.require() resets the badge on a miss")


class FakeNetwork:
    """Stands in for `from network import WLAN`."""

    class WLAN:
        IF_STA = 0
        results = []

        def __init__(self, _iface=0):
            pass

        def active(self, _on):
            return True

        def scan(self):
            return list(FakeNetwork.WLAN.results)


def make_globals(max_frames=0, networks=None, run_frames=0,
                 state_returns_flag=False, alignment=False,
                 image_size=(160, 90)):
    badge = FakeBadge(max_frames=max_frames)
    screen = FakeScreen()

    def run(update):
        # The real run() clears, calls update(), presents and polls buttons.
        for _ in range(run_frames):
            update()
            badge.tick()

    namespace = {
        "badge": badge,
        "screen": screen,
        "run": run,
        # display is injected too, and `display.backlight(0.0-1.0)` is the part
        # the app uses: it raises the panel for the length of a transfer.
        "display": FakeDisplay(),
        # rgb() has to return something distinguishable. Collapsing it to 0
        # made the mid transmit field equal to black, which is exactly the
        # confusion the field-colour tests exist to catch.
        "color": Stub(black=0, white=1, grey=2, smoke=3, navy=4, blue=5,
                      cyan=6, red=7, green=8, yellow=9, orange=10, lime=11,
                      grape=12, brown=13, taupe=14, latte=15,
                      rgb=lambda *a: ("rgb",) + tuple(a),
                      hsv=lambda *a: ("hsv",) + tuple(a),
                      oklch=lambda *a: ("oklch",) + tuple(a)),
        # Strict, so reaching through `image` for a name this firmware does not
        # carry fails here instead of on the badge. The alignment and overflow
        # constants live on it when `alignment=True`, because that is where the
        # firmware puts them before hoisting copies into builtins.
        "image": make_image_stub(*image_size),
        # `spritesheet` is deliberately absent, and so is `image.spritesheet`.
        # Both are documented, both are missing on this badge, and each cost a
        # flash-and-reboot cycle to find out:
        #
        #   NameError: name 'spritesheet' isn't defined
        #   AttributeError: 'image' object has no attribute 'spritesheet'
        #
        # The suite passed through the first one because this harness was
        # injecting the global. Do not add either back to make a test pass --
        # run probe_badge.py (in the tufty_badge repo, rampage/tools/) on the badge and add what it reports.
        "font": Stub(smart="smart", memo="memo", sins="sins",
                     load=lambda p: "loaded"),
        "rect": lambda *a: tuple(a),
        "vec2": lambda *a: tuple(a),
        "State": FakeState(networks, returns_flag=state_returns_flag),
        "HIRES": 1, "LORES": 2, "VSYNC": 4, "DITHER": 8,
        "BUTTON_A": "A", "BUTTON_B": "B", "BUTTON_C": "C",
        "BUTTON_UP": "UP", "BUTTON_DOWN": "DOWN", "BUTTON_HOME": "HOME",
        "clamp": lambda v, lo, hi: max(lo, min(hi, v)),
        "file_exists": lambda p: False,
        "is_dir": lambda p: False,
        "free": lambda: 8 << 20,
    }
    if alignment:
        # Alignment and overflow constants. `alignment=False` is the DEFAULT
        # because it is what the badge does: these exist under no spelling
        # there -- not `image.CENTER`, not bare `CENTER` -- and both raised on
        # the first frame. An app must draw without them, so that is the case
        # the suite runs by default; alignment=True covers a firmware that has
        # them, since the app still prefers the native path when present.
        constants = {
            "LEFT": 0, "CENTER": 1, "RIGHT": 2,
            "TOP": 0, "MIDDLE": 1, "BOTTOM": 2,
            "CLIP": 0, "ELLIPSES": 1,
            "OFF": 0, "X2": 1, "X4": 2,
        }
        namespace.update(constants)
        namespace["image"] = StrictStub("image", **constants)
    return namespace


def load_module(app, module):
    """Import a pure module from an app folder, with no stubs at all.

    Logic that does not touch the badge should live in its own module so it can
    be imported like this -- `firmware/apps/share_wifi/protocol.py` is the example. Tests for
    it need none of the machinery above.
    """
    path = os.path.join(APPS, app)
    if path not in sys.path:
        sys.path.insert(0, path)
    return importlib.import_module(module)


def load_app(name, max_frames=0, networks=None, run_frames=0,
             scan_results=None, state_returns_flag=False, alignment=False,
             secrets=None, image_size=(160, 90), modules=None):
    """exec() an app's __init__.py with stubbed globals and return them.

    Only needed for the parts that genuinely touch the badge. The namespace
    returned includes anything the app imported, so an app that pulls in its own
    pure module still exposes it here.
    """
    if scan_results is not None:
        FakeNetwork.WLAN.results = scan_results
    sys.modules["network"] = FakeNetwork

    # `wifi` is a real module in the firmware (modules/common/wifi.py), not an
    # injected global, so apps import it. Register a stand-in to import.
    fake_wifi = FakeWifi()
    sys.modules["wifi"] = fake_wifi

    # Same story as wifi: a firmware module the app imports, not a global.
    # Unset means an unprovisioned badge, which is what ships.
    fake_secrets = secrets if secrets is not None else FakeSecrets()
    sys.modules["secrets"] = fake_secrets

    # Stand-ins for the app's own sibling modules. An app imports those as
    # plain top-level names, because the launcher puts its folder on sys.path,
    # so overriding one is a matter of getting in first.
    for module_name, module in (modules or {}).items():
        sys.modules[module_name] = module

    # An app folder is a package, but exec() gives no package context, so the
    # app's relative import falls back to an absolute one. Make that resolve.
    app_dir = os.path.join(APPS, name)
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    path = os.path.join(app_dir, "__init__.py")
    with open(path) as handle:
        source = handle.read()

    injected = make_globals(max_frames=max_frames, networks=networks,
                            state_returns_flag=state_returns_flag,
                            run_frames=run_frames, alignment=alignment,
                            image_size=image_size)

    # The firmware injects its globals into `builtins`, never into the app's
    # own module namespace: badgeware/__init__.py is a run of
    # `builtins.CENTER = image.CENTER` and `setattr(builtins, k, v)`. Model
    # that exactly, by handing exec() a __builtins__ of its own rather than
    # dropping the stand-ins into the module globals.
    #
    # The difference is not academic. An app that probes for a firmware
    # difference with `globals().get("CENTER")` gets None on the badge whether
    # the constant exists or not, and a harness that put CENTER in the module
    # namespace answered yes and hid it. share_wifi's alignment probe was that,
    # and it was dead code for as long as this harness disagreed with the badge.
    app_builtins = dict(vars(builtins))
    app_builtins.update(injected)

    # Keep an app's print() out of the test runner's own output, and let a
    # test read it back as `printed_lines`.
    printed = []
    app_builtins["print"] = lambda *a: printed.append(
        " ".join(str(item) for item in a))

    # exec() has no package context, so a relative import raises. Supplying
    # these makes it raise ImportError -- which the app's fallback catches --
    # rather than KeyError, which it does not.
    namespace = {"__name__": name, "__package__": None,
                 "__builtins__": app_builtins}
    try:
        exec(compile(source, name, "exec"), namespace)
    except FrameLimit:
        pass

    # Hand the stand-ins back to the caller, which needs `screen`, `badge` and
    # the rest to drive the app. They were never in the app's namespace while
    # it ran, which is the point above; setdefault so nothing the app defined
    # for itself is overwritten here.
    for key, value in injected.items():
        namespace.setdefault(key, value)
    # share_wifi writes its measurements file directly so it can see OSError;
    # keep that off the host's filesystem.
    if "write_measurements" in namespace:
        state = injected["State"]
        namespace["write_measurements"] = (
            lambda data: state.write_file("measurements", data))
    if "delete_measurements" in namespace:
        state = injected["State"]
        namespace["delete_measurements"] = (
            lambda: state.delete_file("measurements"))
    namespace.setdefault("wifi", fake_wifi)
    namespace.setdefault("secrets", fake_secrets)
    namespace.setdefault("printed_lines", printed)
    return namespace
