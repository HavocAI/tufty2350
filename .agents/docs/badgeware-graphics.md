# Badgeware graphics

Images, sprites, vector shapes, brushes and the maths types. For how to *use*
these, see [badgeware-patterns.md](badgeware-patterns.md). Confidence markers are
explained in [README.md](README.md).

Source pages: `/docs/api/{image,spritesheet,shape,brush,mat3,vec2,rect,tween,algorithm}.md`,
`/docs/guides/{sprites,vector_shapes}.md`

---

## Two drawing systems

Badgeware has a raster path and a vector path, and they have very different costs.

| | Raster | Vector |
|---|---|---|
| Methods | `rectangle`, `circle`, `line`, `triangle`, `put`, `hspan`, `vspan`, `blit` | `shape()`, `shapes()` |
| Edges | Aliased | Antialiased, sub-pixel |
| Cost | Cheap | Expensive |

Use raster for UI chrome, bars, and anything grid-aligned. Reach for vector when
the curve quality actually shows.

> "Antialiasing can be pretty expensive so you should reserve its use for places
> where it really makes a difference!"

**[unmeasured]** How expensive, in frames, is not stated anywhere. If a screen gets
sluggish after adding `shape()` calls, this is the first thing to turn off.

Antialiasing levels — also available bare as `X4` / `X2` / `OFF`, since the
firmware hoists them into `builtins` alongside the alignment constants. Both
spellings are the same object, and neither can be probed with `globals()`; see
§18 of [badgeware-patterns.md](badgeware-patterns.md).

`image.X4` (4x4 grid, best, slowest), `image.X2` (2x2, good,
faster), `image.OFF`.

---

## image

The core pixel buffer type. `screen` is a global `image` bound to the framebuffer.

```python
image(w, h)                  # new buffer
image(w, h, buffer)          # wrap an existing buffer
image.load(path)             # PNG / JPEG / GIF on v3; PNG / JPEG only on v2
image.load(path, w, h)
image.load_into(path)        # PNG / JPEG only, and the file must match the size
```

Properties: `width`, `height` (read-only), `pen`, `font`, `cursor`, `clip`, `alpha`.

Raster drawing:

```python
screen.clear()               # fill with the current pen/brush
screen.put(x, y)             # one pixel
screen.rectangle(x, y, w, h) # or rectangle(rect)
screen.circle(x, y, r)
screen.line(x0, y0, x1, y1)
screen.triangle(...)
screen.hspan(...)            # scanline fills, faster than line() for axis-aligned runs
screen.vspan(...)
```

Blitting:

```python
screen.blit(img, x, y)       # also blit(img, rect) to scale
screen.blit_vspan(...)       # UV-style texture warping
screen.blit_hspan(...)
```

`batch()` combines drawing commands into a single C loop to cut interpreter
overhead — the first thing to try when a draw loop is too slow.

There is also a filter set: blur, bloom, zoom, wave, dither, duotone, c64, gameboy,
CRT, phosphor, synthwave.

---

## Sprites and spritesheets

```python
cards = image.load("/system/assets/cards.png").spritesheet(13, 6)
ace = cards.sprite(0, 1)      # column first, then row, both zero-indexed
screen.blit(ace, 20, 20)
```

**Neither form works on this badge.** **[verified-v2]**
The bare `spritesheet.load(path)` the official docs document raises
`NameError`, and the `image.load(...).spritesheet(cols, rows)` the firmware's
own bundled apps use raises `AttributeError: 'image' object has no attribute
'spritesheet'`. This firmware predates the type. **v2 has its own instead**:
the Python globals `SpriteSheet(path, cols, rows)` and `AnimatedSprite`, built
on `image.window()` **[source-v2]**, untried on the badge -- see "SpriteSheet
and AnimatedSprite" in [badgeware-api-v2.md](badgeware-api-v2.md). Run
`rampage/tools/probe_badge.py` in the `tufty_badge` repo on the badge before
relying on anything here.

`sprite(n)` also works with a single flat index.

A sprite is "a lightweight *view* onto the sheet that shares the same pixels rather
than copying them", so it is "cheap to create — you can call `sprite()` every frame
without copying any image data."

Properties: `rows`, `cols`, `sprites`, `timings` (per-frame ms, for GIFs),
`direction` (`spritesheet.ROWS` or `spritesheet.COLUMNS` — names that are
themselves unreachable on this badge, since `spritesheet` is not a global).

Blitting into a `rect` scales, and stays crisp at whole-number scales. Transparency
comes from the sprite's `alpha`.

### Animated GIFs

From the official `image` docs, which describe v3: "A GIF arrives as a sheet of
composited frames, **one column per frame**, with the file's timings attached."
`timings` on the resulting spritesheet is a tuple of per-frame milliseconds.

**In practice, do not ship an animation as a GIF.** **[verified-v2]** On the
badge's v2 there is **no GIF decoder at all**: `image.load` tries PNG, then
JPEG, and anything else fails the JPEG check **[source-v2]**. That is what
`rampage` got at 160x90 by 91 frames:

```
ValueError: unable to read image! 4
```

v3 does decode GIFs **[source-v3]**, and even there only tiny ones load: the
loader composites *every* frame into one 8bpp sheet before you get anything
back, and that sheet has a hard build limit. From `native/image_gif.cpp` in
[picovector-micropython](https://github.com/pimoroni/picovector-micropython),
which is where the limits live:

- The composite is `width * height * frames` bytes at 8bpp, checked against
  `PV_GIF_MAX_BYTES`. 160x90x91 is 1.28MB. The only GIF the firmware ships,
  `/system/assets/squirrel.gif`, is **1156 bytes**.
- The decoder's LZW tables *and* its frame table live in the shared rasteriser
  working buffer, `PV_WORKING_BUFFER_SIZE`, which is **60KB**. That caps the
  frame count independently of the pixel budget.
- The whole animation must fit **256 colours across all its frames**, not 256
  per frame.

Newer v3 builds spell these out — "%d frames of %dx%d composite to %dKB, over
the %dKB limit", "more than 256 colours across its frames", "too many frames".
On v2 the number is only ever the JPEG decoder's "not a JPEG", because a GIF
never reaches a GIF decoder.

### Ship one PNG per frame instead

With no spritesheet type tried on the badge and no GIF, what is left is the pair of calls
`firmware/apps/demos/demos/sprites.py` uses, and they are enough:

```python
sprites = [image.load("images/%03d.png" % i) for i in range(count)]
screen.blit(sprites[n], dest)
```

`rampage/tools/build_frames.py` in the `tufty_badge` repo is the worked converter: it decimates the
source GIF to the rate the app actually displays, scales it to a whole-number
blit, writes one palettised PNG per frame and a `frames.py` with the count and
the per-frame timings.

What this costs, and what to do about it:

- **Decode time at launch.** 28 frames is slow enough to see. Draw a message
  and `badge.update()` before starting, since `run()` has not presented
  anything yet.
- **RAM.** A palettised PNG decodes to 8bpp, so it is `width * height * frames`
  the same as the GIF composite would have been — the saving is in not being
  refused, not in the total. Decimating to the displayed frame rate is the
  reduction that matters.
- **One palette across all frames**, quantised together. Per-frame palettes
  read as a flicker.

### Looping footage that was not shot to loop

A clip only loops if its last frame matches its first. `rampage`'s does not:
the boat is 5% bigger and 16px further across by the end, so the wrap jumps.

**Work out what is actually moving before correcting anything.** Here the
camera never moves at all -- the horizon holds to a fiftieth of a pixel across
136 frames -- and the boat moves under its own power. Those want opposite
treatments, and an earlier version of this page had it backwards.

**Cancelling a subject's motion by scaling whole frames is not
stabilisation.** It holds the subject rigid and swings the world around it:
here the horizon swelled and the sea heaved. If the background is supposed to
stay put, the correction has to be one that cannot move it.

For a horizon, two transforms qualify, and between them they are enough:

- **a uniform scale about a point on the horizon line** — points on the line
  move along it, and the line spans the frame, so its picture is unchanged;
- **horizontal translation.**

Vertical translation is the one to leave out. And the pair happens to be what
undoing an approach needs: for a rigid thing on water, image size and distance
below the horizon are both one-over-distance, so a single scale about the
horizon corrects size and height together.

**Measure the thing you are correcting with the other axes left free.** The
model above is only nearly true — a planing boat rides higher as it speeds up
— and a fit that imposes the model resolves the conflict by splitting the
difference. Constrained, it reported 2.9% of growth where there was 5.0%, and
the correction visibly under-did it. Measure freely, then correct with only
the components you are allowed to use, and report what that leaves behind.

**Check it with something other than what made it**, before writing anything,
so a bad correction cannot replace a good build. `rampage` checks the horizon
*and* the subject, because those fail in opposite directions: one catches a
correction that overshoots, the other one that never happened.

`rampage/tools/unzoom.py` in the `tufty_badge` repo is the worked example, and its README records three
measurements that looked plausible and were wrong.

### Sizing frames for the panel

The panel is 320x240 in HIRES. **Author frames at a whole-number divisor of
that**, and the blit costs nothing and softens nothing; anything else is
resampled on every frame. `rampage` ships 320x180 and blits at scale 1.

16:9 content does not fit a 4:3 panel. Letterboxing is the honest option —
full width, bars top and bottom — and the alternative is cropping a quarter of
the width away. Stretching is not an option; the aspect ratio is visible.

Derive the scale on the badge from the frame size rather than hardcoding it:

```python
scale = min(screen.width // frame_width, screen.height // frame_height)
```

That way a rebuild at another size keeps working, and `screen.width` is read
rather than assumed — `badge.mode()` can make it 160x120.

**[unverified]** `image.window(rect)` is documented as returning a sub-image
view sharing pixels. If this badge has it, a single sheet becomes possible
again with no spritesheet type at all, and that is worth checking — one decode
instead of 28.

**A zero frame delay is legal in a GIF** and means "as fast as possible". Left
raw it will hang a catch-up loop. Substitute 100ms, as browsers do.

---

## shape

Resolution-independent geometry, drawn with the current brush.

Primitives: circles, ellipses, rectangles, regular polygons, squircles, arcs, pie
slices, stars, stroked lines, plus custom paths from `vec2` points or flat coordinate
arrays.

- `stroke()` turns a filled shape into an outlined band — configurable thickness,
  alignment, joins and caps
- `bounds()` returns the bounding `rect`, for hit testing or layout
- `transform` is a `mat3` applied at render time, leaving the path data alone

> "Creating shapes can be an expensive process, if possible define the shape once
> and keep a reference to it for future use."

Build shapes at import time, animate them via `transform`.

---

## brush

Anywhere you can assign a `color` to a pen, you can assign a brush instead.

```python
screen.pen = brush.image(img, matrix)
screen.pen = brush.gradient(brush.LINEAR, x1, y1, x2, y2, stops, matrix)
screen.pen = brush.fractal(scale, octaves, persistence, repeat, seed, transform)
```

Gradient types: `brush.LINEAR`, `brush.RADIAL`, `brush.CONICAL`, up to 16 stops.
There are also pattern brushes, and *effect* brushes that transform the pixels
underneath a shape (blur, pixelate, colour manipulation) rather than painting over
them.

---

## mat3

2D transform matrices.

```python
mat3()                        # identity
mat3.trs(x, y, degrees)       # combined translate/rotate/scale, cheaper than chaining
m.translate(x, y)
m.rotate(degrees)             # positive is clockwise; rotate_radians() also exists
m.scale(s)                    # or scale(x, y)
m.multiply(other)
m.inverse()                   # the only method that does not mutate in place
a * b                         # compose into a new matrix
```

Every method except `inverse()` mutates in place and returns itself for chaining.

> "The **last** call in a chain is the first thing to happen to your geometry."

So `mat3().translate(80, 60).rotate(30)` rotates first, then translates.

---

## vec2

```python
vec2(x, y)                    # floats, sub-pixel precision
```

Measurement: `length()`, `length_squared()`, `distance(o)`, `distance_squared(o)`,
`angle()` (radians, -pi..pi), `angle_to(o)`.

Vector ops: `dot(o)`, `cross(o)`, `normalized()`, `perpendicular()`, `rotated(a)`.

Transforms: `lerp(o, t)`, `reflect(normal)`, `clamp_length(max)`, `transform(m)`.

Operators `+ - * /` work with vectors and scalars. **Use the in-place forms
(`+=`, `-=`) inside loops** — they avoid an allocation, which matters given the GC
pauses described in [badgeware-hardware.md](badgeware-hardware.md).

---

## rect

```python
rect(x, y, w, h)              # top-left plus size, floats
```

Properties: `x`, `y`, `w`, `h`, and the edges `l`, `r`, `t`, `b`.

Methods: `offset(x, y)`, `deflate(a)` / `deflate(t, r, b, l)`, `inflate(...)`,
`intersection(other)` (returns `None` when they miss), `intersects(other)`,
`contains(other)`, `empty()`.

`offset`, `deflate` and `inflate` all return new rects.

---

## tween

```python
tween(start, end, duration=1.0, easing=tween.LINEAR)
```

Works on numbers, vectors, rects and matrices.

- `at(t)` — the eased value at progress `t`, clamped to the endpoints
- `start()` — self-time against the badge clock; returns self for chaining, call
  again to restart
- `stop()` — freeze; `elapsed` resets to 0 and `done` stays false until restarted

Properties: `from_`, `to`, `elapsed`, `now`, `done`, `running`.

Easing families — Linear, Quadratic, Cubic, Quartic, Quintic, Sine, Exponential,
Circular, Back, Elastic, Bounce — each with `_IN`, `_OUT` and `_INOUT` variants.

---

## algorithm

"Fast, low-level implementations of common algorithms ... whenever you need extra
speed without sacrificing the ease of using MicroPython."

```python
algorithm.clip_line(p1, p2, bounds)   # trims in place; True if any of it survives
algorithm.dda(point, angle, depth)    # grid ray traversal, exact crossings
algorithm.raycast(origin, angle, fov, rays, max_dist, map, width, height, screen_width)
```

`clip_line` is the generally useful one:

```python
if algorithm.clip_line(p1, p2, bounds):
    screen.line(p1, p2)
```
