import builtins
import gc
import os
import time

FPS = 12
FRAME_COUNT = 48
LOOP_MS = FRAME_COUNT * 1000 // FPS

os.chdir("/system/apps/rampage_loop")
badge.mode(LORES | VSYNC)
# Shipped firmware calls this rom_font; newer firmware calls it font.
font_library = getattr(builtins, "rom_font", None)
if font_library is None:
    font_library = builtins.font
screen.font = font_library.sins
screen.pen = color.black
screen.clear()
screen.pen = color.white
screen.text("Loading video...", 10, 50)
display.update()

# Decode once so playback does not read from flash or allocate images.
gc.collect()
frames = []
try:
    for index in range(FRAME_COUNT):
        frame = image.load(f"frames/frame_{index:03d}.png")
        if frame.width != 160 or frame.height != 120:
            raise ValueError("Video frames must be 160x120 pixels")
        frames.append(frame)
except MemoryError:
    frames.clear()
    gc.collect()
    fatal_error("Not enough memory", "Reset the badge and try again. This video needs about 3.5 MiB for its frames.")
except OSError:
    fatal_error("Missing video frame", "Copy the entire rampage_loop folder, including frames, into the badge's apps folder.")

gc.collect()
origin = vec2(0, 0)
last_tick = time.ticks_ms()
elapsed_ms = 0


def update():
    global last_tick, elapsed_ms

    now = time.ticks_ms()
    # Use elapsed time rather than counting screen refreshes. This preserves
    # playback speed and handles the MicroPython millisecond clock wrapping.
    elapsed_ms = (elapsed_ms + time.ticks_diff(now, last_tick)) % LOOP_MS
    last_tick = now
    frame_index = elapsed_ms * FPS // 1000
    screen.blit(frames[frame_index], origin)


run(update)
