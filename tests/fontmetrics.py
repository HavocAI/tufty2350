"""The badge's own text layout, rebuilt from its font files, for fit tests.

`screen.text(s, rect)` on the badge word-wraps inside the rect and CLIPS to it,
so a message with more lines than the rect holds simply loses its last lines,
and a word wider than the rect is cut. Nothing reports either. The stub screen
in harness.py measures every character as 8 px, which cannot see it -- so this
reads the real glyph widths out of romfs/fonts/*.ppf and lays text out the way
the firmware does.

From pimoroni/picovector (pixel_font.cpp) and picovector-micropython
(native/pixel_font_native.cpp, native/image_native.cpp), as of 2026-10:

- A .ppf is "ppf!", u16 flags, u32 glyph count, u16 max glyph width, u16
  height, a 32-byte name, then per glyph a u32 codepoint and a u16 width, all
  big-endian; bitmaps follow.
- A run of glyphs is as wide as the sum of (glyph width + 1).
- In wrapped layout a space is a third of the font height (measure(" ") is 0,
  so image_layout_text falls back to height / 3); words break only at spaces
  and "\\n"; the first word of a line is placed even if it is wider than the
  rect; lines advance by the font height.
"""

import os
import struct

FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "romfs", "fonts")


class PixelFont:
    def __init__(self, name):
        with open(os.path.join(FONTS, name + ".ppf"), "rb") as handle:
            data = handle.read()
        assert data[:4] == b"ppf!", name
        _flags, count, self.max_width, self.height = struct.unpack(
            ">HIHH", data[4:14])
        self.name = data[14:46].split(b"\0")[0].decode()
        self.widths = {}
        offset = 46
        for _ in range(count):
            codepoint, width = struct.unpack(">IH", data[offset:offset + 6])
            self.widths[codepoint] = width
            offset += 6

    def span(self, text):
        """Width of a run with no spaces: each glyph's width plus one."""
        return sum(self.widths[ord(c)] + 1 for c in text if ord(c) in self.widths)

    def measure(self, text):
        """`measure_text(text)` unwrapped: spaces are max width // 3 here."""
        lines = str(text).split("\n")
        widest = 0
        for line in lines:
            width = 0
            for word in line.split(" "):
                width += self.span(word)
            width += line.count(" ") * (self.max_width // 3)
            widest = max(widest, width)
        return widest, self.height * len(lines)

    def layout(self, text, box_width):
        """The lines `screen.text(text, rect)` produces, as (words, width) pairs.

        Mirrors image_layout_line: words break at spaces; a word that would
        overrun starts the next line unless it is the line's first.
        """
        space = self.height / 3.0
        lines = []
        for hard in str(text).split("\n"):
            words = [w for w in hard.split(" ") if w]
            line, width = [], 0.0
            for word in words:
                w = self.span(word)
                if line and width + space + w > box_width:
                    lines.append((line, width))
                    line, width = [], 0.0
                width = w if not line else width + space + w
                line.append(word)
            lines.append((line, width))
        return lines


def overflow(font, text, box):
    """Why `text` does not fit `box` (x, y, w, h), or None if it does."""
    _x, _y, width, height = box
    lines = font.layout(text, width)
    needed = len(lines) * font.height
    problems = []
    if needed > height:
        problems.append("%d lines need %d px, box is %d high"
                        % (len(lines), needed, height))
    for words, w in lines:
        if w > width:
            problems.append("%r is %d px, box is %d wide"
                            % (" ".join(words), w, width))
    return "; ".join(problems) or None
