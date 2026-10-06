"""Every piece of text fits the area it is drawn in, in the badge's own font.

`screen.text(text, rect)` clips to the rect, so text that does not fit is not
an error anywhere -- it is just missing on the badge. The stub screen measures
every character as 8 px and cannot see that, so this measures with the real
glyph widths from romfs/fonts/smart.ppf (see tests/fontmetrics.py).

Coverage comes from re-running every test in test_app.py -- which between them
reach every screen -- with the real font in place and a check on every draw,
plus long SSIDs of the widest glyphs. Three things are checked on each draw:

- A boxed draw: every line fits the box's width under both of the firmware's
  space widths (measure_text's and wrapped layout's), so the firmware has
  nothing left to wrap, and the lines fit the box's height.
- A positioned draw: the line ends on the screen.
- No fixed string from the app's source -- an instruction, a help line, a
  message -- is ever cut with "...". Only data (an SSID, a reading) may be.
"""

import ast
import os
import types

import fontmetrics
import test_app
from harness import APPS, load_app

FONT = fontmetrics.PixelFont("smart")
SOURCE = os.path.join(APPS, "share_wifi", "__init__.py")


def fixed_strings():
    """Every string literal in the app that a screen might show."""
    tree = ast.parse(open(SOURCE).read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
            if len(text) > 3 and " " in text and "%" not in text:
                out.add(text)
    return out


FIXED = fixed_strings()


def line_widths(line):
    """(measure_text's width, wrapped layout's width) of one line."""
    words = line.split(" ")
    glyphs = sum(FONT.span(w) for w in words)
    gaps = len(words) - 1
    return (glyphs + gaps * (FONT.max_width // 3),
            glyphs + gaps * FONT.height / 3.0)


class Checker:
    def __init__(self):
        self.problems = []
        self.where = ""

    def install(self, namespace):
        screen = namespace["screen"]
        screen.measure_text = lambda message, *a: FONT.measure(message)
        real_text = screen.text
        width = screen.width

        def text(message, *args, **kwargs):
            message = str(message)
            if args and isinstance(args[0], tuple) and len(args[0]) == 4:
                self.check_box(message, args[0])
            elif len(args) >= 2:
                x = args[0]
                for line in message.split("\n"):
                    if x + max(line_widths(line)) > width:
                        self.problem("runs off the screen at x=%d" % x, line)
            return real_text(message, *args, **kwargs)
        screen.text = text

        real_draw = namespace["draw_text"]

        def draw_text(text, box, center=False, middle=False):
            source = str(text)
            laid = namespace["layout_text"](source, box[2], box[3])
            if source in FIXED and laid.replace("\n", " ") != source.replace(
                    "\n", " "):
                self.problem("a fixed string was cut to fit %s" % (box,),
                             source)
            return real_draw(text, box, center, middle)
        namespace["draw_text"] = draw_text
        return namespace

    def check_box(self, message, box):
        _x, _y, width, height = box
        lines = message.split("\n")
        if len(lines) * FONT.height > height:
            self.problem("%d lines in a box %d high" % (len(lines), height),
                         message)
        for line in lines:
            narrow, wrapped = line_widths(line)
            if max(narrow, wrapped) > width:
                self.problem("%d px in a box %d wide" % (max(narrow, wrapped),
                                                         width), line)

    def problem(self, why, text):
        entry = "%s: %r -- %s" % (self.where, text, why)
        if entry not in self.problems:
            self.problems.append(entry)


def test_every_screen_in_every_test_fits_the_real_font():
    checker = Checker()
    original = test_app.load_app

    def checked(*args, **kwargs):
        return checker.install(original(*args, **kwargs))
    test_app.load_app = checked
    failures = []
    try:
        for name in sorted(dir(test_app)):
            test = getattr(test_app, name)
            if not name.startswith("test_") or not isinstance(
                    test, types.FunctionType):
                continue
            checker.where = name
            try:
                test()
            except Exception as error:      # noqa: BLE001
                failures.append("%s: %s" % (name, error))
    finally:
        test_app.load_app = original
    # The real font changes where lines break, not what the app does: every
    # test must still pass, or its screens were not all reached.
    assert not failures, "app tests failed with the real font:\n" + \
        "\n".join(failures)
    assert not checker.problems, "\n".join(checker.problems)


def test_the_widest_ssids_fit_every_list_and_message():
    # 32 octets is the longest SSID; '@', 'W' and 'M' are the font's widest.
    names = ["@" * 32, "W" * 32, "MwMw " * 6 + "MW", "Café " + "W" * 27]
    networks = dict((name, "password123") for name in names)
    checker = Checker()
    namespace = checker.install(load_app("share_wifi", networks=networks,
                                         alignment=True))
    app = namespace["app"]
    frame = test_app.frame
    checker.where = "lists"
    frame(namespace, "A")                       # Share: the list
    frame(namespace)
    frame(namespace, "B")
    frame(namespace, "DOWN")
    frame(namespace, "DOWN")
    frame(namespace, "A")                       # Connect: the list
    frame(namespace)
    for name in names:
        checker.where = "connect %r" % name[:8]
        app.start_connect(name)
        frame(namespace)                        # Connecting
        app.status_readable = False
        frame(namespace)
        for message in ("Incorrect password. (-3)",
                        "Access point %s not found. (-2)" % name,
                        "Timed out\nWi-Fi status unavailable (1)"):
            app.message = "%s\n\n%s" % (name, message)
            app.error = message
            app.screen = namespace["CONNECT_RESULT"]
            frame(namespace)
        app.message = "Connected to\n" + name
        app.error = None
        app.screen = namespace["CONNECT_RESULT"]
        frame(namespace)
    assert not checker.problems, "\n".join(checker.problems)


def test_the_check_catches_text_that_does_not_fit():
    # The check itself: a box too small for its message is reported.
    checker = Checker()
    checker.where = "self-test"
    checker.check_box("Turn the lights off.\nHold the badge", (0, 0, 60, 16))
    assert len(checker.problems) >= 2
