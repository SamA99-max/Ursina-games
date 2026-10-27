"""
POCKET ARTILLERY - a 2-player / vs-CPU artillery game (Pocket-Tanks-style gameplay)
Target: Python 3.12.9, Ursina 7.0.0, Windows, VS Code.
    pip install ursina==7.0.0
    python pocket_artillery.py

Controls
    Left / Right : aim angle        Up / Down : power   (hold Shift = fine adjust)
    A / D        : drive tank (limited fuel per turn)
    Q / E        : previous / next weapon
    Space        : fire             R : back to menu     Esc : quit

New in this version
    * CPU is much less accurate (tune it with AI_SPREAD below)
    * Dotted white trajectory preview that fades with distance
    * Ground goes down forever; the camera follows (and zooms out if needed)
    * Text sits on dark panels made from separate Entities

Every unstable part is wrapped in safe-guards; errors are written to
pocket_artillery_errors.log instead of crashing the game.
"""
import sys
import math
import time
import random
import functools
import traceback

try:
    from ursina import *
except Exception:
    print("Could not import ursina. Install it with:  pip install ursina==7.0.0")
    traceback.print_exc()
    input("Press Enter to exit...")
    sys.exit(1)

# ----------------------------------------------------------------- settings
W, H = 80.0, 45.0          # world width / base view height in units
COLS = 200                 # terrain columns
GRAV = 22.0
FLOOR = -10000.0           # terrain can be dug "forever"
ROT_SIGN = -1              # if barrels point mirrored on your machine, set to 1
SHOTS_PER_PLAYER = 10
AI_SPREAD = 1.0            # CPU inaccuracy: 0 = perfect aim, 1 = default, 2 = very sloppy
PREVIEW_DOTS = 20          # max dots in the trajectory preview
PREVIEW_SPACING = 1.8      # world units between dots
LOGF = "pocket_artillery_errors.log"
_err_count = 0


def C(r, g, b, a=1.0):
    return color.Color(r, g, b, a)


def log_err(where):
    global _err_count
    _err_count += 1
    print(f"[warn] error in {where} (see {LOGF})")
    if _err_count > 40:        # don't spam the log file
        return
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write(f"[{where}]\n{traceback.format_exc()}\n")
    except Exception:
        pass


def safe(fn):
    """Decorator: swallow + log exceptions so one bug can't kill the game."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            log_err(fn.__name__)
            return None
    return wrapper


def safe_destroy(e):
    try:
        if e is not None:
            destroy(e)
    except Exception:
        log_err("safe_destroy")


def Wp(r, pts, kind, col, g=1.0, spd=1.0):
    return dict(r=r, pts=pts, kind=kind, col=C(*col), g=g, spd=spd)


WEAPONS = {
    "Pebble":      Wp(2.0, 18, "shell",   (1.0, 1.0, 0.6)),
    "Shell":       Wp(3.0, 28, "shell",   (1.0, 0.8, 0.2)),
    "Heavy Shell": Wp(4.5, 42, "shell",   (1.0, 0.5, 0.1)),
    "Mega Bomb":   Wp(6.5, 65, "shell",   (1.0, 0.2, 0.1)),
    "Nuke":        Wp(9.0, 90, "shell",   (0.9, 0.0, 0.2), spd=0.9),
    "Triple Shot": Wp(2.6, 26, "triple",  (0.6, 1.0, 0.6)),
    "Cluster":     Wp(2.5, 25, "cluster", (0.9, 0.5, 1.0)),
    "Roller":      Wp(3.5, 38, "roller",  (0.5, 0.9, 1.0)),
    "Digger":      Wp(2.6, 32, "digger",  (0.7, 0.5, 0.3)),
    "Dirt Mound":  Wp(4.5, 0,  "dirt",    (0.5, 0.35, 0.2)),
    "Bouncer":     Wp(3.0, 32, "bouncer", (1.0, 0.4, 0.8)),
    "Sniper":      Wp(1.8, 48, "shell",   (1.0, 1.0, 1.0), g=0.2, spd=1.6),
}
BOMBLET = Wp(2.0, 14, "shell", (1.0, 0.9, 0.3))


# ----------------------------------------------------------------- text label with panel
class Label:
    """Text + a separate dark quad Entity behind it (no Text(background=True))."""

    def __init__(self, x, y, scale=1.0, col=None, center=False, alpha=0.5, pad=0.02):
        self.x, self.y, self.sc = x, y, scale
        self.center, self.pad = center, pad
        self.cur = None
        self.panel = Entity(parent=camera.ui, model="quad", color=C(0.02, 0.03, 0.06, alpha),
                            z=1, enabled=False)
        self.text = Text(text="", x=x, y=y, scale=scale, color=col or color.white,
                         origin=(0, 0) if center else (-0.5, 0.5))

    def _dims(self):
        lines = (self.cur or "").split("\n")
        est_w = max(len(l) for l in lines) * 0.0135 * self.sc
        est_h = len(lines) * 0.032 * self.sc
        w = est_w
        try:  # use the real width if it looks sane (it may or may not include scale)
            rw = float(self.text.width)
            best = min((rw, rw * self.sc), key=lambda c: abs(c - est_w))
            if 0.5 * est_w < best < 2.0 * est_w:
                w = best
        except Exception:
            pass
        return w, est_h

    @safe
    def set(self, txt):
        if txt == self.cur:
            return
        self.cur = txt
        self.text.text = txt
        if not txt:
            self.panel.enabled = False
            return
        w, h = self._dims()
        self.panel.scale = (w + 2 * self.pad, h + 2 * self.pad)
        if self.center:
            self.panel.position = (self.x, self.y, 1)
        else:
            self.panel.position = (self.x + w / 2, self.y - h / 2, 1)
        self.panel.enabled = True


# ----------------------------------------------------------------- terrain
class Terrain:
    def __init__(self):
        self.n = COLS + 1
        self.dx = W / COLS
        self.h = [10.0] * self.n
        self.ver = 0
        self.ent = Entity(position=(0, 0, 0), double_sided=True)
        self.generate()

    @safe
    def generate(self):
        p1, p2, p3 = (random.uniform(0, 6.28) for _ in range(3))
        a1, a2, a3 = random.uniform(3, 6), random.uniform(1.5, 3.5), random.uniform(0.5, 1.5)
        for i in range(self.n):
            x = i * self.dx
            v = 14 + a1 * math.sin(x * 0.08 + p1) + a2 * math.sin(x * 0.19 + p2) + a3 * math.sin(x * 0.43 + p3)
            self.h[i] = max(4.0, min(28.0, v))
        self.build()

    def height(self, x):
        f = max(0.0, min(self.n - 1.001, x / self.dx))
        i = int(f)
        t = f - i
        return self.h[i] * (1 - t) + self.h[i + 1] * t

    def _range(self, cx, r):
        return max(0, int((cx - r) / self.dx)), min(self.n - 1, int((cx + r) / self.dx) + 1)

    @safe
    def carve(self, cx, cy, r):
        i0, i1 = self._range(cx, r)
        for i in range(i0, i1 + 1):
            d = i * self.dx - cx
            if abs(d) >= r:
                continue
            lo = cy - math.sqrt(r * r - d * d)
            if self.h[i] > lo:
                self.h[i] = max(FLOOR, lo)

    @safe
    def add(self, cx, cy, r):
        i0, i1 = self._range(cx, r)
        for i in range(i0, i1 + 1):
            d = i * self.dx - cx
            if abs(d) >= r:
                continue
            q = math.sqrt(r * r - d * d)
            if self.h[i] >= cy - q:
                self.h[i] = min(70.0, max(self.h[i], cy + q))

    @safe
    def shaft(self, cx, r, depth):
        i0, i1 = self._range(cx, r)
        for i in range(i0, i1 + 1):
            d = (i * self.dx - cx) / r
            if abs(d) < 1:
                self.h[i] = max(FLOOR, self.h[i] - depth * (1 - d * d))

    @safe
    def build(self):
        V, K = [], []
        DEEP = -400.0
        dtop, dmid, dlo = C(0.55, 0.38, 0.20), C(0.40, 0.26, 0.12), C(0.20, 0.12, 0.06)
        g1, g2 = C(0.18, 0.50, 0.15), C(0.28, 0.70, 0.22)

        def quad(p0, p1, p2, p3, c0, c1, c2, c3):
            for p in (p0, p1, p2, p0, p2, p3):
                V.append(Vec3(p[0], p[1], 0))
            for c in (c0, c1, c2, c0, c2, c3):
                K.append(c)

        def column(x0, x1, h0, h1):
            b0, b1 = h0 - 1.0, h1 - 1.0
            c0, c1 = b0 - 14.0, b1 - 14.0
            quad((x0, b0), (x1, b1), (x1, h1), (x0, h0), g1, g1, g2, g2)               # grass
            quad((x0, c0), (x1, c1), (x1, b1), (x0, b0), dmid, dmid, dtop, dtop)       # upper dirt
            quad((x0, DEEP), (x1, DEEP), (x1, c1), (x0, c0), dlo, dlo, dmid, dmid)     # endless depth

        for i in range(self.n - 1):
            column(i * self.dx, (i + 1) * self.dx, self.h[i], self.h[i + 1])
        # flat land beyond both edges so the view can zoom out without seeing a void
        column(-2 * W, 0.0, self.h[0], self.h[0])
        column(W, 3 * W, self.h[-1], self.h[-1])
        self.ent.model = Mesh(vertices=V, colors=K)
        self.ver += 1

    def destroy(self):
        safe_destroy(self.ent)


# ----------------------------------------------------------------- tank
class Tank:
    def __init__(self, idx, x, col, terrain):
        self.idx = idx
        self.x = x
        self.y = terrain.height(x)
        self.angle = 60.0 if idx == 0 else 120.0
        self.power = 55.0
        self.score = 0
        self.fuel = 12.0
        self.weapons = []
        self.sel = 0
        self.slope = 0.0
        dark = C(col[0] * 0.5, col[1] * 0.5, col[2] * 0.5)
        self.root = Entity(position=(self.x, self.y, -1))
        Entity(parent=self.root, model="quad", color=col, scale=(2.6, 0.9), y=0.45)
        Entity(parent=self.root, model="circle", color=col, scale=1.5, y=0.9)
        self.pivot = Entity(parent=self.root, y=1.0)
        Entity(parent=self.pivot, model="quad", color=dark, origin=(-0.5, 0), scale=(1.9, 0.28))

    def muzzle(self):
        a = math.radians(self.angle)
        return self.x + math.cos(a) * 1.9, self.y + 1.0 + math.sin(a) * 1.9

    def center(self):
        return self.x, self.y + 0.8

    @safe
    def sync(self, T, dt):
        ty = T.height(self.x)
        self.y = max(ty, self.y - 30 * dt) if self.y > ty + 0.02 else ty
        sl = math.degrees(math.atan2(T.height(self.x + 1.2) - T.height(self.x - 1.2), 2.4))
        self.slope += (sl - self.slope) * min(1.0, dt * 10)
        self.root.position = (self.x, self.y, -1)
        self.root.rotation_z = ROT_SIGN * self.slope
        self.pivot.rotation_z = ROT_SIGN * (self.angle - self.slope)

    def destroy(self):
        safe_destroy(self.root)


# ----------------------------------------------------------------- projectile
class Shot:
    def __init__(self, owner, wname, x, y, vx, vy, gen=0, w=None):
        self.owner = owner
        self.wname = wname
        self.w = w or WEAPONS[wname]
        self.kind = self.w["kind"] if self.w["kind"] != "triple" else "shell"
        self.x, self.y, self.vx, self.vy = x, y, vx, vy
        self.gen = gen
        self.age = 0.0
        self.bounces = 0
        self.rolling = False
        self.roll_t = 0.0
        self.rspd = 9.0
        self.dir = 1 if vx >= 0 else -1
        self.dead = False
        sc = 0.4 if wname == "Sniper" else 0.65
        self.ent = Entity(model="circle", scale=sc, color=self.w["col"], position=(x, y, -2))

    def kill(self):
        self.dead = True
        safe_destroy(self.ent)
        self.ent = None


# ----------------------------------------------------------------- game
class Game(Entity):
    def __init__(self):
        super().__init__()
        self.state = "menu"
        self.mode = 1
        self.cur = 0
        self.terrain = None
        self.tanks = []
        self.shots = []
        self.objs = []
        self.wait = 0.0
        self.msg_t = 0.0
        self.ai_t = 0.0
        self.ai_plan = None
        self._fov = -1.0
        self._applied_fov = -1.0
        self.cam_y = H / 2
        self.cam_snap = True
        self.hud_l = Label(-0.86, 0.47)
        self.hud_r = Label(0.52, 0.47)
        self.hud_b = Label(-0.86, -0.33, scale=0.85)
        self.msg = Label(0, 0.28, scale=2.2, col=color.yellow, center=True, alpha=0.4)
        self.center = Label(0, 0.05, scale=1.6, center=True, alpha=0.6, pad=0.04)
        # trajectory preview dots (pooled, created once)
        self.dots = [Entity(model="circle", scale=0.3, color=C(1, 1, 1, 1), position=(0, 0, -1.5), enabled=False)
                     for _ in range(PREVIEW_DOTS)]
        self.dots_n = 0
        self.prev_key = None
        self.show_menu()

    # ---- helpers
    def say(self, txt, secs=1.6):
        self.msg.set(txt)
        self.msg_t = secs

    @safe
    def clear_game(self):
        for s in self.shots:
            s.kill()
        self.shots = []
        for t in self.tanks:
            t.destroy()
        self.tanks = []
        if self.terrain:
            self.terrain.destroy()
            self.terrain = None
        for o in self.objs:
            safe_destroy(o)
        self.objs = []
        self.hide_preview()

    def make_clouds(self):
        for _ in range(7):
            self.objs.append(Entity(model="circle", color=C(1, 1, 1, 0.8), z=3,
                                    position=(random.uniform(-10, W + 10), random.uniform(32, 46)),
                                    scale=(random.uniform(6, 11), random.uniform(2, 4))))

    @safe
    def show_menu(self):
        self.clear_game()
        self.terrain = Terrain()
        self.make_clouds()
        self.state = "menu"
        self.cam_snap = True
        self.msg.set("")
        self.center.set("POCKET ARTILLERY\n\n1 - Two players\n2 - Versus CPU\n\n"
                        "Left/Right angle   Up/Down power   Shift fine\n"
                        "A/D drive   Q/E weapon   Space fire\nR menu   Esc quit")

    @safe
    def new_game(self, mode):
        self.clear_game()
        self.mode = mode
        self.terrain = Terrain()
        self.make_clouds()
        x0 = random.uniform(8, 18)
        x1 = random.uniform(W - 18, W - 8)
        self.tanks = [Tank(0, x0, C(0.9, 0.25, 0.2), self.terrain),
                      Tank(1, x1, C(0.2, 0.45, 0.95), self.terrain)]
        names = list(WEAPONS)
        for t in self.tanks:
            t.weapons = random.sample(names, min(SHOTS_PER_PLAYER, len(names)))
        self.cur = 0
        self.cam_snap = True
        self.center.set("")
        self.begin_turn()

    def begin_turn(self):
        self.state = "aim"
        self.ai_plan = None
        self.ai_t = 0.0
        self.prev_key = None
        t = self.tanks[self.cur]
        t.fuel = 12.0
        t.sel = max(0, min(t.sel, len(t.weapons) - 1))
        self.say(f"Player {self.cur + 1}" + (" (CPU)" if self.is_cpu() else ""), 1.0)

    def is_cpu(self):
        return self.mode == 2 and self.cur == 1

    # ---- firing
    @safe
    def fire(self):
        t = self.tanks[self.cur]
        if not t.weapons:
            return
        wname = t.weapons.pop(t.sel)
        t.sel = max(0, min(t.sel, len(t.weapons) - 1))
        w = WEAPONS[wname]
        speed = t.power * 0.45 * w["spd"]
        angles = [t.angle - 6, t.angle, t.angle + 6] if w["kind"] == "triple" else [t.angle]
        mx, my = t.muzzle()
        for a in angles:
            r = math.radians(a)
            self.shots.append(Shot(t, wname, mx, my, math.cos(r) * speed, math.sin(r) * speed))
        self.say(wname, 1.0)
        self.state = "flight"
        self.wait = 0.0
        self.hide_preview()

    @safe
    def end_turn(self):
        if not any(t.weapons for t in self.tanks):
            self.finish()
            return
        self.cur = 1 - self.cur
        if not self.tanks[self.cur].weapons:
            self.cur = 1 - self.cur
        self.begin_turn()

    def finish(self):
        self.state = "over"
        a, b = self.tanks[0].score, self.tanks[1].score
        res = "DRAW!" if a == b else f"PLAYER {1 if a > b else 2} WINS!"
        self.center.set(f"GAME OVER\n{a}  -  {b}\n{res}\n\nPress R for menu")

    # ---- physics
    def hit_tank(self, s):
        for t in self.tanks:
            if t is s.owner and s.age < 0.4:
                continue
            if abs(s.x - t.x) < 1.4 and t.y - 0.1 <= s.y <= t.y + 1.7:
                return True
        return False

    def move_shot(self, s, h):
        T = self.terrain
        if s.rolling:
            s.roll_t += h
            s.x += s.dir * s.rspd * h
            s.rspd = max(3.0, s.rspd - 1.0 * h)
            if s.x < 0 or s.x > W:
                s.x = max(0.0, min(W, s.x))
                self.explode(s)
                return
            s.y = T.height(s.x) + 0.3
            if s.roll_t > 2.2 or self.hit_tank(s):
                self.explode(s)
            return
        s.vy -= GRAV * s.w["g"] * h
        s.x += s.vx * h
        s.y += s.vy * h
        if s.x < 0:
            s.x, s.vx = 0.0, abs(s.vx) * 0.6
        elif s.x > W:
            s.x, s.vx = W, -abs(s.vx) * 0.6
        if s.y < -600:
            s.kill()
            return
        if s.kind == "cluster" and s.gen == 0 and s.vy < 0 and s.age > 0.3:
            for i in range(5):
                self.shots.append(Shot(s.owner, "bomblet", s.x, s.y,
                                       s.vx * 0.6 + (i - 2) * 3.0, s.vy * 0.3 + random.uniform(-1, 2),
                                       gen=1, w=BOMBLET))
            s.kill()
            return
        if self.hit_tank(s):
            self.explode(s)
            return
        gh = T.height(s.x)
        if s.y <= gh:
            if s.kind == "bouncer" and s.bounces < 2:
                s.bounces += 1
                s.y = gh + 0.05
                s.vy = abs(s.vy) * 0.6
                s.vx *= 0.8
            elif s.kind == "roller":
                s.rolling = True
                s.y = gh + 0.3
                s.dir = 1 if s.vx >= 0 else -1
            else:
                s.y = gh
                self.explode(s)

    @safe
    def step_shots(self, dt):
        for s in list(self.shots):
            if s.dead:
                continue
            s.age += dt
            if s.age > 25:
                s.kill()
                continue
            n = 4
            for _ in range(n):
                if s.dead:
                    break
                try:
                    self.move_shot(s, dt / n)
                except Exception:
                    log_err("move_shot")
                    s.kill()
            if not s.dead and s.ent is not None:
                s.ent.position = (s.x, s.y, -2)
        self.shots = [s for s in self.shots if not s.dead]

    @safe
    def explode(self, s):
        if s.dead:
            return
        w, r, T = s.w, s.w["r"], self.terrain
        cx, cy = s.x, s.y
        if s.kind == "dirt":
            T.add(cx, cy, r)
        else:
            T.carve(cx, cy, r)
            if s.kind == "digger":
                T.shaft(cx, r * 0.9, 7.0)
        self.fx(cx, cy, r, s.kind == "dirt")
        gained = 0
        if w["pts"] > 0:
            for t in self.tanks:
                if t is s.owner:
                    continue
                tx, ty = t.center()
                d = math.hypot(tx - cx, ty - cy)
                reach = r + 1.6
                if d < reach:
                    gained += max(1, int(w["pts"] * (1 - d / reach)))
        if gained:
            s.owner.score += gained
            self.say(f"+{gained}", 1.4)
        s.kill()
        T.build()

    @safe
    def fx(self, x, y, r, dirt=False):
        col = C(0.55, 0.38, 0.2, 0.9) if dirt else C(1.0, 0.7, 0.1, 0.9)
        e = Entity(model="circle", position=(x, y, -3), scale=0.3, color=col)
        try:
            e.animate_scale(r * 2.3, duration=0.25)
            e.fade_out(duration=0.45)
        except Exception:
            log_err("fx-anim")
        destroy(e, delay=0.55)

    # ---- trajectory preview (dotted white line that fades with distance)
    def hide_preview(self):
        for d in self.dots[:self.dots_n]:
            d.enabled = False
        self.dots_n = 0
        self.prev_key = None

    @safe
    def update_preview(self):
        t = self.tanks[self.cur] if self.tanks else None
        if not (self.state == "aim" and t and t.weapons and not self.is_cpu() and self.terrain):
            if self.dots_n:
                self.hide_preview()
            return
        key = (round(t.x, 2), round(t.y, 2), round(t.angle, 1), round(t.power, 1),
               t.sel, len(t.weapons), self.terrain.ver)
        if key == self.prev_key:
            return
        self.prev_key = key
        w = WEAPONS[t.weapons[t.sel]]
        speed = t.power * 0.45 * w["spd"]
        a = math.radians(t.angle)
        x, y = t.muzzle()
        vx, vy = math.cos(a) * speed, math.sin(a) * speed
        g = GRAV * w["g"]
        foe = self.tanks[1 - self.cur]
        step = 0.02
        acc = travel = 0.0
        pts = []
        for _ in range(2500):
            vy -= g * step
            nx, ny = x + vx * step, y + vy * step
            d = math.hypot(nx - x, ny - y)
            x, y = nx, ny
            acc += d
            travel += d
            if x < 0:
                x, vx = 0.0, abs(vx) * 0.6
            elif x > W:
                x, vx = W, -abs(vx) * 0.6
            if acc >= PREVIEW_SPACING:
                acc = 0.0
                f = min(1.0, travel / 170.0)            # fades slowly: still visible at long range
                pts.append((x, y, 1.0 - 0.8 * f, 0.30 - 0.12 * f))
                if len(pts) >= PREVIEW_DOTS:
                    break
            if y <= self.terrain.height(x) or y < -500:
                break
            if abs(x - foe.x) < 1.4 and foe.y - 0.1 <= y <= foe.y + 1.7:
                break
        for i, (px, py, al, sc) in enumerate(pts):
            dot = self.dots[i]
            dot.position = (px, py, -1.5)
            dot.scale = sc
            dot.color = C(1, 1, 1, al)
            dot.enabled = True
        for i in range(len(pts), self.dots_n):
            self.dots[i].enabled = False
        self.dots_n = len(pts)

    # ---- AI
    def simulate(self, t, angle, speed, g):
        x, y = t.muzzle()
        r = math.radians(angle)
        vx, vy = math.cos(r) * speed, math.sin(r) * speed
        for _ in range(700):
            vy -= GRAV * g * 0.03
            x += vx * 0.03
            y += vy * 0.03
            if x < 0 or x > W:
                return x, y
            if y <= self.terrain.height(x):
                return x, y
        return x, y

    @safe
    def ai_turn(self, dt):
        t = self.tanks[1]
        foe = self.tanks[0]
        self.ai_t += dt
        if self.ai_plan is None:
            if self.ai_t < 0.7:
                return
            good = [i for i, n in enumerate(t.weapons) if WEAPONS[n]["kind"] != "dirt"]
            t.sel = random.choice(good or list(range(len(t.weapons))))
            w = WEAPONS[t.weapons[t.sel]]
            fx_, fy_ = foe.center()
            # the CPU "misjudges" where you are, then adds hand-shake on top
            off = random.uniform(-10.0, 10.0) * AI_SPREAD
            if random.random() < 0.25:
                off *= 2.0
            best = None
            for _ in range(80):
                a, p = random.uniform(15, 165), random.uniform(20, 100)
                lx, ly = self.simulate(t, a, p * 0.45 * w["spd"], w["g"])
                err = math.hypot(lx - (fx_ + off), ly - fy_)
                if best is None or err < best[0]:
                    best = (err, a, p)
            na = random.uniform(-5.0, 5.0) * AI_SPREAD
            npw = 1.0 + random.uniform(-0.09, 0.09) * AI_SPREAD
            self.ai_plan = (best[1] + na, max(10.0, min(100.0, best[2] * npw)))
            self.ai_t = 0.0
            return
        ta, tp = self.ai_plan
        step_a, step_p = 70 * dt, 50 * dt
        t.angle += max(-step_a, min(step_a, ta - t.angle))
        t.power += max(-step_p, min(step_p, tp - t.power))
        t.angle = max(0.0, min(180.0, t.angle))
        if abs(ta - t.angle) < 0.5 and abs(tp - t.power) < 0.5 and self.ai_t > 0.8:
            self.fire()

    # ---- input / update
    @safe
    def handle_aim(self, dt):
        t = self.tanks[self.cur]
        if self.is_cpu():
            self.ai_turn(dt)
            return
        f = 0.25 if held_keys["shift"] else 1.0
        if held_keys["left arrow"]:
            t.angle += 40 * f * dt
        if held_keys["right arrow"]:
            t.angle -= 40 * f * dt
        if held_keys["up arrow"]:
            t.power += 30 * f * dt
        if held_keys["down arrow"]:
            t.power -= 30 * f * dt
        t.angle = max(0.0, min(180.0, t.angle))
        t.power = max(5.0, min(100.0, t.power))
        d = (1 if held_keys["d"] else 0) - (1 if held_keys["a"] else 0)
        if d and t.fuel > 0:
            nx = max(1.5, min(W - 1.5, t.x + d * 5.0 * dt))
            if self.terrain.height(nx) - t.y < 1.0:
                t.x = nx
                t.fuel = max(0.0, t.fuel - 5.0 * dt)

    @safe
    def fit_camera(self, dt):
        """Camera only drops as far as needed to keep both tanks in view (zooms out if they are far apart)."""
        ar = window.aspect_ratio or 1.78
        low, high = 0.0, H
        if self.tanks:
            ys = [t.y for t in self.tanks]
            low = min(0.0, min(ys) - 5.0)
            high = max(low + H, max(ys) + 9.0)
        fov = max(high - low, W / ar)
        cy = (low + high) / 2.0
        if self.cam_snap or self._fov < 0:
            self._fov, self.cam_y, self.cam_snap = fov, cy, False
        else:
            k = min(1.0, dt * 3.0)
            self._fov += (fov - self._fov) * k
            self.cam_y += (cy - self.cam_y) * k
        if abs(self._fov - self._applied_fov) > 1e-3:
            self._applied_fov = self._fov
            camera.fov = self._fov
        camera.position = (W / 2, self.cam_y, -30)

    def refresh_hud(self):
        if not self.tanks:
            self.hud_l.set("")
            self.hud_r.set("")
            self.hud_b.set("")
            return
        for i, t in enumerate(self.tanks):
            mark = ">" if (i == self.cur and self.state in ("aim", "flight")) else " "
            tag = " (CPU)" if (self.mode == 2 and i == 1) else ""
            (self.hud_l if i == 0 else self.hud_r).set(
                f"{mark}P{i + 1}{tag}  Score: {t.score}\n   Shots left: {len(t.weapons)}")
        t = self.tanks[self.cur]
        if t.weapons and self.state in ("aim", "flight"):
            names = [f"[{n}]" if j == t.sel else n for j, n in enumerate(t.weapons)]
            row1, row2 = "  ".join(names[:5]), "  ".join(names[5:])
            txt = (f"Angle {int(t.angle)}   Power {int(t.power)}   Fuel {int(t.fuel)}\n"
                   f"Weapon: {t.weapons[t.sel]}  (Q/E)\n{row1}" + (f"\n{row2}" if row2 else ""))
        else:
            txt = ""
        self.hud_b.set(txt)

    @safe
    def update(self):
        dt = min(getattr(time, "dt", 0.016), 0.05)
        self.fit_camera(dt)
        if self.terrain:
            for t in self.tanks:
                t.sync(self.terrain, dt)
        if self.msg_t > 0:
            self.msg_t -= dt
            if self.msg_t <= 0:
                self.msg.set("")
        if self.state == "aim":
            self.handle_aim(dt)
        elif self.state == "flight":
            self.step_shots(dt)
            if not self.shots:
                self.wait += dt
                if self.wait > 0.9:
                    self.end_turn()
        self.update_preview()
        self.refresh_hud()

    @safe
    def input(self, key):
        if key == "escape":
            application.quit()
            return
        if self.state == "menu":
            if key == "1":
                self.new_game(1)
            elif key == "2":
                self.new_game(2)
            return
        if key == "r":
            self.show_menu()
            return
        if self.state == "aim" and not self.is_cpu():
            t = self.tanks[self.cur]
            if key == "space":
                self.fire()
            elif key in ("q", "e") and t.weapons:
                t.sel = (t.sel + (1 if key == "e" else -1)) % len(t.weapons)


# ----------------------------------------------------------------- main
def main():
    app = Ursina(title="Pocket Artillery", borderless=False)
    window.color = C(0.45, 0.70, 0.95)
    camera.orthographic = True
    camera.fov = H
    camera.position = (W / 2, H / 2, -30)
    Game()
    app.run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_err("main")
        traceback.print_exc()
        input("Crashed during startup. See log. Press Enter to exit...")
