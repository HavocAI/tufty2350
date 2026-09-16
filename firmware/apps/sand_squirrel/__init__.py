APP_DIR = "/system/apps/sand_squirrel"

import sys
import os

os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

import random

# Run at the badge's native low-res mode so the simulation grid maps
# one cell to one real screen pixel - "every pixel is simulated".
badge.mode(LORES | VSYNC)

COLS = screen.width
ROWS = screen.height

BG = color.rgb(14, 16, 22)

# a few warm shades so the sand pile isn't a flat block of colour
SHADES = (
    color.rgb(224, 190, 120),
    color.rgb(238, 205, 138),
    color.rgb(204, 168, 96),
    color.rgb(246, 220, 165),
)

BRUSH_NAMES = ("Circle", "Ellipse", "Square", "Plus", "X", "Squirrel")
BRUSH_COUNT = len(BRUSH_NAMES)

# scale the brush to the screen so small and large badge displays feel similar
RADIUS = max(2, min(COLS, ROWS) // 16)

MOVE_STEP = max(1, RADIUS // 3)
MOVE_INTERVAL = 55   # ms between cursor steps while a direction is held
SPAWN_INTERVAL = 45  # ms between sand stamps while B is held past the long-press threshold
LONG_PRESS_MS = 260  # how long B must be held before it starts dropping sand

hud_font = rom_font.sins

# 0 = empty, 1..len(SHADES) = a settled grain of sand (its shade)
grid = bytearray(COLS * ROWS)

# the highest row sand has ever reached - everything above it is guaranteed
# empty, so the sim and render passes can skip scanning it every frame
top_row = ROWS

cursor_x = COLS // 2
cursor_y = ROWS // 4
brush_index = 0

last_move = 0
last_spawn = 0
b_press_start = 0
b_long_fired = False
frame = 0

show_instructions = True


def brush_contains(shape_idx, dx, dy, r):
    if shape_idx == 0:  # Circle
        return dx * dx + dy * dy <= r * r

    if shape_idx == 1:  # Ellipse
        rx = r * 1.5
        ry = r * 0.7
        return (dx * dx) / (rx * rx) + (dy * dy) / (ry * ry) <= 1

    if shape_idx == 2:  # Square
        return -r <= dx <= r and -r <= dy <= r

    if shape_idx == 3:  # Plus
        thick = max(1, r // 3)
        return (abs(dx) <= thick and abs(dy) <= r) or (abs(dy) <= thick and abs(dx) <= r)

    if shape_idx == 4:  # X
        thick = max(1, r // 3)
        return abs(dx) <= r and abs(dy) <= r and (abs(dx - dy) <= thick or abs(dx + dy) <= thick)

    # Squirrel face: round head + two ear tufts, with two eye holes punched out
    face_ry = r * 0.85
    in_face = (dx * dx) / (r * r) + (dy * dy) / (face_ry * face_ry) <= 1

    ear_r = r * 0.4
    ear_y = -r * 0.75
    ear_x = r * 0.55
    in_left_ear = (dx + ear_x) ** 2 + (dy - ear_y) ** 2 <= ear_r * ear_r
    in_right_ear = (dx - ear_x) ** 2 + (dy - ear_y) ** 2 <= ear_r * ear_r

    if not (in_face or in_left_ear or in_right_ear):
        return False

    eye_r = max(1, r * 0.16)
    eye_y = -r * 0.05
    eye_x = r * 0.35
    in_left_eye = (dx + eye_x) ** 2 + (dy - eye_y) ** 2 <= eye_r * eye_r
    in_right_eye = (dx - eye_x) ** 2 + (dy - eye_y) ** 2 <= eye_r * eye_r

    return not (in_left_eye or in_right_eye)


def stamp_brush(cx, cy, r, shape_idx):
    global top_row
    for dy in range(-r, r + 1):
        y = cy + dy
        if y < 0 or y >= ROWS:
            continue
        row = y * COLS
        for dx in range(-r, r + 1):
            x = cx + dx
            if x < 0 or x >= COLS:
                continue
            if not brush_contains(shape_idx, dx, dy, r):
                continue
            idx = row + x
            if grid[idx] == 0:
                grid[idx] = 1 + random.randint(0, len(SHADES) - 1)

    if cy - r < top_row:
        top_row = max(0, cy - r)


# Falling-sand cellular automaton: scan bottom-to-top so a grain only ever
# takes one step per frame, and alternate scan direction each frame so the
# pile doesn't lean toward one side.
@micropython.native
def simulate(parity):
    for y in range(ROWS - 2, top_row - 1, -1):
        row = y * COLS
        below = row + COLS
        x = 0 if parity else COLS - 1
        step = 1 if parity else -1
        for _ in range(COLS):
            idx = row + x
            v = grid[idx]
            if v:
                if grid[below + x] == 0:
                    grid[below + x] = v
                    grid[idx] = 0
                else:
                    can_left = x > 0 and grid[below + x - 1] == 0
                    can_right = x < COLS - 1 and grid[below + x + 1] == 0
                    if can_left and can_right:
                        if (x + y + parity) & 1:
                            grid[below + x - 1] = v
                        else:
                            grid[below + x + 1] = v
                        grid[idx] = 0
                    elif can_left:
                        grid[below + x - 1] = v
                        grid[idx] = 0
                    elif can_right:
                        grid[below + x + 1] = v
                        grid[idx] = 0
            x += step


@micropython.native
def render():
    idx = top_row * COLS
    last_shade = 0
    for y in range(top_row, ROWS):
        for x in range(COLS):
            v = grid[idx]
            if v:
                if v != last_shade:
                    screen.pen = SHADES[v - 1]
                    last_shade = v
                screen.put(x, y)
            idx += 1


def draw_cursor():
    screen.pen = color.rgb(255, 255, 255, 130)
    screen.shape(shape.circle(cursor_x, cursor_y, RADIUS).stroke(1))


def draw_hud():
    screen.font = hud_font
    screen.pen = color.rgb(0, 0, 0, 140)
    screen.rectangle(0, 0, COLS, 10)
    screen.pen = color.rgb(255, 255, 255, 220)
    screen.text(BRUSH_NAMES[brush_index], 2, 1)


def center_text(text, y):
    w, _ = screen.measure_text(text)
    screen.text(text, (COLS - w) // 2, y)


def draw_instructions():
    screen.font = hud_font
    screen.pen = color.rgb(0, 0, 0, 160)
    screen.rectangle(0, 0, COLS, ROWS)
    screen.pen = color.rgb(255, 255, 255, 230)
    center_text("Hold B to make sand fall", ROWS // 2 - 6)

    if int(badge.ticks / 500) % 2:
        center_text("Press any button to start", ROWS // 2 + 6)


def update():
    global cursor_x, cursor_y, brush_index, last_move, last_spawn
    global b_press_start, b_long_fired, frame, show_instructions

    now = badge.ticks

    if show_instructions:
        if (
            badge.pressed(BUTTON_A)
            or badge.pressed(BUTTON_B)
            or badge.pressed(BUTTON_C)
            or badge.pressed(BUTTON_UP)
            or badge.pressed(BUTTON_DOWN)
        ):
            show_instructions = False

        screen.pen = BG
        screen.clear()
        render()
        draw_cursor()
        draw_hud()
        draw_instructions()
        return

    if badge.pressed(BUTTON_B):
        b_press_start = now
        b_long_fired = False

    if badge.held(BUTTON_B):
        if not b_long_fired and now - b_press_start >= LONG_PRESS_MS:
            b_long_fired = True
            last_spawn = now - SPAWN_INTERVAL
        if b_long_fired and now - last_spawn >= SPAWN_INTERVAL:
            stamp_brush(cursor_x, cursor_y, RADIUS, brush_index)
            last_spawn = now

    if badge.released(BUTTON_B) and not b_long_fired:
        brush_index = (brush_index + 1) % BRUSH_COUNT

    if now - last_move >= MOVE_INTERVAL:
        moved = False
        if badge.held(BUTTON_A):
            cursor_x = max(0, cursor_x - MOVE_STEP)
            moved = True
        if badge.held(BUTTON_C):
            cursor_x = min(COLS - 1, cursor_x + MOVE_STEP)
            moved = True
        if badge.held(BUTTON_UP):
            cursor_y = max(0, cursor_y - MOVE_STEP)
            moved = True
        if badge.held(BUTTON_DOWN):
            cursor_y = min(ROWS - 1, cursor_y + MOVE_STEP)
            moved = True
        if moved:
            last_move = now

    simulate(frame & 1)
    frame += 1

    screen.pen = BG
    screen.clear()
    render()
    draw_cursor()
    draw_hud()


run(update)
