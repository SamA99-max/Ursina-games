from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
from ursina.shaders import lit_with_shadows_shader
from ursina.prefabs.ursfx import ursfx
import random
import math

# Optional cheap shader for particles / beams (safe fallback if unavailable)
try:
    from ursina.shaders import unlit_shader
except Exception as e:
    print(f"DEBUG unlit_shader import fallback: {e}")
    unlit_shader = None

app = Ursina()

Entity.default_shader = lit_with_shadows_shader
window.borderless = False

MAP_SIZE = 250
score = 0
wave = 1
enemies_left_in_wave = 6
game_is_over = False
host_mode = False
saved_player_hp = 125
saved_player_max_hp = 125

# ----------------------------------------------------------------------------
# NEW GLOBAL STATE (upgrade)
# ----------------------------------------------------------------------------
MAX_ACTIVE_ENEMIES = 36        # soft cap, spawner waits instead of flooding the map
MAX_PARTICLES = 160            # hard cap for FX entities (particles, beams, rings)
BASE_FOV = 90
DASH_DURATION = 0.2
DASH_SPEED = 42.0
BOSS_WAVE_INTERVAL = 10        # major boss every 10th wave (forever)
CHAMPION_WAVE_INTERVAL = 5     # champion (elite mini-boss) every other 5th wave
BASE_PLAYER_HP = 125

kills = 0
bosses_defeated = 0
game_time = 0.0
combo_count = 0
combo_best = 0
combo_timer = 0.0
spawn_generation = 0           # invalidates stale spawn chains (restart / host toggle)
wave_modifier = None
pending_perk_choices = 0
perk_offer = []
perk_levels = {}
perk_menu_open = False
perk_menu_prev_mouse_visible = False
current_boss = None
last_damage_time = 0.0

dash_cooldown_timer = 0.0
dash_time_left = 0.0
dash_direction = Vec3(0, 0, 1)
dash_hit_ids = set()
player_invuln_timer = 0.0
grenade_count = 2
pulse_cooldown_timer = 0.0
overdrive_charge = 0.0
overdrive_time_left = 0.0
fov_kick = 0.0
banner_timer = 0.0
hitmarker_timer = 0.0
fx_overlay_alpha = 0.0
fx_overlay_rgb = (1.0, 1.0, 1.0)
shift_was_down = False
sky_blood_active = False

_debug_last_print = {}
_hud_cache = {}


def debug_log(tag, err=None):
    """Throttled debug printer (max once per second per tag) so a per-frame error can't flood the console."""
    try:
        now = game_time
        last = _debug_last_print.get(tag, -999.0)
        if now - last < 1.0:
            return
        _debug_last_print[tag] = now
        print(f"DEBUG {tag} Error: {err}")
    except Exception:
        pass


def is_entity_valid(entity):
    """Anti-assertion guard: True only when the entity exists and its NodePath is not empty."""
    if not entity:
        return False
    if hasattr(entity, 'isEmpty') and entity.isEmpty():
        return False
    return True


def rgba01(r, g, b, a=1.0):
    """Color helper that is independent of ursina's rgb()/rgba() scale (always 0..1 floats)."""
    return Vec4(r, g, b, a)


def fx_shader_kwargs():
    if unlit_shader is not None:
        return {'shader': unlit_shader}
    return {}


def safe_call(fn, tag):
    try:
        fn()
    except Exception as e:
        debug_log(tag, e)


def default_player_stats():
    return {
        "damage_mult": 1.0,
        "cooldown_mult": 1.0,
        "speed_mult": 1.0,
        "lifesteal_bonus": 0.0,
        "arc_level": 0,
        "regen": 0.0,
        "score_mult": 1.0,
        "dash_cd": 2.5,
        "shock_dash": False,
        "grenade_cap": 3,
        "blast_mult": 1.0,
        "pulse_cd": 14.0,
        "pulse_radius": 10.0,
        "drop_bonus": 0.0,
        "crit_chance": 0.05,
    }


player_stats = default_player_stats()

WEAPONS = [
    {
        "name": "Tactical Pistol",
        "damage": 25,
        "cooldown": 0.22,
        "pellets": 1,
        "spread": 0.0,
        "perk": "Reliable & Fast Handling",
        "color": color.dark_gray,
        "scale": (0.2, 0.2, 0.7),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 1.0,
    },
    {
        "name": "Combat Shotgun",
        "damage": 14, # per pellet
        "cooldown": 0.55,
        "pellets": 7,
        "spread": 0.08,
        "perk": "7-Pellet Close-Range Shredder",
        "color": color.rgb(100, 60, 30),
        "scale": (0.3, 0.25, 0.9),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 0.95,
    },
    {
        "name": "Rapid SMG",
        "damage": 15,
        "cooldown": 0.08,
        "pellets": 1,
        "spread": 0.03,
        "perk": "High Mobility (+25% Move Speed)",
        "color": color.rgb(40, 90, 140),
        "scale": (0.18, 0.22, 0.6),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 1.25,
    },
    {
        "name": "Assault Rifle",
        "damage": 28,
        "cooldown": 0.14,
        "pellets": 1,
        "spread": 0.015,
        "perk": "Fully Automatic All-Rounder",
        "color": color.rgb(60, 80, 60),
        "scale": (0.22, 0.22, 0.95),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 1.0,
    },
    {
        "name": "Heavy Sniper",
        "damage": 135,
        "cooldown": 0.85,
        "pellets": 1,
        "spread": 0.0,
        "perk": "Armor-Piercing High Damage",
        "color": color.black,
        "scale": (0.2, 0.2, 1.3),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 0.85,
    },
    {
        "name": "Vampiric Reaper",
        "damage": 32,
        "cooldown": 0.28,
        "pellets": 1,
        "spread": 0.01,
        "perk": "Lifesteal (+6 HP per hit)",
        "color": color.red,
        "scale": (0.25, 0.25, 0.8),
        "lifesteal": 6,
        "aoe": 0.0,
        "speed_mult": 1.0,
    },
    {
        "name": "Plasma Launcher",
        "damage": 85,
        "cooldown": 0.7,
        "pellets": 1,
        "spread": 0.02,
        "perk": "Visual Plasma",
        "color": color.magenta,
        "scale": (0.35, 0.35, 0.85),
        "lifesteal": 0,
        "aoe": 7.0,
        "speed_mult": 0.9,
    },
    {
        "name": "Vulcan Minigun",
        "damage": 18,
        "cooldown": 0.05,
        "pellets": 1,
        "spread": 0.05,
        "perk": "Extreme Firerate Shredder",
        "color": color.gold,
        "scale": (0.4, 0.4, 1.1),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 0.8,
    },
    {
        "name": "Thunder Cannon",
        "damage": 60,
        "cooldown": 0.4,
        "pellets": 1,
        "spread": 0.01,
        "perk": "Electro Stun ",
        "color": color.cyan,
        "scale": (0.28, 0.28, 1.0),
        "lifesteal": 0,
        "aoe": 4.5,
        "speed_mult": 0.95,
    },
    {
        "name": "BFG 9000",
        "damage": 240,
        "cooldown": 1.1,
        "pellets": 1,
        "spread": 0.0,
        "perk": "Ultimate Mass Annihilator",
        "color": color.lime,
        "scale": (0.45, 0.45, 1.2),
        "lifesteal": 12,
        "aoe": 10.0,
        "speed_mult": 0.9,
    },
    # ---- NEW WEAPONS (reachable via scroll wheel / E key once unlocked) ----
    {
        "name": "Arc Railgun",
        "damage": 110,
        "cooldown": 0.95,
        "pellets": 1,
        "spread": 0.0,
        "perk": "Pierces Up To 5 Targets",
        "color": color.azure,
        "scale": (0.22, 0.22, 1.25),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 0.9,
        "pierce": 5,
        "slow": 0.0,
    },
    {
        "name": "Cryo Frost Rifle",
        "damage": 22,
        "cooldown": 0.11,
        "pellets": 1,
        "spread": 0.02,
        "perk": "Chills Enemies (-45% Speed)",
        "color": color.hsv(195, 0.35, 1.0),
        "scale": (0.22, 0.22, 0.9),
        "lifesteal": 0,
        "aoe": 0.0,
        "speed_mult": 1.0,
        "pierce": 0,
        "slow": 0.55,
    },
]

unlocked_weapons = [0]
current_weapon_idx = 0

def create_cylinder_mesh(radius=0.5, height=2, segments=8):
    verts = []
    triangles = []
    verts.append(Vec3(0, height, 0))
    verts.append(Vec3(0, 0, 0))
    for i in range(segments):
        angle = (i / segments) * math.pi * 2
        x = math.cos(angle) * radius
        z = math.sin(angle) * radius
        verts.append(Vec3(x, height, z))
        verts.append(Vec3(x, 0, z))

    for i in range(segments):
        top_curr = 2 + (i * 2)
        bot_curr = 3 + (i * 2)
        top_next = 2 + (((i + 1) % segments) * 2)
        bot_next = 3 + (((i + 1) % segments) * 2)
        triangles.append((top_curr, bot_curr, bot_next))
        triangles.append((top_curr, bot_next, top_next))
        triangles.append((0, top_next, top_curr))
        triangles.append((1, bot_curr, bot_next))
    return Mesh(vertices=verts, triangles=triangles, mode='triangle')

def create_cone_mesh(radius=1.0, height=2, segments=8):
    verts = [Vec3(0, height, 0), Vec3(0, 0, 0)]
    triangles = []
    for i in range(segments):
        angle = (i / segments) * math.pi * 2
        x = math.cos(angle) * radius
        z = math.sin(angle) * radius
        verts.append(Vec3(x, 0, z))
    for i in range(segments):
        curr = 2 + i
        nxt = 2 + ((i + 1) % segments)
        triangles.append((0, curr, nxt))
        triangles.append((1, nxt, curr))
    return Mesh(vertices=verts, triangles=triangles, mode='triangle')

def create_ring_mesh(inner_radius=0.8, outer_radius=1.0, segments=48):
    """Flat double-sided annulus (used for shockwaves). Radius 1.0 at scale 1."""
    verts = []
    triangles = []
    for i in range(segments):
        angle = (i / segments) * math.pi * 2
        cx = math.cos(angle)
        cz = math.sin(angle)
        verts.append(Vec3(cx * inner_radius, 0, cz * inner_radius))
        verts.append(Vec3(cx * outer_radius, 0, cz * outer_radius))
    for i in range(segments):
        i0 = i * 2
        o0 = i * 2 + 1
        i1 = ((i + 1) % segments) * 2
        o1 = ((i + 1) % segments) * 2 + 1
        triangles.append((i0, o0, o1))
        triangles.append((i0, o1, i1))
        triangles.append((i0, o1, o0))
        triangles.append((i0, i1, o1))
    return Mesh(vertices=verts, triangles=triangles, mode='triangle')

cylinder_mesh = create_cylinder_mesh()
cone_mesh = create_cone_mesh()

def play_shoot_sound():
    ursfx([(0.0, 0.0), (0.12, 0.98), (0.21, 0.3), (0.56, 0.06), (1.17, 0.0)], volume=1.0, wave='noise', pitch=-12, pitch_change=-12, speed=2.2)

def play_enemy_shoot_sound():
    ursfx([(0.0, 0.0), (0.16, 0.89), (0.3, 0.2), (0.38, 0.17), (0.5, 0.02)], volume=1.0, wave='noise', pitch=-13, pitch_change=-12, speed=2.0)

def play_reload_sound():
    ursfx([(0.0, 0.0), (0.08, 0.8), (0.15, 0.2), (0.25, 0.7), (0.4, 0.0)], volume=0.7, wave='square', pitch=8, pitch_change=-15, speed=2.2)

def play_hit_sound():
    ursfx([(0.0, 0.0), (0.09, 0.95), (0.14, 0.38), (0.19, 0.18), (0.4, 0.0)], volume=0.63, wave='noise', pitch_change=-5, speed=4.0)

def play_defeat_sound():
    ursfx([(0.0, 0.0), (0.14, 0.89), (0.23, 0.7), (0.33, 0.15), (1.0, 0.0)], volume=0.64, wave='triangle', pitch_change=-12, speed=0.8)

def play_spawn_sound():
    ursfx([(0.0, 1.0), (0.1, 0.5), (0.25, 0.5), (0.27, 0.5), (1.0, 0.0)], volume=0.55, wave='triangle', pitch=7, pitch_change=8, speed=1.2)

def play_player_hurt_sound():
    ursfx([(0.0, 0.0), (0.1, 0.81), (0.3, 0.38), (0.38, 0.13), (0.57, 0.01)], volume=0.44, wave='square', pitch=-11, pitch_change=-12, speed=2.0)

def play_pickup_sound():
    ursfx([(0.0, 0.0), (0.08, 0.9), (0.18, 0.95), (0.3, 0.0), (0.4, 0.0)], volume=0.85, wave='sine', pitch=12, pitch_change=15, speed=2.5)

def play_weapon_pickup_sound():
    ursfx([(0.0, 0.0), (0.1, 0.9), (0.2, 0.7), (0.35, 1.0), (0.5, 0.0)], volume=0.9, wave='square', pitch=5, pitch_change=18, speed=2.0)

def play_aoe_sound():
    ursfx([(0.0, 0.0), (0.2, 1.0), (0.4, 0.5), (0.7, 0.1), (1.0, 0.0)], volume=0.95, wave='noise', pitch=-20, pitch_change=-10, speed=1.5)

# ---- NEW sounds (all wrapped so a missing audio device can never crash the game loop) ----
def _safe_sfx(curve_points, **kwargs):
    try:
        ursfx(curve_points, **kwargs)
    except Exception as e:
        debug_log('ursfx', e)

def play_dash_sound():
    _safe_sfx([(0.0, 0.0), (0.06, 0.85), (0.2, 0.45), (0.45, 0.0)], volume=0.5, wave='noise', pitch=4, pitch_change=-8, speed=3.0)

def play_grenade_throw_sound():
    _safe_sfx([(0.0, 0.0), (0.08, 0.7), (0.2, 0.4), (0.35, 0.0)], volume=0.5, wave='triangle', pitch=-2, pitch_change=6, speed=2.4)

def play_pulse_sound():
    _safe_sfx([(0.0, 0.0), (0.15, 1.0), (0.4, 0.6), (0.8, 0.1), (1.0, 0.0)], volume=0.8, wave='sine', pitch=-14, pitch_change=-6, speed=1.4)

def play_perk_sound():
    _safe_sfx([(0.0, 0.0), (0.1, 0.8), (0.2, 0.9), (0.35, 0.6), (0.6, 0.0)], volume=0.7, wave='sine', pitch=14, pitch_change=12, speed=2.0)

def play_boss_roar_sound():
    _safe_sfx([(0.0, 0.0), (0.2, 1.0), (0.5, 0.8), (0.9, 0.3), (1.4, 0.0)], volume=0.95, wave='noise', pitch=-22, pitch_change=-10, speed=0.8)

def play_leap_sound():
    _safe_sfx([(0.0, 0.0), (0.1, 0.8), (0.25, 0.5), (0.5, 0.0)], volume=0.5, wave='triangle', pitch=2, pitch_change=10, speed=2.4)

def play_blink_sound():
    _safe_sfx([(0.0, 0.0), (0.05, 0.9), (0.15, 0.3), (0.3, 0.0)], volume=0.45, wave='sine', pitch=16, pitch_change=-14, speed=3.0)

def play_bomber_beep_sound():
    _safe_sfx([(0.0, 0.0), (0.05, 0.9), (0.1, 0.0), (0.15, 0.9), (0.25, 0.0)], volume=0.5, wave='square', pitch=14, pitch_change=0, speed=3.0)

def play_overdrive_sound():
    _safe_sfx([(0.0, 0.0), (0.2, 0.9), (0.4, 1.0), (0.7, 0.4), (1.0, 0.0)], volume=0.8, wave='square', pitch=2, pitch_change=20, speed=1.6)

def play_slam_sound():
    _safe_sfx([(0.0, 0.0), (0.1, 1.0), (0.3, 0.6), (0.7, 0.1), (1.0, 0.0)], volume=1.0, wave='noise', pitch=-24, pitch_change=-8, speed=1.2)

def play_summon_sound():
    _safe_sfx([(0.0, 0.0), (0.2, 0.7), (0.4, 0.9), (0.8, 0.0)], volume=0.6, wave='triangle', pitch=-6, pitch_change=10, speed=1.4)

sky = Sky(color=color.azure)
sun = DirectionalLight()
sun.look_at(Vec3(1, -1, 1))

ground = Entity(model='plane', collider='box', scale=MAP_SIZE, texture='grass', texture_scale=(32, 32))
scene_parent = Entity()
bullets_parent = Entity()
pickups_parent = Entity()
particles_parent = Entity()

for _ in range(30):
    tx, tz = random.uniform(-MAP_SIZE/2, MAP_SIZE/2), random.uniform(-MAP_SIZE/2, MAP_SIZE/2)
    if abs(tx) > 10 or abs(tz) > 10:
        Entity(parent=scene_parent, model=cylinder_mesh, color=color.brown, scale=(0.8, 2, 0.8), position=(tx, 0, tz))
        Entity(parent=scene_parent, model=cone_mesh, color=color.green, scale=(3, 4, 3), position=(tx, 3, tz))

for _ in range(40):
    rx, rz = random.uniform(-MAP_SIZE/2, MAP_SIZE/2), random.uniform(-MAP_SIZE/2, MAP_SIZE/2)
    if abs(rx) > 10 or abs(rz) > 10:
        Entity(parent=scene_parent, model='cube', color=color.gray, 
               scale=(random.uniform(2, 5), random.uniform(2, 4), random.uniform(2, 5)), 
               position=(rx, 1, rz), collider='box')

player = FirstPersonController(model='cube', z=0, color=color.orange, origin_y=-.5, speed=10, collider='box')
player.collider = BoxCollider(player, Vec3(0, 1, 0), Vec3(1, 2, 1))
player.max_hp = 125
player.hp = player.max_hp
player.dead = False

shootables_parent = Entity()

gun = Entity(model='cube', parent=camera, position=(.5, -.25, .5), scale=(.2, .2, .8), origin_z=-.5, color=color.dark_gray, on_cooldown=False)
gun.muzzle_flash = Entity(parent=gun, z=1, world_scale=.4, model='quad', color=color.yellow, enabled=False)

score_text = Text(text=f"Score: {score}  |  Wave: {wave}", position=(-0.85, 0.45), scale=1.5, color=color.yellow)
crosshair = Entity(model='quad', color=color.rgba(255, 255, 255, 180), scale=0.015, parent=camera.ui)

health_bar_bg = Entity(parent=camera.ui, model='quad', color=color.black, scale=(0.4, 0.035), position=(-0.65, -0.42))
health_bar = Entity(parent=camera.ui, model='quad', color=color.green, scale=(0.39, 0.025), position=(-0.845, -0.42), origin_x=-0.5)
health_text = Text(text="HP", position=(-0.88, -0.41), scale=1.2, color=color.white)

weapon_ui_text = Text(text="", position=(0.25, -0.4), scale=1.3, color=color.gold)
perk_ui_text = Text(text="", position=(0.25, -0.44), scale=1.0, color=color.cyan)

pickup_notification = Text(text="", position=(0, 0.25), origin=(0, 0), scale=1.8, color=color.green, enabled=False)

damage_overlay = Entity(parent=camera.ui, model='quad', color=color.rgba(255, 0, 0, 0), scale=(2, 2), z=-1)

death_bg = Entity(parent=camera.ui, model='quad', color=color.rgba(120, 0, 0, .6), scale=(2, 2), z=-2, enabled=False)
death_title = Text(text="You died!", origin=(0, 0), position=(0, 0.18), scale=3.5, color=color.rgba(250, 10, 10, 1), parent=camera.ui, z=-3, enabled=False)
death_score_text = Text(text="", origin=(0, 0), position=(0, 0.04), scale=1.8, color=color.white, parent=camera.ui, z=-3, enabled=False)

# ---- NEW HUD ELEMENTS (upgrade) ----
death_stats_text = Text(text="", origin=(0, 0), position=(0, -0.025), scale=1.1, color=color.white, parent=camera.ui, z=-3, enabled=False)

tier_text = Text(text="", position=(-0.85, 0.405), scale=1.0, color=color.orange)
kills_text = Text(text="", position=(-0.85, 0.37), scale=0.9, color=color.white)
combo_text = Text(text="", position=(0.45, 0.45), scale=1.4, color=color.orange)
ability_text = Text(text="", position=(-0.88, -0.35), scale=0.95, color=color.white)

overdrive_bar_bg = Entity(parent=camera.ui, model='quad', color=color.black, scale=(0.4, 0.014), position=(-0.65, -0.465))
overdrive_bar = Entity(parent=camera.ui, model='quad', color=color.cyan, scale=(0.0005, 0.009), position=(-0.845, -0.465), origin_x=-0.5)

banner_text = Text(text="", origin=(0, 0), position=(0, 0.33), scale=2.4, color=color.yellow, enabled=False)
modifier_text = Text(text="", origin=(0, 0), position=(0, 0.375), scale=1.1, color=color.orange, enabled=False)

boss_name_text = Text(text="", origin=(0, 0), position=(0, 0.465), scale=1.3, color=color.red, enabled=False)
boss_bar_bg = Entity(parent=camera.ui, model='quad', color=color.black, scale=(0.62, 0.03), position=(0, 0.425), enabled=False)
boss_bar = Entity(parent=camera.ui, model='quad', color=color.red, scale=(0.6, 0.02), position=(-0.3, 0.425), origin_x=-0.5, z=-0.1, enabled=False)

perk_panel_text = Text(text="", origin=(0, 0), position=(0, -0.2), scale=1.05, color=color.white, enabled=False)

fx_overlay = Entity(parent=camera.ui, model='quad', color=rgba01(1.0, 1.0, 1.0, 0.0), scale=(2, 2), z=-1.5)
hitmarker_a = Entity(parent=camera.ui, model='quad', color=color.white, scale=(0.034, 0.004), rotation_z=45, z=-1.6, enabled=False)
hitmarker_b = Entity(parent=camera.ui, model='quad', color=color.white, scale=(0.034, 0.004), rotation_z=-45, z=-1.6, enabled=False)

def restart_game_wrapper():
    restart_game()

respawn_button = Button(text="Respawn (Press R)", color=color.rgba(80, 80, 80, 1), highlight_color=color.rgba(20, 20, 20, 1),
                        scale=(0.36, 0.08), position=(0, -0.12), parent=camera.ui, z=-3, enabled=False, on_click=restart_game_wrapper)

_notification_token = 0

def _hide_notification(token):
    if token == _notification_token and is_entity_valid(pickup_notification):
        pickup_notification.enabled = False

def show_notification(msg, msg_color=color.green, duration=2.0):
    global _notification_token
    _notification_token += 1
    pickup_notification.text = msg
    pickup_notification.color = msg_color
    pickup_notification.enabled = True
    invoke(_hide_notification, _notification_token, delay=duration)

def safe_destroy(entity):
    if not entity:
        return
    if hasattr(entity, 'isEmpty') and entity.isEmpty():
        return
    try:
        destroy(entity)
    except Exception:
        pass

def safe_blink(entity, flash_color=color.white, duration=0.1):
    if not entity or getattr(entity, 'dead', False):
        return
    if hasattr(entity, 'isEmpty') and entity.isEmpty():
        return
    try:
        orig_color = entity.color
        entity.color = flash_color
        def reset_color():
            if entity and not getattr(entity, 'dead', False):
                if not (hasattr(entity, 'isEmpty') and entity.isEmpty()):
                    try:
                        entity.color = orig_color
                    except Exception:
                        pass
        invoke(reset_color, delay=duration)
    except Exception:
        pass

# ---- NEW UI / feedback helpers ----
def set_text_safe(text_entity, new_text):
    """Only regenerates Text geometry when the string actually changed (Text rebuilds are expensive)."""
    try:
        key = id(text_entity)
        if _hud_cache.get(key) != new_text:
            text_entity.text = new_text
            _hud_cache[key] = new_text
    except Exception as e:
        debug_log('set_text_safe', e)

def show_banner(msg, banner_color=color.yellow, duration=2.5):
    global banner_timer
    try:
        banner_text.text = msg
        banner_text.color = banner_color
        banner_text.enabled = True
        banner_timer = duration
    except Exception as e:
        debug_log('show_banner', e)

def show_hitmarker(kill=False):
    global hitmarker_timer
    try:
        col = color.red if kill else color.white
        hitmarker_a.color = col
        hitmarker_b.color = col
        hitmarker_a.enabled = True
        hitmarker_b.enabled = True
        hitmarker_timer = 0.12 if kill else 0.07
    except Exception as e:
        debug_log('show_hitmarker', e)

def flash_screen(r, g, b, alpha=0.25):
    global fx_overlay_alpha, fx_overlay_rgb
    fx_overlay_rgb = (r, g, b)
    fx_overlay_alpha = max(fx_overlay_alpha, alpha)

def add_fov_kick(amount):
    global fov_kick
    fov_kick = min(25.0, fov_kick + amount)

def set_sky_blood(on):
    global sky_blood_active
    try:
        if on == sky_blood_active:
            return
        sky_blood_active = on
        if is_entity_valid(sky):
            sky.color = color.hsv(8, 0.7, 0.65) if on else color.azure
    except Exception as e:
        debug_log('set_sky_blood', e)

def get_tier_name():
    tiers = ["Recruit", "Veteran", "Elite", "Nightmare", "Apocalypse", "Cataclysm", "Oblivion"]
    if wave < 5:
        idx = 0
    elif wave < 10:
        idx = 1
    elif wave < 15:
        idx = 2
    elif wave < 20:
        idx = 3
    elif wave < 30:
        idx = 4
    elif wave < 40:
        idx = 5
    else:
        idx = 6
    name = tiers[idx]
    if wave >= 50:
        name = f"{name} {((wave - 50) // 10) + 1}"
    return name

def heal_player(amount):
    if not player or player.dead:
        return
    player.hp = min(player.max_hp, player.hp + amount)
    update_health_bar()

def push_player(from_pos, strength):
    """Horizontal shove away from from_pos, clamped to the map."""
    try:
        dx = player.x - from_pos[0]
        dz = player.z - from_pos[2]
        length = math.sqrt(dx * dx + dz * dz)
        if length < 0.001:
            dx, dz, length = 0.0, 1.0, 1.0
        player.x += (dx / length) * strength
        player.z += (dz / length) * strength
        clamp_player_to_map()
    except Exception as e:
        debug_log('push_player', e)

def clamp_player_to_map():
    limit = MAP_SIZE / 2 - 2
    try:
        if player.x > limit:
            player.x = limit
        elif player.x < -limit:
            player.x = -limit
        if player.z > limit:
            player.z = limit
        elif player.z < -limit:
            player.z = -limit
        if player.y < -25:
            player.y = 2
    except Exception as e:
        debug_log('clamp_player_to_map', e)

def gain_overdrive(amount):
    global overdrive_charge
    if overdrive_time_left > 0:
        return
    before = overdrive_charge
    overdrive_charge = min(100.0, overdrive_charge + amount)
    if before < 100.0 <= overdrive_charge:
        show_notification("OVERDRIVE READY!  Press F", color.cyan)

def add_grenades(amount):
    global grenade_count
    grenade_count = min(player_stats["grenade_cap"], grenade_count + amount)

def refresh_player_speed():
    try:
        if player.dead:
            return
        w = WEAPONS[current_weapon_idx]
        boost = 1.2 if overdrive_time_left > 0 else 1.0
        player.speed = 10 * w["speed_mult"] * player_stats["speed_mult"] * boost
    except Exception as e:
        debug_log('refresh_player_speed', e)

def switch_weapon(idx):
    global current_weapon_idx
    if idx not in unlocked_weapons:
        return
    current_weapon_idx = idx
    w = WEAPONS[current_weapon_idx]
    gun.color = w["color"]
    gun.scale = w["scale"]
    player.speed = 10 * w["speed_mult"] * player_stats["speed_mult"] * (1.2 if overdrive_time_left > 0 else 1.0)
    
    weapon_ui_text.text = f"[{idx + 1}] {w['name']}"
    perk_ui_text.text = f"Perk: {w['perk']}"

switch_weapon(0)

def damage_player(amount):
    global game_is_over, last_damage_time, combo_count
    if game_is_over or player.dead:
        return

    # NEW: dash i-frames make the player briefly untouchable
    if player_invuln_timer > 0:
        return

    player.hp -= amount
    last_damage_time = game_time
    if combo_count > 0:
        combo_count = combo_count // 2
    update_health_bar()

    play_player_hurt_sound()

    damage_overlay.color = color.rgba(255, 0, 0, .3)
    damage_overlay.animate_color(color.rgba(255, 0, 0, 0), duration=0.3)

    if player.hp <= 0:
        player.dead = True
        game_is_over = True
        death_bg.enabled = True
        death_title.enabled = True
        death_score_text.text = f"Score: {score}"
        death_score_text.enabled = True
        try:
            death_stats_text.text = f"Wave {wave} ({get_tier_name()})  |  Kills {kills}  |  Best Combo x{combo_best}  |  Bosses Slain {bosses_defeated}"
            death_stats_text.enabled = True
            perk_panel_text.enabled = False
            banner_text.enabled = False
            boss_bar_bg.enabled = False
            boss_bar.enabled = False
            boss_name_text.enabled = False
            fx_overlay.color = rgba01(1.0, 1.0, 1.0, 0.0)
        except Exception as e:
            debug_log('damage_player.death_stats', e)
        respawn_button.enabled = True
        player.speed = 0
        mouse.locked = False

def update_health_bar():
    health_percentage = max(0.001, min(1.0, player.hp / player.max_hp))
    health_bar.scale_x = 0.39 * health_percentage
    if host_mode:
        health_bar.color = color.cyan
    elif health_percentage < 0.3:
        health_bar.color = color.red
    elif health_percentage < 0.6:
        health_bar.color = color.yellow
    else:
        health_bar.color = color.green

# ----------------------------------------------------------------------------
# NEW FX CLASSES (particles, beams, rings) - all self-contained & anti-assertion guarded
# ----------------------------------------------------------------------------
class FXParticle(Entity):
    def __init__(self, position, velocity, particle_color, size=0.15, lifetime=0.5, gravity=9.0, **kwargs):
        kw = {'parent': particles_parent, 'model': 'cube', 'color': particle_color, 'scale': size, 'position': position}
        kw.update(fx_shader_kwargs())
        kw.update(kwargs)
        super().__init__(**kw)
        self.vx = velocity[0]
        self.vy = velocity[1]
        self.vz = velocity[2]
        self.base_size = size
        self.fx_life = max(0.05, lifetime)
        self.fx_age = 0.0
        self.fx_gravity = gravity

    def update(self):
        if not self:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return
        try:
            dt = time.dt
            self.fx_age += dt
            if self.fx_age >= self.fx_life:
                safe_destroy(self)
                return
            self.vy -= self.fx_gravity * dt
            self.x += self.vx * dt
            self.y += self.vy * dt
            self.z += self.vz * dt
            if self.y < 0.05:
                self.y = 0.05
                self.vy = 0.0
                self.vx *= 0.8
                self.vz *= 0.8
            self.scale = max(0.02, self.base_size * (1.0 - (self.fx_age / self.fx_life)))
        except Exception as e:
            debug_log('FXParticle.update', e)
            safe_destroy(self)


class BeamFX(Entity):
    def __init__(self, start, end, beam_color, thickness=0.06, life=0.12, **kwargs):
        s = Vec3(start[0], start[1], start[2])
        t = Vec3(end[0], end[1], end[2])
        length = max(0.05, distance(s, t))
        kw = {'parent': particles_parent, 'model': 'cube', 'color': beam_color,
              'scale': (thickness, thickness, length), 'position': (s + t) * 0.5}
        kw.update(fx_shader_kwargs())
        kw.update(kwargs)
        super().__init__(**kw)
        self.beam_rgb = (beam_color[0], beam_color[1], beam_color[2])
        self.beam_alpha = beam_color[3]
        self.fx_life = max(0.03, life)
        self.fx_age = 0.0
        try:
            self.look_at(t)
        except Exception as e:
            debug_log('BeamFX.look_at', e)

    def update(self):
        if not self:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return
        try:
            self.fx_age += time.dt
            p = self.fx_age / self.fx_life
            if p >= 1.0:
                safe_destroy(self)
                return
            self.color = rgba01(self.beam_rgb[0], self.beam_rgb[1], self.beam_rgb[2], self.beam_alpha * (1.0 - p))
        except Exception as e:
            debug_log('BeamFX.update', e)
            safe_destroy(self)


class RingFX(Entity):
    """Flat expanding disc. mode='fade' expands + fades (impact), mode='hold' expands at constant alpha (telegraph)."""
    def __init__(self, position, ring_color, max_radius=6.0, life=0.4, start_radius=0.5, mode='fade', **kwargs):
        kw = {'parent': particles_parent, 'model': create_cylinder_mesh(radius=0.5, height=1, segments=36),
              'color': ring_color, 'scale': (start_radius * 2, 0.06, start_radius * 2),
              'position': Vec3(position[0], 0.06, position[2]), 'double_sided': True}
        kw.update(fx_shader_kwargs())
        kw.update(kwargs)
        super().__init__(**kw)
        self.ring_rgb = (ring_color[0], ring_color[1], ring_color[2])
        self.ring_alpha = ring_color[3]
        self.max_radius = max_radius
        self.start_radius = start_radius
        self.fx_life = max(0.05, life)
        self.fx_age = 0.0
        self.fx_mode = mode

    def update(self):
        if not self:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return
        try:
            self.fx_age += time.dt
            p = self.fx_age / self.fx_life
            if p >= 1.0:
                safe_destroy(self)
                return
            r = lerp(self.start_radius, self.max_radius, p)
            self.scale_x = r * 2
            self.scale_z = r * 2
            a = self.ring_alpha * (1.0 - p) if self.fx_mode == 'fade' else self.ring_alpha
            self.color = rgba01(self.ring_rgb[0], self.ring_rgb[1], self.ring_rgb[2], a)
        except Exception as e:
            debug_log('RingFX.update', e)
            safe_destroy(self)


def spawn_burst(position, burst_color, count=8, speed=6.0, size=0.18, lifetime=0.55, gravity=9.0, up_bias=0.5):
    """Capped particle burst. Never raises."""
    try:
        if not is_entity_valid(particles_parent):
            return
        existing = len(particles_parent.children)
        if existing >= MAX_PARTICLES:
            return
        count = min(count, MAX_PARTICLES - existing)
        origin = Vec3(position[0], position[1], position[2])
        for _ in range(count):
            vel = Vec3(random.uniform(-1, 1), random.uniform(0, 1) + up_bias, random.uniform(-1, 1))
            if vel.length() < 0.01:
                vel = Vec3(0, 1, 0)
            vel = vel.normalized() * speed * random.uniform(0.4, 1.0)
            FXParticle(position=origin, velocity=vel, particle_color=burst_color,
                       size=size * random.uniform(0.6, 1.2), lifetime=lifetime * random.uniform(0.7, 1.2), gravity=gravity)
    except Exception as e:
        debug_log('spawn_burst', e)


def spawn_beam(start, end, beam_color, thickness=0.06, life=0.12):
    try:
        if not is_entity_valid(particles_parent):
            return
        if len(particles_parent.children) >= MAX_PARTICLES + 40:
            return
        BeamFX(start, end, beam_color, thickness=thickness, life=life)
    except Exception as e:
        debug_log('spawn_beam', e)


def spawn_ring(position, ring_color, max_radius=6.0, life=0.4, start_radius=0.5, mode='fade'):
    try:
        if not is_entity_valid(particles_parent):
            return
        RingFX(position, ring_color, max_radius=max_radius, life=life, start_radius=start_radius, mode=mode)
    except Exception as e:
        debug_log('spawn_ring', e)


def trim_pickups(limit=80):
    """Late-game safety: never let uncollected pickups pile up forever (oldest go first). Skipped in host mode."""
    try:
        if host_mode:
            return
        kids = list(pickups_parent.children)
        extra = len(kids) - limit
        if extra > 0:
            for old_pickup in kids[:extra]:
                safe_destroy(old_pickup)
    except Exception as e:
        debug_log('trim_pickups', e)


class Pickup(Entity):
    def __init__(self, pickup_type='health', weapon_idx=0, position=Vec3(0, 0.8, 0), **kwargs):
        super().__init__(parent=pickups_parent, position=position, **kwargs)
        self.pickup_type = pickup_type
        self.weapon_idx = weapon_idx
        self.start_y = position.y
        self.hover_time = random.uniform(0, 10)
        self.pickup_age = 0.0

        if pickup_type == 'health':
            self.model_core = Entity(parent=self, model='cube', color=color.red, scale=(0.4, 0.6, 0.4))
            self.model_cap = Entity(parent=self, model='cube', color=color.white, scale=(0.2, 0.2, 0.2), position=(0, 0.4, 0))
            self.aura = Entity(parent=self, model='sphere', color=color.rgba(255, 0, 0, 80), scale=0.9)
        elif pickup_type == 'grenade':
            self.model_core = Entity(parent=self, model='sphere', color=color.hsv(120, 0.6, 0.35), scale=0.5)
            self.model_cap = Entity(parent=self, model='cube', color=color.yellow, scale=(0.16, 0.16, 0.16), position=(0, 0.35, 0))
            self.aura = Entity(parent=self, model='sphere', color=rgba01(1.0, 0.6, 0.1, 0.3), scale=1.0)
        elif pickup_type == 'overdrive':
            self.model_core = Entity(parent=self, model='cube', color=color.cyan, scale=(0.45, 0.45, 0.45))
            self.aura = Entity(parent=self, model='sphere', color=rgba01(0.2, 0.9, 1.0, 0.3), scale=1.0)
        else:
            w = WEAPONS[weapon_idx]
            self.model_core = Entity(parent=self, model='cube', color=w["color"], scale=(0.6, 0.3, 0.6))
            self.aura = Entity(parent=self, model='sphere', color=color.rgba(255, 215, 0, 100), scale=1.0)
        trim_pickups()

    def update(self):
        if not self or player.dead or game_is_over:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return

        self.hover_time += time.dt
        self.rotation_y += 90 * time.dt
        self.y = self.start_y + math.sin(self.hover_time * 3) * 0.25

        # NEW pickup types expire so they never pile up; health & weapons persist exactly like before
        if self.pickup_type in ('grenade', 'overdrive'):
            self.pickup_age += time.dt
            if self.pickup_age > 40.0:
                safe_destroy(self)
                return

        if distance(self.position, player.position + Vec3(0, 1, 0)) < 1.8:
            try:
                if self.pickup_type == 'health':
                    player.hp = min(player.max_hp, player.hp + 35)
                    update_health_bar()
                    play_pickup_sound()
                    show_notification("+35 HP Restored!", color.green)
                elif self.pickup_type == 'weapon':
                    if self.weapon_idx not in unlocked_weapons:
                        unlocked_weapons.append(self.weapon_idx)
                        unlocked_weapons.sort()
                    switch_weapon(self.weapon_idx)
                    play_weapon_pickup_sound()
                    w_name = WEAPONS[self.weapon_idx]["name"]
                    show_notification(f"Unlocked Weapon: {w_name}!", color.yellow)
                elif self.pickup_type == 'grenade':
                    add_grenades(1)
                    play_pickup_sound()
                    show_notification("+1 Grenade!", color.orange)
                elif self.pickup_type == 'overdrive':
                    gain_overdrive(35.0)
                    play_pickup_sound()
                    show_notification("+35% Overdrive Charge!", color.cyan)
            except Exception as e:
                debug_log('Pickup.collect', e)

            safe_destroy(self)

class EnemyBullet(Entity):
    def __init__(self, position, direction, damage=15, speed=18.0, bullet_color=None, size=0.3, **kwargs):
        col = bullet_color if bullet_color is not None else color.orange
        super().__init__(parent=bullets_parent, model='sphere', color=col, scale=size, position=position, collider='sphere', **kwargs)
        self.direction = direction.normalized()
        self.speed = speed
        self.damage = damage
        self.dead = False
        self.lifetime = 0.0

    def update(self):
        if not self or self.dead or player.dead or game_is_over:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return

        self.position += self.direction * self.speed * time.dt
        self.lifetime += time.dt

        if self.lifetime > 5.0:
            self.dead = True
            safe_destroy(self)
            return

        if distance(self.position, player.position + Vec3(0, 1, 0)) < 1.2:
            damage_player(self.damage)
            self.dead = True
            safe_destroy(self)

class AOEExplosion(Entity):
    def __init__(self, position, radius=6.0, max_damage=75, color_theme=color.magenta, lifesteal=0, **kwargs):
        super().__init__(position=position, **kwargs)
        self.radius = radius
        self.max_damage = max_damage
        self.color_theme = color_theme
        self.lifesteal = lifesteal
        self.lifetime = 0.0
        self.duration = 0.45

        cylinder_mesh_aoe = create_cylinder_mesh(radius=0.5, height=2, segments=108)
        self.ring = Entity(parent=self, model=cylinder_mesh_aoe, color=color_theme, scale=(0.1, 0.2, 0.1), position=(0, 0.05, 0))
        self.ring.color = color.rgba(color_theme.r, color_theme.g, color_theme.b, 220)
        
        self.core = Entity(parent=self, model='sphere', color=color.white, scale=(0.8, 0.8, 0.8), position=(0, 0.6, 0))
        
        self.flash = Entity(parent=self, model='sphere', color=color_theme, scale=radius * 0.7, position=(0, 0.6, 0))
        self.flash.color = color.rgba(color_theme.r, color_theme.g, color_theme.b, 130)
        
        play_aoe_sound()
        self.deal_aoe_damage()
        add_fov_kick(min(8.0, radius * 0.9))

    def deal_aoe_damage(self):
        if not shootables_parent:
            return
            
        active_enemies = [e for e in list(shootables_parent.children) if e and not getattr(e, 'dead', False)]
        total_lifesteal = 0

        for enemy in active_enemies:
            if not enemy or getattr(enemy, 'dead', False):
                continue
            if hasattr(enemy, 'isEmpty') and enemy.isEmpty():
                continue

            try:
                dist = distance(enemy.position, self.position)
                if dist <= self.radius:
                    falloff = max(0.3, 1.0 - (dist / self.radius))
                    damage_to_apply = self.max_damage * falloff

                    if not getattr(enemy, 'is_dummy', False):
                        knockback_dir = (enemy.position - self.position).normalized()
                        knockback_dir.y = 0.2
                        enemy.position += knockback_dir * (3.5 * falloff * (1.0 - getattr(enemy, 'knockback_resist', 0.0)))

                    enemy.hp -= damage_to_apply
                    safe_blink(enemy, color.yellow, 0.1)

                    if self.lifesteal > 0:
                        total_lifesteal += self.lifesteal
            except Exception as e:
                print(f"DEBUG AOE Damage Error: {e}")

        if total_lifesteal > 0 and player and not player.dead:
            player.hp = min(player.max_hp, player.hp + total_lifesteal)
            update_health_bar()

    def update(self):
        if not self:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return

        self.lifetime += time.dt
        progress = self.lifetime / self.duration

        if progress >= 1.0:
            safe_destroy(self)
            return

        current_ring_scale = lerp(0.5, self.radius * 2.0, progress)
        self.ring.scale_x = current_ring_scale
        self.ring.scale_z = current_ring_scale

        alpha = lerp(220, 0, progress)
        self.ring.color = color.rgba(self.color_theme.r, self.color_theme.g, self.color_theme.b, int(alpha))
        self.flash.color = color.rgba(self.color_theme.r, self.color_theme.g, self.color_theme.b, int(alpha * 0.5))
        self.core.scale = Vec3(lerp(1.5, 0.05, progress))


class Grenade(Entity):
    """Player-thrown frag grenade (G). Arcs, bounces, detonates on fuse or enemy proximity."""
    def __init__(self, position, velocity, blast_radius=7.0, blast_damage=130, **kwargs):
        super().__init__(parent=bullets_parent, model='sphere', color=color.hsv(110, 0.7, 0.5), scale=0.38, position=position, **kwargs)
        self.gvx = velocity[0]
        self.gvy = velocity[1]
        self.gvz = velocity[2]
        self.blast_radius = blast_radius
        self.blast_damage = blast_damage
        self.fuse = 1.7
        self.dead = False
        self.trail_timer = 0.0

    def update(self):
        if not self or self.dead or player.dead or game_is_over:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return
        try:
            dt = time.dt
            self.fuse -= dt
            self.gvy -= 24.0 * dt
            self.x += self.gvx * dt
            self.y += self.gvy * dt
            self.z += self.gvz * dt
            if self.y <= 0.3 and self.gvy < 0:
                self.y = 0.3
                self.gvy = -self.gvy * 0.35
                self.gvx *= 0.6
                self.gvz *= 0.6

            self.trail_timer -= dt
            if self.trail_timer <= 0:
                self.trail_timer = 0.06
                spawn_burst(self.position, color.orange, count=1, speed=0.8, size=0.1, lifetime=0.3, gravity=0.0, up_bias=0.0)

            trigger = self.fuse <= 0
            if not trigger:
                for en in list(shootables_parent.children):
                    if not en or getattr(en, 'dead', False):
                        continue
                    if hasattr(en, 'isEmpty') and en.isEmpty():
                        continue
                    if distance(en.position + Vec3(0, 1, 0), self.position) < 1.8:
                        trigger = True
                        break
            if trigger:
                self.detonate()
        except Exception as e:
            debug_log('Grenade.update', e)
            self.dead = True
            safe_destroy(self)

    def detonate(self):
        if self.dead:
            return
        self.dead = True
        pos = Vec3(self.x, max(0.2, self.y), self.z)
        try:
            AOEExplosion(position=pos, radius=self.blast_radius, max_damage=self.blast_damage, color_theme=color.orange, lifesteal=0)
            spawn_burst(pos, color.orange, count=14, speed=9.0, size=0.25, lifetime=0.6)
            spawn_burst(pos, color.yellow, count=8, speed=6.0, size=0.18, lifetime=0.45)
        except Exception as e:
            debug_log('Grenade.detonate', e)
        safe_destroy(self)


class ShockwaveRing(Entity):
    """Boss shockwave: an expanding ring on the ground. JUMP (or dash) to avoid it."""
    def __init__(self, position, max_radius=16.0, duration=0.9, damage=28, ring_color=None, **kwargs):
        col = ring_color if ring_color is not None else rgba01(1.0, 0.55, 0.15, 0.7)
        kw = {'parent': bullets_parent, 'model': create_ring_mesh(0.82, 1.0, 48), 'color': col,
              'scale': (1, 1, 1), 'position': Vec3(position[0], 0.15, position[2]), 'double_sided': True}
        kw.update(fx_shader_kwargs())
        kw.update(kwargs)
        super().__init__(**kw)
        self.max_radius = max_radius
        self.duration = max(0.1, duration)
        self.wave_damage = damage
        self.wave_age = 0.0
        self.has_hit = False
        self.dead = False

    def update(self):
        if not self or self.dead or player.dead or game_is_over:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return
        try:
            self.wave_age += time.dt
            p = self.wave_age / self.duration
            if p >= 1.0:
                self.dead = True
                safe_destroy(self)
                return
            r = lerp(1.0, self.max_radius, p)
            self.scale_x = r
            self.scale_z = r
            if not self.has_hit:
                d = distance_xz(player.position, self.position)
                if (r * 0.78) <= d <= (r + 0.8) and player.y < 1.1:
                    self.has_hit = True
                    damage_player(self.wave_damage)
                    push_player(self.position, 3.0)
        except Exception as e:
            debug_log('ShockwaveRing.update', e)
            self.dead = True
            safe_destroy(self)


class StrikeZone(Entity):
    """Delayed ground strike with a visible telegraph. Step out of the circle before it lands."""
    def __init__(self, position, radius=3.2, delay=1.2, damage=30, **kwargs):
        super().__init__(parent=bullets_parent, position=Vec3(position[0], 0, position[2]), **kwargs)
        self.zone_radius = radius
        self.zone_delay = max(0.1, delay)
        self.zone_timer = 0.0
        self.zone_damage = damage
        self.dead = False
        spawn_ring(self.position, rgba01(1.0, 0.85, 0.2, 0.35), max_radius=radius, life=self.zone_delay, start_radius=0.3, mode='hold')

    def update(self):
        if not self or self.dead or player.dead or game_is_over:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return
        try:
            self.zone_timer += time.dt
            if self.zone_timer >= self.zone_delay:
                self.strike()
        except Exception as e:
            debug_log('StrikeZone.update', e)
            self.dead = True
            safe_destroy(self)

    def strike(self):
        if self.dead:
            return
        self.dead = True
        try:
            if distance_xz(player.position, self.position) < self.zone_radius and player.y < 2.5:
                damage_player(self.zone_damage)
            spawn_burst((self.x, 0.5, self.z), color.yellow, count=12, speed=8.0, size=0.2, lifetime=0.5)
            spawn_beam((self.x + 0.4, 0.0, self.z), (self.x, 30.0, self.z), rgba01(1.0, 0.95, 0.5, 0.9), thickness=0.5, life=0.18)
            spawn_ring((self.x, 0, self.z), rgba01(1.0, 0.9, 0.3, 0.6), max_radius=self.zone_radius, life=0.25, start_radius=self.zone_radius * 0.5)
            play_slam_sound()
        except Exception as e:
            debug_log('StrikeZone.strike', e)
        safe_destroy(self)

COL_VIOLET = color.hsv(280, 0.9, 1.0)


def clamp_xz(x, z):
    limit = MAP_SIZE / 2 - 3
    return max(-limit, min(limit, x)), max(-limit, min(limit, z))


def inherit_scaling(child, parent):
    """Children (splits / summons) inherit the late-game scaling of whoever created them."""
    try:
        hp_scale = getattr(parent, 'hp_scale', 1.0)
        speed_scale = getattr(parent, 'speed_scale', 1.0)
        dmg_scale = getattr(parent, 'dmg_scale', 1.0)
        if hp_scale != 1.0:
            child.max_hp = child.max_hp * hp_scale
            child._hp = child._hp * hp_scale
        if speed_scale != 1.0:
            child.speed = child.speed * speed_scale
        if dmg_scale != 1.0:
            child.dmg_scale = dmg_scale
            if hasattr(child, 'melee_damage'):
                child.melee_damage = child.melee_damage * dmg_scale
        child.hp_scale = hp_scale
        child.speed_scale = speed_scale
    except Exception as e:
        debug_log('inherit_scaling', e)


class Enemy(Entity):
    def __init__(self, **kwargs):
        super().__init__(parent=shootables_parent, model='cube', origin_y=-.5, collider='box', **kwargs)
        self.dead = False
        self.is_dummy = False
        self.health_bar = Entity(parent=self, y=1.2, model='cube', color=color.red, world_scale=(1.5, .1, .1))
        
        self.left_hand = Entity(parent=self, model='cube', color=self.color, scale=(0.3, 0.3, 0.6), position=(-0.6, 1.2, 0.3))
        self.right_hand = Entity(parent=self, model='cube', color=self.color, scale=(0.3, 0.3, 0.6), position=(0.6, 1.2, 0.3))

        # ---- NEW additive state (identity defaults => original enemies behave exactly as before) ----
        self.slow_factor = 1.0
        self.slow_timer = 0.0
        self.stun_timer = 0.0
        self.stun_resist = 0.0
        self.knockback_resist = 0.0
        self.damage_taken_mult = 1.0
        self.dmg_scale = 1.0
        self.hp_scale = 1.0
        self.speed_scale = 1.0
        self.is_elite = False
        self.is_boss = False
        self.is_champion = False
        self.suicide = False
        self.display_name = type(self).__name__
        self.status_fx_timer = 0.0

    def update(self):
        if not self or player.dead or self.dead or game_is_over:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return

        dist = distance_xz(player.position, self.position)
        if dist > 80:
            return

        # NEW: slow / stun status effects (returns True while stunned)
        try:
            if self.tick_status_effects():
                return
        except Exception as e:
            debug_log('Enemy.tick_status_effects', e)

        self.look_at_2d(player.position, 'y')
        try:
            self.custom_behavior(dist)
        except Exception as e:
            debug_log(f'{type(self).__name__}.custom_behavior', e)

    def custom_behavior(self, dist):
        if dist > 2.0:
            if not self.is_dummy:
                self.position += self.forward * time.dt * self.speed
        else:
            self.attack_timer += time.dt
            if self.attack_timer >= self.attack_cooldown:
                self.attack_timer = 0.0
                damage_player(self.melee_damage)

                # Synchronized swing-down attack & swing-up cooldown recovery
                if (hasattr(self, 'left_hand') and self.left_hand and not (hasattr(self.left_hand, 'isEmpty') and self.left_hand.isEmpty()) and
                    hasattr(self, 'right_hand') and self.right_hand and not (hasattr(self.right_hand, 'isEmpty') and self.right_hand.isEmpty())):
                    try:
                        self.left_hand.rotation_x = 90
                        self.right_hand.rotation_x = 90
                        self.left_hand.animate('rotation_x', 0, duration=self.attack_cooldown, curve=curve.linear)
                        self.right_hand.animate('rotation_x', 0, duration=self.attack_cooldown, curve=curve.linear)
                    except Exception:
                        pass

    # ---- NEW hooks / helpers -------------------------------------------------
    def tick_status_effects(self):
        if not hasattr(self, 'base_speed'):
            self.base_speed = getattr(self, 'speed', 0.0)
        dt = time.dt
        if self.slow_timer > 0:
            self.slow_timer -= dt
            if self.slow_timer <= 0:
                self.slow_timer = 0.0
                self.slow_factor = 1.0
            else:
                self.status_fx_timer -= dt
                if self.status_fx_timer <= 0:
                    self.status_fx_timer = 0.4
                    spawn_burst((self.x, 1.4, self.z), rgba01(0.6, 0.9, 1.0, 1.0), count=1, speed=1.2, size=0.12, lifetime=0.5, gravity=-1.0, up_bias=0.3)
        self.speed = self.base_speed * self.slow_factor
        if self.stun_timer > 0:
            self.stun_timer -= dt
            return True
        return False

    def apply_slow(self, factor, duration):
        self.slow_factor = min(self.slow_factor, factor)
        self.slow_timer = max(self.slow_timer, duration)

    def apply_stun(self, duration):
        self.stun_timer = max(self.stun_timer, duration * (1.0 - self.stun_resist))

    def scaled(self, amount):
        return amount * self.dmg_scale

    def place_health_bar(self, height_above=0.6):
        try:
            if self.scale_y > 0.01:
                self.health_bar.y = (self.scale_y + height_above) / self.scale_y
        except Exception as e:
            debug_log('Enemy.place_health_bar', e)

    def filter_incoming_damage(self, old_hp, new_value):
        if self.damage_taken_mult != 1.0:
            return old_hp - ((old_hp - new_value) * self.damage_taken_mult)
        return new_value

    def on_damaged(self, delta):
        pass

    def on_death(self, death_pos):
        pass

    def handle_death_extras(self, drop_pos):
        try:
            register_kill(self, drop_pos)
        except Exception as e:
            debug_log('register_kill', e)
        try:
            self.on_death(drop_pos)
        except Exception as e:
            debug_log(f'{type(self).__name__}.on_death', e)

    @property
    def hp(self):
        return self._hp

    @hp.setter
    def hp(self, value):
        if not self or self.dead:
            return
        if hasattr(self, 'isEmpty') and self.isEmpty():
            return

        # NEW: damage filter hook (shields, invulnerability windows, ...)
        old_hp = getattr(self, '_hp', value)
        try:
            if value < old_hp:
                value = self.filter_incoming_damage(old_hp, value)
        except Exception as e:
            debug_log('Enemy.filter_incoming_damage', e)

        self._hp = value
        if value <= 0:
            self.dead = True
            
            try:
                self.collider = None
                self.enabled = False
                self.visible = False
            except Exception:
                pass

            global score
            score += getattr(self, 'score_value', 100)
            score_text.text = f"Score: {score}  |  Wave: {wave}"
            play_defeat_sound()

            drop_pos = Vec3(self.x, 0.8, self.z)
            drop_bonus = player_stats["drop_bonus"]
            if random.random() < (getattr(self, 'health_drop_chance', 0.35) + drop_bonus):
                Pickup(pickup_type='health', position=drop_pos)
            elif random.random() < (getattr(self, 'weapon_drop_chance', 0.15) + drop_bonus * 0.5):
                rand_w_idx = random.randint(1, len(WEAPONS) - 1)
                Pickup(pickup_type='weapon', weapon_idx=rand_w_idx, position=drop_pos)
            elif random.random() < (0.10 + drop_bonus):
                Pickup(pickup_type=random.choice(['grenade', 'grenade', 'overdrive']), position=drop_pos)

            self.handle_death_extras(drop_pos)

            safe_destroy(self)
            return

        if hasattr(self, 'health_bar') and self.health_bar and hasattr(self.health_bar, 'isEmpty') and not self.health_bar.isEmpty():
            try:
                self.health_bar.world_scale_x = max(0.001, (self.hp / self.max_hp) * 1.5)
            except Exception:
                pass
        play_hit_sound()
        try:
            self.on_damaged(old_hp - value)
        except Exception as e:
            debug_log(f'{type(self).__name__}.on_damaged', e)

class StandardZombie(Enemy):
    def __init__(self, **kwargs):
        super().__init__(scale_y=2.5, color=color.hsv(0, 0.8, 0.8), **kwargs)
        self.max_hp = 70
        self._hp = random.randint(40, self.max_hp)
        self.speed = 3.5
        self.attack_cooldown = 1.0
        self.attack_timer = 0.0
        self.melee_damage = 10
        self.score_value = 100
        self.health_drop_chance = 0.35
        self.weapon_drop_chance = 0.12

class RunnerZombie(Enemy):
    def __init__(self, **kwargs):
        super().__init__(scale=(0.8, 1.8, 0.8), color=color.yellow, **kwargs)
        self.max_hp = 30
        self._hp = random.randint(20, self.max_hp)
        self.speed = 8.0
        self.attack_cooldown = 0.7
        self.attack_timer = 0.0
        self.melee_damage = 6
        self.score_value = 150
        self.health_drop_chance = 0.25
        self.weapon_drop_chance = 0.15

class TankZombie(Enemy):
    def __init__(self, **kwargs):
        super().__init__(scale=(2.0, 3.5, 2.0), color=color.dark_gray, **kwargs)
        self.max_hp = 160
        self._hp = random.randint(120, self.max_hp)
        self.speed = 2.0
        self.attack_cooldown = 1.8
        self.attack_timer = 0.0
        self.melee_damage = 25
        self.score_value = 300
        self.health_bar.y = 1.1 / self.scale_y
        self.health_drop_chance = 0.60
        self.weapon_drop_chance = 0.35

class ShooterZombie(Enemy):
    def __init__(self, **kwargs):
        super().__init__(scale=(1.0, 2.2, 1.0), color=color.cyan, **kwargs)
        self.max_hp = 60
        self._hp = random.randint(40, self.max_hp)
        self.speed = 2.5
        self.attack_cooldown = 2.0
        self.attack_timer = 0.0
        self.score_value = 250
        self.health_bar.y = 1.1 / self.scale_y
        self.health_drop_chance = 0.40
        self.weapon_drop_chance = 0.20
        
        self.gun = Entity(parent=self.right_hand, model='cube', color=color.dark_gray, scale=(0.4, 0.4, 1.2), position=(0, 0, 0.4))
        
        # Max ammo is randomized between 1-2 shots to reload almost after every shot
        self.max_ammo = random.randint(1, 2)
        self.ammo = self.max_ammo
        self.is_reloading = False

    def custom_behavior(self, dist):
        if not self or getattr(self, 'dead', False) or (hasattr(self, 'isEmpty') and self.isEmpty()):
            return

        if not self.is_dummy:
            if dist > 15.0:
                self.position += self.forward * time.dt * self.speed
            elif dist < 10.0:
                self.position -= self.forward * time.dt * self.speed
            
        if self.is_reloading:
            return

        self.attack_timer += time.dt
        if self.attack_timer >= self.attack_cooldown:
            self.attack_timer = 0.0
            fire_pos = self.position + Vec3(0, 1.2, 0)
            target_pos = player.position + Vec3(0, 1, 0)
            direction = (target_pos - fire_pos)
            EnemyBullet(position=fire_pos, direction=direction)
            play_enemy_shoot_sound()
            
            self.ammo -= 1
            if self.ammo <= 0:
                self.reload_gun()

    def reload_gun(self):
        if not self or self.is_reloading or getattr(self, 'dead', False) or (hasattr(self, 'isEmpty') and self.isEmpty()):
            return
        self.is_reloading = True
        play_reload_sound()
        
        def finish_enemy_reload():
            if not self or getattr(self, 'dead', False) or (hasattr(self, 'isEmpty') and self.isEmpty()):
                return
            self.max_ammo = random.randint(1, 2)
            self.ammo = self.max_ammo
            self.is_reloading = False

        invoke(finish_enemy_reload, delay=1.8)


# ============================================================================
# NEW ENEMIES (all inherit Enemy; each has its own movement style + ability)
# ============================================================================

class LeaperZombie(Enemy):
    """Pouncer: circles the player, telegraphs a red landing circle, then leaps in a ballistic arc."""
    def __init__(self, **kwargs):
        super().__init__(scale=(0.9, 1.9, 0.9), color=color.hsv(280, 0.65, 0.85), **kwargs)
        self.max_hp = 55
        self._hp = random.randint(40, self.max_hp)
        self.speed = 5.5
        self.attack_cooldown = 0.9
        self.attack_timer = 0.0
        self.melee_damage = 9
        self.score_value = 220
        self.health_drop_chance = 0.30
        self.weapon_drop_chance = 0.16
        self.display_name = "Pouncer"
        self.place_health_bar(0.5)

        self.leap_state = 'stalk'      # stalk -> windup -> airborne -> recover
        self.leap_timer = random.uniform(1.2, 2.6)
        self.leap_progress = 0.0
        self.leap_duration = 0.8
        self.leap_height = 6.5
        self.leap_damage = 18
        self.leap_start_x = 0.0
        self.leap_start_z = 0.0
        self.leap_target_x = 0.0
        self.leap_target_z = 0.0
        self.recover_timer = 0.0
        self.strafe_dir = random.choice([-1, 1])
        self.base_scale_y = 1.9

    def custom_behavior(self, dist):
        if self.is_dummy:
            return
        dt = time.dt

        if self.leap_state == 'stalk':
            if dist > 11.0:
                self.position += self.forward * dt * self.speed
            elif dist < 7.0:
                self.position -= self.forward * dt * self.speed * 0.6
            else:
                self.position += self.right * self.strafe_dir * dt * self.speed * 0.8
            if dist < 3.0:
                self.attack_timer += dt
                if self.attack_timer >= self.attack_cooldown:
                    self.attack_timer = 0.0
                    damage_player(self.melee_damage)
            self.leap_timer -= dt
            if self.leap_timer <= 0 and dist < 22.0:
                self.begin_windup()

        elif self.leap_state == 'windup':
            self.leap_timer -= dt
            if self.leap_timer <= 0:
                self.launch()

        elif self.leap_state == 'airborne':
            self.leap_progress += (dt * self.slow_factor) / self.leap_duration
            p = min(1.0, self.leap_progress)
            self.x = lerp(self.leap_start_x, self.leap_target_x, p)
            self.z = lerp(self.leap_start_z, self.leap_target_z, p)
            self.y = 4.0 * self.leap_height * p * (1.0 - p)
            if p >= 1.0:
                self.land()

        elif self.leap_state == 'recover':
            self.recover_timer -= dt
            if self.recover_timer <= 0:
                self.leap_state = 'stalk'
                self.leap_timer = random.uniform(2.0, 3.5)
                self.strafe_dir = random.choice([-1, 1])

    def begin_windup(self):
        self.leap_state = 'windup'
        self.leap_timer = 0.6
        self.leap_target_x = player.x
        self.leap_target_z = player.z
        self.scale_y = self.base_scale_y * 0.7
        spawn_ring((self.leap_target_x, 0, self.leap_target_z), rgba01(1.0, 0.2, 0.2, 0.35), max_radius=2.8, life=0.6, start_radius=0.4, mode='hold')

    def launch(self):
        self.leap_state = 'airborne'
        self.leap_progress = 0.0
        self.leap_start_x = self.x
        self.leap_start_z = self.z
        self.scale_y = self.base_scale_y
        play_leap_sound()

    def land(self):
        self.y = 0
        self.leap_state = 'recover'
        self.recover_timer = 0.9
        spawn_burst((self.x, 0.3, self.z), color.brown, count=8, speed=5.0, size=0.2, lifetime=0.5)
        if distance_xz(player.position, self.position) < 3.0 and player.y < 1.5:
            damage_player(self.scaled(self.leap_damage))


class BomberZombie(Enemy):
    """Bloater: sprints in, starts a flashing fuse, detonates. Shoot it from range - its blast also hurts other zombies."""
    def __init__(self, **kwargs):
        super().__init__(scale=(1.5, 1.7, 1.5), color=color.hsv(18, 0.9, 0.95), **kwargs)
        self.max_hp = 45
        self._hp = random.randint(30, self.max_hp)
        self.speed = 7.5
        self.attack_cooldown = 1.0
        self.attack_timer = 0.0
        self.melee_damage = 0
        self.score_value = 260
        self.health_drop_chance = 0.25
        self.weapon_drop_chance = 0.18
        self.display_name = "Bloater"
        self.place_health_bar(0.5)

        self.blast_radius = 6.5
        self.blast_damage = 45
        self.fuse_active = False
        self.fuse_timer = 0.0
        self.exploded = False
        self.flash_state = False
        self.belly = Entity(parent=self, model='sphere', color=color.yellow, scale=(0.9, 0.5, 0.9), position=(0, 0.45, 0.45))

    def custom_behavior(self, dist):
        if self.is_dummy or self.exploded:
            return
        dt = time.dt
        if self.fuse_active:
            self.fuse_timer -= dt
            new_state = (int(max(0.0, self.fuse_timer) * 10) % 2 == 0)
            if new_state != self.flash_state:
                self.flash_state = new_state
                self.color = color.white if new_state else color.red
            if self.fuse_timer <= 0 or dist < 1.8:
                self.explode()
            else:
                self.position += self.forward * dt * self.speed * 0.5
        else:
            self.position += self.forward * dt * self.speed
            if dist < 1.8:
                self.explode()
            elif dist < 5.5:
                self.fuse_active = True
                self.fuse_timer = 0.9
                play_bomber_beep_sound()

    def blast(self):
        pos = Vec3(self.x, 0.6, self.z)
        d = distance(pos, player.position + Vec3(0, 1, 0))
        if d <= self.blast_radius:
            falloff = max(0.4, 1.0 - d / self.blast_radius)
            damage_player(self.scaled(self.blast_damage) * falloff)
            flash_screen(1.0, 0.5, 0.1, 0.3)
        try:
            AOEExplosion(position=pos, radius=self.blast_radius, max_damage=self.scaled(self.blast_damage) * 1.4, color_theme=color.orange, lifesteal=0)
        except Exception as e:
            debug_log('Bomber.blast.AOE', e)
        spawn_burst(pos, color.orange, count=14, speed=9.0, size=0.28, lifetime=0.6)
        spawn_burst(pos, color.yellow, count=8, speed=6.0, size=0.2, lifetime=0.45)

    def explode(self):
        """Suicide detonation: no score / drops / kill credit."""
        if self.exploded or self.dead:
            return
        self.exploded = True
        self.suicide = True
        self.score_value = 0
        self.health_drop_chance = 0.0
        self.weapon_drop_chance = 0.0
        self.blast()
        self.hp = 0

    def on_death(self, death_pos):
        """Killed by the player: it still pops (don't kill it point-blank)."""
        if self.exploded:
            return
        self.exploded = True
        self.blast()


class PhantomZombie(Enemy):
    """Wraith: ghostly and half-transparent. Blinks behind the player and stabs after a short telegraph."""
    def __init__(self, **kwargs):
        super().__init__(scale=(0.9, 2.3, 0.9), color=rgba01(0.6, 0.7, 1.0, 0.35), **kwargs)
        self.max_hp = 50
        self._hp = random.randint(35, self.max_hp)
        self.speed = 4.2
        self.attack_cooldown = 1.2
        self.attack_timer = 0.0
        self.melee_damage = 14
        self.score_value = 300
        self.health_drop_chance = 0.35
        self.weapon_drop_chance = 0.18
        self.display_name = "Wraith"
        self.place_health_bar(0.5)

        self.ghost_state = 'drift'     # drift -> strike (telegraph) -> drift
        self.blink_timer = random.uniform(2.5, 4.0)
        self.strike_timer = 0.0
        self.hit_blink_cd = 0.0
        self.force_blink = False
        self.eyes = Entity(parent=self, model='cube', color=color.red, scale=(0.7, 0.08, 0.1), position=(0, 0.85, 0.5))

    def set_ghost_alpha(self, alpha, alert=False):
        try:
            if alert:
                self.color = rgba01(1.0, 0.45, 0.6, alpha)
            else:
                self.color = rgba01(0.6, 0.7, 1.0, alpha)
        except Exception as e:
            debug_log('Phantom.set_ghost_alpha', e)

    def custom_behavior(self, dist):
        if self.is_dummy:
            return
        dt = time.dt
        self.hit_blink_cd = max(0.0, self.hit_blink_cd - dt)

        if self.ghost_state == 'strike':
            self.strike_timer -= dt
            if self.strike_timer <= 0:
                if distance_xz(player.position, self.position) < 3.4:
                    damage_player(self.melee_damage + 6)
                self.set_ghost_alpha(0.35)
                self.ghost_state = 'drift'
                self.blink_timer = random.uniform(3.0, 5.0)
            return

        if dist > 2.0:
            self.position += self.forward * dt * self.speed
        else:
            self.attack_timer += dt
            if self.attack_timer >= self.attack_cooldown:
                self.attack_timer = 0.0
                damage_player(self.melee_damage)

        self.blink_timer -= dt
        if self.force_blink or (self.blink_timer <= 0 and dist > 5.0):
            self.force_blink = False
            self.blink_near_player()

    def blink_near_player(self):
        spawn_burst((self.x, 1.0, self.z), rgba01(0.6, 0.7, 1.0, 1.0), count=8, speed=4.0, size=0.18, lifetime=0.5)
        back = player.forward * -1.0
        ang = random.uniform(-0.9, 0.9)
        bx = back.x * math.cos(ang) - back.z * math.sin(ang)
        bz = back.x * math.sin(ang) + back.z * math.cos(ang)
        nx, nz = clamp_xz(player.x + bx * 4.5, player.z + bz * 4.5)
        self.x = nx
        self.z = nz
        spawn_burst((self.x, 1.0, self.z), rgba01(1.0, 0.5, 0.7, 1.0), count=8, speed=4.0, size=0.18, lifetime=0.5)
        play_blink_sound()
        self.ghost_state = 'strike'
        self.strike_timer = 0.55
        self.set_ghost_alpha(0.9, alert=True)

    def on_damaged(self, delta):
        if self.is_dummy:
            return
        if self.hit_blink_cd <= 0 and self.ghost_state == 'drift' and random.random() < 0.35:
            self.hit_blink_cd = 2.0
            self.force_blink = True


class SplitterZombie(Enemy):
    """Mitosis Blob: hops toward you and splits into two smaller, faster blobs when killed (2 generations)."""
    SIZES = {0: (1.7, 1.7, 1.7), 1: (1.1, 1.1, 1.1), 2: (0.7, 0.7, 0.7)}
    HP_BY_GEN = {0: 95, 1: 45, 2: 20}

    def __init__(self, generation=0, **kwargs):
        generation = max(0, min(2, generation))
        super().__init__(scale=SplitterZombie.SIZES[generation], color=color.hsv(105 + generation * 18, 0.8, 0.8), **kwargs)
        self.generation = generation
        self.max_hp = SplitterZombie.HP_BY_GEN[generation]
        self._hp = self.max_hp
        self.speed = 3.0 + generation * 1.6
        self.attack_cooldown = 1.0
        self.attack_timer = 0.0
        self.melee_damage = 9 - generation * 2
        self.score_value = 180 - generation * 50
        self.health_drop_chance = 0.30 if generation == 0 else 0.10
        self.weapon_drop_chance = 0.12 if generation == 0 else 0.0
        self.display_name = "Mitosis Blob"
        self.place_health_bar(0.4)
        self.hop_time = random.uniform(0, 6)

    def custom_behavior(self, dist):
        if not self.is_dummy:
            self.hop_time += time.dt * 7.0
        Enemy.custom_behavior(self, dist)
        if not self.is_dummy:
            self.y = abs(math.sin(self.hop_time)) * (0.35 + 0.15 * (2 - self.generation))

    def on_death(self, death_pos):
        if self.generation >= 2 or self.is_dummy:
            return
        for _ in range(2):
            ang = random.uniform(0, math.pi * 2)
            cx, cz = clamp_xz(death_pos[0] + math.cos(ang) * 1.4, death_pos[2] + math.sin(ang) * 1.4)
            child = SplitterZombie(generation=self.generation + 1, x=cx, z=cz)
            inherit_scaling(child, self)
        spawn_burst((death_pos[0], 0.8, death_pos[2]), color.lime, count=10, speed=5.0, size=0.22, lifetime=0.5)


class Swarmling(Enemy):
    """Tiny erratic zig-zag runner summoned by Hexers and the Warlord."""
    def __init__(self, **kwargs):
        super().__init__(scale=(0.55, 1.0, 0.55), color=color.hsv(150, 0.7, 0.5), **kwargs)
        self.max_hp = 14
        self._hp = 14
        self.speed = 8.5
        self.attack_cooldown = 0.6
        self.attack_timer = 0.0
        self.melee_damage = 4
        self.score_value = 45
        self.health_drop_chance = 0.04
        self.weapon_drop_chance = 0.0
        self.display_name = "Swarmling"
        self.place_health_bar(0.3)
        self.zig_time = random.uniform(0, 6)
        self.zig_phase = random.uniform(0, 6)

    def custom_behavior(self, dist):
        if dist > 2.0:
            if not self.is_dummy:
                self.zig_time += time.dt * 7.0
                side = math.sin(self.zig_time + self.zig_phase)
                self.position += self.forward * time.dt * self.speed + self.right * side * time.dt * self.speed * 0.6
        else:
            Enemy.custom_behavior(self, dist)


class NecromancerZombie(Enemy):
    """Hexer: kites at range, fires slow soul bolts and keeps summoning Swarmlings. Killing it weakens its minions."""
    def __init__(self, **kwargs):
        super().__init__(scale=(0.9, 2.7, 0.9), color=color.hsv(275, 0.8, 0.45), **kwargs)
        self.max_hp = 85
        self._hp = random.randint(60, self.max_hp)
        self.speed = 3.2
        self.attack_cooldown = 3.2
        self.attack_timer = random.uniform(0.0, 2.0)
        self.melee_damage = 8
        self.score_value = 450
        self.health_drop_chance = 0.45
        self.weapon_drop_chance = 0.25
        self.display_name = "Hexer"
        self.place_health_bar(0.5)

        self.minions = []
        self.summon_timer = random.uniform(2.0, 4.0)
        self.orb_angle = random.uniform(0, 360)
        self.orb = Entity(parent=self, model='sphere', color=COL_VIOLET, scale=(0.5, 0.2, 0.5), position=(1.0, 0.7, 0))

    def prune_minions(self):
        alive = []
        for m in self.minions:
            if is_entity_valid(m) and not getattr(m, 'dead', False):
                alive.append(m)
        self.minions = alive

    def summon_minions(self):
        for _ in range(random.randint(2, 3)):
            ang = random.uniform(0, math.pi * 2)
            sx, sz = clamp_xz(self.x + math.cos(ang) * 2.2, self.z + math.sin(ang) * 2.2)
            m = Swarmling(x=sx, z=sz)
            inherit_scaling(m, self)
            self.minions.append(m)
        spawn_burst((self.x, 1.2, self.z), COL_VIOLET, count=10, speed=5.0, size=0.2, lifetime=0.6)
        spawn_ring((self.x, 0, self.z), rgba01(0.7, 0.2, 1.0, 0.5), max_radius=3.5, life=0.5, start_radius=0.5)
        play_summon_sound()

    def custom_behavior(self, dist):
        dt = time.dt
        try:
            self.orb_angle += dt * 200.0
            a = math.radians(self.orb_angle)
            self.orb.x = math.cos(a) * 1.0
            self.orb.z = math.sin(a) * 1.0
        except Exception as e:
            debug_log('Necromancer.orb', e)
        if self.is_dummy:
            return

        if dist > 20.0:
            self.position += self.forward * dt * self.speed
        elif dist < 13.0:
            self.position -= self.forward * dt * self.speed

        self.summon_timer -= dt
        if self.summon_timer <= 0:
            self.summon_timer = random.uniform(6.0, 8.5)
            self.prune_minions()
            if len(self.minions) <= 3:
                self.summon_minions()

        self.attack_timer += dt
        if self.attack_timer >= self.attack_cooldown:
            self.attack_timer = 0.0
            fire_pos = self.position + Vec3(0, 1.8, 0)
            target_pos = player.position + Vec3(0, 1, 0)
            EnemyBullet(position=fire_pos, direction=(target_pos - fire_pos), damage=self.scaled(12), speed=13.0, bullet_color=COL_VIOLET, size=0.4)
            play_enemy_shoot_sound()

    def on_death(self, death_pos):
        self.prune_minions()
        for m in list(self.minions):
            try:
                if is_entity_valid(m) and not getattr(m, 'dead', False):
                    m.hp -= m.max_hp * 0.5
            except Exception as e:
                debug_log('Necromancer.on_death', e)


class AegisKnight(Enemy):
    """Armored brute behind a regenerating energy shield. Breaking the shield staggers it. Telegraphed shield-bash shoves you back."""
    def __init__(self, **kwargs):
        super().__init__(scale=(1.5, 2.8, 1.5), color=color.hsv(210, 0.25, 0.75), **kwargs)
        self.max_hp = 150
        self._hp = random.randint(120, self.max_hp)
        self.speed = 2.8
        self.attack_cooldown = 2.2
        self.attack_timer = 0.0
        self.melee_damage = 16
        self.score_value = 500
        self.health_drop_chance = 0.50
        self.weapon_drop_chance = 0.30
        self.display_name = "Aegis Knight"
        self.place_health_bar(0.6)
        self.knockback_resist = 0.5

        self.shield_max = 130.0
        self.shield_hp = self.shield_max
        self.shield_regen_delay = 0.0
        self.shield_broken_timer = 0.0
        self.shield_fx_timer = 0.0
        self.bash_state = 'idle'
        self.bash_timer = 0.0
        self.shield_fx = Entity(parent=self, model='sphere', color=rgba01(0.3, 0.8, 1.0, 0.35), scale=(1.7, 1.15, 1.7), position=(0, 0.5, 0.1))

    def filter_incoming_damage(self, old_hp, new_value):
        delta = old_hp - new_value
        if delta <= 0 or self.shield_hp <= 0:
            return new_value
        absorbed = min(self.shield_hp, delta)
        self.shield_hp -= absorbed
        self.shield_regen_delay = 3.5
        if self.shield_hp <= 0:
            self.shield_hp = 0.0
            self.shield_broken_timer = 6.0
            self.apply_stun(1.2)
            spawn_burst((self.x, 1.5, self.z), rgba01(0.4, 0.9, 1.0, 1.0), count=12, speed=7.0, size=0.2, lifetime=0.6)
        return old_hp - (delta - absorbed)

    def refresh_shield_fx(self, dt):
        self.shield_fx_timer -= dt
        if self.shield_fx_timer > 0:
            return
        self.shield_fx_timer = 0.15
        frac = self.shield_hp / self.shield_max
        want_on = frac > 0.01
        if self.shield_fx.enabled != want_on:
            self.shield_fx.enabled = want_on
        if want_on:
            self.shield_fx.color = rgba01(0.3, 0.8, 1.0, 0.12 + 0.25 * frac)

    def custom_behavior(self, dist):
        dt = time.dt
        if self.shield_broken_timer > 0:
            self.shield_broken_timer -= dt
        elif self.shield_hp < self.shield_max:
            if self.shield_regen_delay > 0:
                self.shield_regen_delay -= dt
            else:
                self.shield_hp = min(self.shield_max, self.shield_hp + 22.0 * dt)
        self.refresh_shield_fx(dt)
        if self.is_dummy:
            return

        if self.bash_state == 'windup':
            self.bash_timer -= dt
            if self.bash_timer <= 0:
                self.do_bash()
            return

        if dist > 3.2:
            self.position += self.forward * dt * self.speed
        else:
            self.attack_timer += dt
            if self.attack_timer >= self.attack_cooldown:
                self.attack_timer = 0.0
                self.bash_state = 'windup'
                self.bash_timer = 0.5
                spawn_ring((self.x, 0, self.z), rgba01(1.0, 0.5, 0.15, 0.4), max_radius=4.4, life=0.5, start_radius=1.0, mode='hold')

    def do_bash(self):
        self.bash_state = 'idle'
        if distance_xz(player.position, self.position) < 4.4:
            damage_player(self.melee_damage)
            push_player(self.position, 7.0)
            flash_screen(1.0, 0.4, 0.1, 0.2)
        spawn_ring((self.x, 0, self.z), rgba01(1.0, 0.6, 0.2, 0.5), max_radius=4.4, life=0.25, start_radius=1.0)
        play_enemy_shoot_sound()

# ============================================================================
# KILL / BOSS REWARD HANDLING
# ============================================================================
def register_kill(enemy, death_pos):
    """Called from Enemy.hp setter on death. Combo, bonus score, overdrive charge, death FX."""
    global kills, combo_count, combo_best, combo_timer, score
    if getattr(enemy, 'suicide', False):
        return
    kills += 1
    combo_count += 1
    combo_timer = 4.0
    if combo_count > combo_best:
        combo_best = combo_count

    mult = (1.0 + min(combo_count, 25) * 0.04) * player_stats["score_mult"]
    base = getattr(enemy, 'score_value', 100)
    bonus = int(base * (mult - 1.0))
    if bonus > 0:
        score += bonus
        score_text.text = f"Score: {score}  |  Wave: {wave}"

    is_major = getattr(enemy, 'is_boss', False) or getattr(enemy, 'is_champion', False)
    gain_overdrive(40.0 if is_major else 6.0)

    try:
        c = enemy.color
        burst_col = rgba01(c[0], c[1], c[2], 1.0)
    except Exception:
        burst_col = color.red
    spawn_burst((death_pos[0], 1.0, death_pos[2]), burst_col, count=18 if is_major else 9, speed=7.0, size=0.22, lifetime=0.6)
    show_hitmarker(True)

    if combo_count in (10, 20, 30, 50, 75, 100, 150, 200):
        show_notification(f"{combo_count}x COMBO!", color.orange)

    if is_major:
        boss_defeated(enemy, death_pos)


def boss_defeated(enemy, death_pos):
    global current_boss, bosses_defeated, pending_perk_choices
    try:
        major = getattr(enemy, 'is_boss', False)
        if current_boss is enemy:
            current_boss = None
        if major:
            bosses_defeated += 1
            pending_perk_choices += 2
            show_banner("BOSS DEFEATED!", color.gold, 3.5)
            heal_player(player.max_hp * 0.4)
            add_grenades(2)
            gain_overdrive(50.0)
            spawn_burst((death_pos[0], 1.5, death_pos[2]), color.gold, count=40, speed=12.0, size=0.3, lifetime=1.0)
            spawn_ring((death_pos[0], 0, death_pos[2]), rgba01(1.0, 0.85, 0.2, 0.6), max_radius=14.0, life=0.8, start_radius=1.0)
        else:
            pending_perk_choices += 1
            show_banner("CHAMPION SLAIN!", color.gold, 2.8)
            heal_player(player.max_hp * 0.2)
            add_grenades(1)
            spawn_burst((death_pos[0], 1.5, death_pos[2]), color.gold, count=24, speed=9.0, size=0.25, lifetime=0.8)

        loot = ['health', 'health', 'grenade', 'overdrive'] if major else ['health', 'grenade']
        for i, kind in enumerate(loot):
            ang = (i / len(loot)) * math.pi * 2
            Pickup(pickup_type=kind, position=Vec3(death_pos[0] + math.cos(ang) * 3.0, 0.8, death_pos[2] + math.sin(ang) * 3.0))
        if major:
            Pickup(pickup_type='weapon', weapon_idx=random.randint(1, len(WEAPONS) - 1),
                   position=Vec3(death_pos[0], 0.8, death_pos[2]))
        if pending_perk_choices > 0 and not perk_offer:
            offer_perks()
        refresh_perk_panel()
    except Exception as e:
        debug_log('boss_defeated', e)


# ============================================================================
# BOSSES (infinite rotation: Titan -> Warlord -> Storm -> Titan ... each lap is stronger)
# ============================================================================
class BossEnemy(Enemy):
    def __init__(self, boss_level=1, **kwargs):
        super().__init__(**kwargs)
        self.is_boss = True
        self.boss_level = boss_level
        self.boss_title = "BOSS"
        self.knockback_resist = 0.9
        self.stun_resist = 0.85
        self.phase = 1
        self.rage = 1.0
        try:
            self.health_bar.enabled = False
        except Exception as e:
            debug_log('BossEnemy.health_bar', e)

    def set_boss_hp(self, base_hp):
        total = base_hp * (1.0 + 0.5 * (self.boss_level - 1))
        self.max_hp = total
        self._hp = total

    def on_damaged(self, delta):
        frac = self._hp / self.max_hp
        new_phase = 1
        if frac < 0.33:
            new_phase = 3
        elif frac < 0.66:
            new_phase = 2
        if new_phase > self.phase:
            self.phase = new_phase
            self.on_phase_change(new_phase)

    def on_phase_change(self, new_phase):
        self.rage = 1.0 + 0.3 * (new_phase - 1)
        show_banner(f"{self.boss_title} - PHASE {new_phase}!", color.red, 2.5)
        play_boss_roar_sound()
        flash_screen(1.0, 0.1, 0.1, 0.3)
        spawn_burst((self.x, self.y + 2.0, self.z), color.red, count=20, speed=9.0, size=0.28, lifetime=0.8)


class TitanBoss(BossEnemy):
    """Colossus Titan: ground-slam shockwaves (JUMP or DASH them) and, from phase 2, telegraphed charges."""
    def __init__(self, boss_level=1, **kwargs):
        super().__init__(boss_level=boss_level, scale=(4.2, 9.0, 4.2), color=color.hsv(20, 0.55, 0.35), **kwargs)
        self.set_boss_hp(2200)
        self.speed = 3.0
        self.attack_cooldown = 2.0
        self.attack_timer = 0.0
        self.melee_damage = 35
        self.score_value = 5000 * boss_level
        self.health_drop_chance = 1.0
        self.weapon_drop_chance = 1.0
        self.boss_title = "COLOSSUS TITAN"
        self.display_name = "Colossus Titan"

        self.slam_timer = 5.0
        self.slam_state = 'idle'
        self.slam_windup = 1.1
        self.slam_clock = 0.0
        self.slam_radius = 18.0
        self.charge_timer = 9.0
        self.charge_state = 'idle'
        self.charge_clock = 0.0
        self.charge_dx = 0.0
        self.charge_dz = 1.0
        self.charge_hit = False

    def custom_behavior(self, dist):
        if self.is_dummy:
            return
        dt = time.dt

        if self.slam_state == 'windup':
            self.slam_clock += dt
            self.y = 1.8 * min(1.0, self.slam_clock / self.slam_windup)
            if self.slam_clock >= self.slam_windup:
                self.do_slam()
            return

        if self.charge_state == 'windup':
            self.charge_clock += dt
            if self.charge_clock >= 0.9:
                self.charge_state = 'charging'
                self.charge_clock = 0.0
                self.charge_hit = False
            return

        if self.charge_state == 'charging':
            self.charge_clock += dt
            nx, nz = clamp_xz(self.x + self.charge_dx * 26.0 * dt, self.z + self.charge_dz * 26.0 * dt)
            self.x = nx
            self.z = nz
            if not self.charge_hit and dist < 5.0:
                self.charge_hit = True
                damage_player(self.scaled(40))
                push_player(self.position, 8.0)
                flash_screen(1.0, 0.2, 0.1, 0.35)
            if self.charge_clock >= 1.1:
                self.charge_state = 'idle'
                self.charge_timer = random.uniform(7.0, 10.0) / self.rage
                self.slam_timer = max(self.slam_timer, 1.5)
            return

        if dist > 6.0:
            self.position += self.forward * dt * self.speed * self.rage
        else:
            self.attack_timer += dt
            if self.attack_timer >= self.attack_cooldown:
                self.attack_timer = 0.0
                damage_player(self.melee_damage)

        self.slam_timer -= dt * self.rage
        if self.slam_timer <= 0 and dist < 45.0:
            self.begin_slam()
        if self.phase >= 2:
            self.charge_timer -= dt
            if self.charge_timer <= 0 and dist > 8.0:
                self.begin_charge()

    def begin_slam(self):
        self.slam_state = 'windup'
        self.slam_clock = 0.0
        spawn_ring((self.x, 0, self.z), rgba01(1.0, 0.3, 0.1, 0.25), max_radius=self.slam_radius, life=self.slam_windup, start_radius=2.0, mode='hold')
        play_boss_roar_sound()

    def do_slam(self):
        self.y = 0
        self.slam_state = 'idle'
        self.slam_timer = random.uniform(6.0, 8.0)
        ShockwaveRing(position=self.position, max_radius=self.slam_radius, duration=0.95, damage=self.scaled(28))
        spawn_burst((self.x, 0.5, self.z), color.brown, count=24, speed=10.0, size=0.35, lifetime=0.8)
        play_slam_sound()
        add_fov_kick(10.0)
        if self.phase >= 3:
            invoke(self.delayed_second_ring, delay=0.55)

    def delayed_second_ring(self):
        if not is_entity_valid(self) or self.dead or game_is_over or player.dead:
            return
        ShockwaveRing(position=self.position, max_radius=self.slam_radius, duration=0.95, damage=self.scaled(24))

    def begin_charge(self):
        dx = player.x - self.x
        dz = player.z - self.z
        length = math.sqrt(dx * dx + dz * dz)
        if length < 0.01:
            return
        self.charge_dx = dx / length
        self.charge_dz = dz / length
        self.charge_state = 'windup'
        self.charge_clock = 0.0
        spawn_beam((self.x, 0.5, self.z), (self.x + self.charge_dx * 30.0, 0.5, self.z + self.charge_dz * 30.0),
                   rgba01(1.0, 0.2, 0.2, 0.8), thickness=0.6, life=0.9)
        play_boss_roar_sound()


class WarlordBoss(BossEnemy):
    """Necro Warlord: stays at range, fans out soul bolts, summons swarms and hides behind a shield while they live."""
    def __init__(self, boss_level=1, **kwargs):
        super().__init__(boss_level=boss_level, scale=(2.6, 6.0, 2.6), color=color.hsv(285, 0.7, 0.4), **kwargs)
        self.set_boss_hp(1700)
        self.speed = 3.6
        self.attack_cooldown = 2.4
        self.attack_timer = 1.0
        self.melee_damage = 25
        self.score_value = 5000 * boss_level
        self.health_drop_chance = 1.0
        self.weapon_drop_chance = 1.0
        self.boss_title = "NECRO WARLORD"
        self.display_name = "Necro Warlord"

        self.minions = []
        self.summon_timer = 3.0
        self.shield_timer = 0.0
        self.shield_fx = Entity(parent=self, model='sphere', color=rgba01(0.7, 0.3, 1.0, 0.3), scale=(1.8, 0.8, 1.8), position=(0, 0.5, 0), enabled=False)

    def prune_minions(self):
        alive = []
        for m in self.minions:
            if is_entity_valid(m) and not getattr(m, 'dead', False):
                alive.append(m)
        self.minions = alive
        return len(alive)

    def raise_shield(self):
        self.shield_timer = 7.0
        self.damage_taken_mult = 0.15
        try:
            self.shield_fx.enabled = True
        except Exception as e:
            debug_log('Warlord.raise_shield', e)

    def drop_shield(self):
        self.shield_timer = 0.0
        self.damage_taken_mult = 1.0
        try:
            self.shield_fx.enabled = False
        except Exception as e:
            debug_log('Warlord.drop_shield', e)
        spawn_burst((self.x, 3.0, self.z), COL_VIOLET, count=14, speed=8.0, size=0.25, lifetime=0.6)

    def summon_wave(self):
        count = 3 + self.phase + (self.boss_level // 2)
        for _ in range(count):
            ang = random.uniform(0, math.pi * 2)
            sx, sz = clamp_xz(self.x + math.cos(ang) * 4.0, self.z + math.sin(ang) * 4.0)
            m = Swarmling(x=sx, z=sz)
            inherit_scaling(m, self)
            self.minions.append(m)
        spawn_ring((self.x, 0, self.z), rgba01(0.7, 0.2, 1.0, 0.5), max_radius=6.0, life=0.6, start_radius=1.0)
        spawn_burst((self.x, 2.0, self.z), COL_VIOLET, count=16, speed=6.0, size=0.25, lifetime=0.7)
        play_summon_sound()

    def fire_fan(self):
        fire_pos = Vec3(self.x, 3.5, self.z)
        target = player.position + Vec3(0, 1, 0)
        dx = target.x - fire_pos.x
        dy = target.y - fire_pos.y
        dz = target.z - fire_pos.z
        n = 3 + 2 * (self.phase - 1) + (self.boss_level // 2)
        for i in range(n):
            off = (i - (n - 1) / 2.0) * 0.16
            rx = dx * math.cos(off) - dz * math.sin(off)
            rz = dx * math.sin(off) + dz * math.cos(off)
            EnemyBullet(position=fire_pos, direction=Vec3(rx, dy, rz), damage=self.scaled(11), speed=15.0, bullet_color=COL_VIOLET, size=0.45)
        play_enemy_shoot_sound()

    def custom_behavior(self, dist):
        dt = time.dt
        if self.shield_timer > 0:
            self.shield_timer -= dt
            if self.shield_timer <= 0 or self.prune_minions() <= 0:
                self.drop_shield()
        if self.is_dummy:
            return

        if dist > 24.0:
            self.position += self.forward * dt * self.speed * self.rage
        elif dist < 14.0:
            self.position -= self.forward * dt * self.speed * self.rage

        self.summon_timer -= dt * self.rage
        if self.summon_timer <= 0:
            self.summon_timer = random.uniform(9.0, 12.0)
            cap = min(16, 8 + 2 * (self.boss_level - 1) + 2 * (self.phase - 1))
            if self.prune_minions() < cap - 3:
                self.summon_wave()
                self.raise_shield()

        self.attack_timer += dt * self.rage
        if self.attack_timer >= self.attack_cooldown:
            self.attack_timer = 0.0
            self.fire_fan()

    def on_death(self, death_pos):
        self.prune_minions()
        for m in list(self.minions):
            try:
                if is_entity_valid(m) and not getattr(m, 'dead', False):
                    m.hp -= m.max_hp * 2.0
            except Exception as e:
                debug_log('Warlord.on_death', e)


class StormBoss(BossEnemy):
    """Storm Sovereign: hovers and orbits, radial bullet rings (jump / dash the gaps), blinks, and calls lightning strikes."""
    def __init__(self, boss_level=1, **kwargs):
        super().__init__(boss_level=boss_level, scale=(2.2, 5.0, 2.2), color=color.hsv(195, 0.6, 0.9), **kwargs)
        self.set_boss_hp(1500)
        self.speed = 9.0
        self.melee_damage = 20
        self.attack_cooldown = 2.6
        self.attack_timer = 0.0
        self.score_value = 5000 * boss_level
        self.health_drop_chance = 1.0
        self.weapon_drop_chance = 1.0
        self.boss_title = "STORM SOVEREIGN"
        self.display_name = "Storm Sovereign"

        self.orbit_angle = random.uniform(0, math.pi * 2)
        self.orbit_dir = random.choice([-1, 1])
        self.orbit_radius = 20.0
        self.hover_base = 2.5
        self.hover_time = 0.0
        self.ring_timer = 4.0
        self.burst_timer = 2.0
        self.blink_timer = 8.0
        self.strike_timer = 3.0

    def on_phase_change(self, new_phase):
        BossEnemy.on_phase_change(self, new_phase)
        if new_phase >= 3:
            self.orbit_radius = 16.0

    def fire_ring(self):
        n = 14 + 6 * (self.phase - 1) + 2 * (self.boss_level - 1)
        offset = random.uniform(0, math.pi * 2)
        fire_pos = Vec3(self.x, 1.2, self.z)
        for i in range(n):
            a = offset + (i / n) * math.pi * 2
            EnemyBullet(position=fire_pos, direction=Vec3(math.cos(a), 0, math.sin(a)), damage=self.scaled(13), speed=11.0,
                        bullet_color=color.cyan, size=0.38)
        spawn_ring((self.x, 0, self.z), rgba01(0.3, 0.9, 1.0, 0.5), max_radius=5.0, life=0.4, start_radius=1.0)
        play_enemy_shoot_sound()

    def fire_burst(self):
        fire_pos = Vec3(self.x, self.y + 2.5, self.z)
        target = player.position + Vec3(0, 1, 0)
        base_dir = target - fire_pos
        for i in range(3):
            jitter = Vec3(random.uniform(-0.12, 0.12), random.uniform(-0.05, 0.05), random.uniform(-0.12, 0.12))
            EnemyBullet(position=fire_pos, direction=(base_dir.normalized() + jitter), damage=self.scaled(11), speed=22.0,
                        bullet_color=color.azure, size=0.3)
        play_enemy_shoot_sound()

    def blink(self):
        spawn_burst((self.x, self.y + 2.0, self.z), color.cyan, count=14, speed=8.0, size=0.25, lifetime=0.6)
        self.orbit_angle = random.uniform(0, math.pi * 2)
        nx, nz = clamp_xz(player.x + math.cos(self.orbit_angle) * self.orbit_radius, player.z + math.sin(self.orbit_angle) * self.orbit_radius)
        self.x = nx
        self.z = nz
        spawn_burst((self.x, self.y + 2.0, self.z), color.cyan, count=14, speed=8.0, size=0.25, lifetime=0.6)
        play_blink_sound()

    def custom_behavior(self, dist):
        dt = time.dt
        self.hover_time += dt
        self.y = self.hover_base + math.sin(self.hover_time * 2.0) * 0.6
        if self.is_dummy:
            return

        self.orbit_angle += self.orbit_dir * dt * 0.45 * self.rage
        tx, tz = clamp_xz(player.x + math.cos(self.orbit_angle) * self.orbit_radius,
                          player.z + math.sin(self.orbit_angle) * self.orbit_radius)
        dx = tx - self.x
        dz = tz - self.z
        length = math.sqrt(dx * dx + dz * dz)
        if length > 0.3:
            step = min(length, self.speed * self.rage * dt)
            self.x += (dx / length) * step
            self.z += (dz / length) * step

        self.ring_timer -= dt * self.rage
        if self.ring_timer <= 0:
            self.ring_timer = random.uniform(5.0, 6.5)
            self.fire_ring()

        self.burst_timer -= dt * self.rage
        if self.burst_timer <= 0:
            self.burst_timer = 2.6
            self.fire_burst()

        if self.phase >= 2:
            self.blink_timer -= dt
            if self.blink_timer <= 0:
                self.blink_timer = random.uniform(7.0, 10.0)
                self.blink()

        if self.phase >= 3:
            self.strike_timer -= dt
            if self.strike_timer <= 0:
                self.strike_timer = 2.2
                StrikeZone(position=player.position, radius=3.4, delay=1.2, damage=self.scaled(30))
                sx, sz = clamp_xz(player.x + random.uniform(-9, 9), player.z + random.uniform(-9, 9))
                StrikeZone(position=Vec3(sx, 0, sz), radius=3.4, delay=1.4, damage=self.scaled(30))


# ============================================================================
# WAVE SCALING / SPECIAL SPAWNS / ELITES / MODIFIERS
# ============================================================================
# (class, first wave it can appear, spawn weight)
SPECIAL_ENEMY_TABLE = [
    (LeaperZombie, 4, 3.0),
    (BomberZombie, 6, 3.0),
    (SplitterZombie, 7, 2.5),
    (PhantomZombie, 8, 2.5),
    (AegisKnight, 10, 2.0),
    (NecromancerZombie, 12, 1.5),
]

WAVE_MODIFIERS = [
    {"name": "Swift Horde", "desc": "enemies move 30% faster", "speed": 1.3},
    {"name": "Iron Tide", "desc": "enemies have +50% health", "hp": 1.5},
    {"name": "Glass Cannons", "desc": "enemies hit 60% harder but have 30% less health", "dmg": 1.6, "hp": 0.7},
    {"name": "Blood Moon", "desc": "more elite & special enemies", "special_bonus": 0.25, "elite_bonus": 0.12, "sky": True},
    {"name": "Frenzy", "desc": "enemies spawn much faster", "spawn_rate": 0.55},
]


def count_active_enemies():
    n = 0
    try:
        for en in list(shootables_parent.children):
            if not en or getattr(en, 'dead', False) or getattr(en, 'is_dummy', False):
                continue
            n += 1
    except Exception as e:
        debug_log('count_active_enemies', e)
    return n


def pick_special_enemy_class():
    """Returns a NEW enemy class or None (None => the original wave roll decides, so waves 1-3 are untouched)."""
    try:
        if host_mode:
            if random.random() >= 0.35:
                return None
        else:
            if wave < 4:
                return None
            chance = min(0.65, 0.07 * (wave - 3))
            if wave_modifier:
                chance = min(0.85, chance + wave_modifier.get('special_bonus', 0.0))
            if random.random() >= chance:
                return None
        pool = [(cls, wt) for (cls, min_wave, wt) in SPECIAL_ENEMY_TABLE if host_mode or wave >= min_wave]
        if not pool:
            return None
        total = sum(wt for (_, wt) in pool)
        r = random.uniform(0, total)
        acc = 0.0
        for cls, wt in pool:
            acc += wt
            if r <= acc:
                return cls
        return pool[-1][0]
    except Exception as e:
        debug_log('pick_special_enemy_class', e)
        return None


def apply_wave_scaling(enemy):
    """Late-game scaling: identical to the original game up to wave 10 (unless a wave modifier is active)."""
    try:
        if not is_entity_valid(enemy):
            return
        late = max(0, wave - 10)
        hp_scale = 1.0 + late * 0.06
        speed_scale = 1.0 + min(0.45, late * 0.012)
        dmg_scale = 1.0 + late * 0.03
        if wave_modifier:
            hp_scale *= wave_modifier.get('hp', 1.0)
            speed_scale *= wave_modifier.get('speed', 1.0)
            dmg_scale *= wave_modifier.get('dmg', 1.0)
        if hp_scale != 1.0:
            enemy.max_hp = enemy.max_hp * hp_scale
            enemy._hp = enemy._hp * hp_scale
        if speed_scale != 1.0:
            enemy.speed = enemy.speed * speed_scale
        if dmg_scale != 1.0:
            enemy.dmg_scale = dmg_scale
            if hasattr(enemy, 'melee_damage'):
                enemy.melee_damage = enemy.melee_damage * dmg_scale
        enemy.hp_scale = hp_scale
        enemy.speed_scale = speed_scale
    except Exception as e:
        debug_log('apply_wave_scaling', e)


def make_elite(enemy, hp_mult=2.2, speed_mult=1.15, dmg_mult=1.3, score_mult=2.0):
    try:
        if not is_entity_valid(enemy) or getattr(enemy, 'is_elite', False):
            return
        enemy.is_elite = True
        enemy.max_hp = enemy.max_hp * hp_mult
        enemy._hp = enemy._hp * hp_mult
        enemy.speed = enemy.speed * speed_mult
        enemy.dmg_scale = enemy.dmg_scale * dmg_mult
        if hasattr(enemy, 'melee_damage'):
            enemy.melee_damage = enemy.melee_damage * dmg_mult
        enemy.score_value = int(getattr(enemy, 'score_value', 100) * score_mult)
        enemy.health_drop_chance = min(1.0, getattr(enemy, 'health_drop_chance', 0.35) + 0.25)
        enemy.display_name = "Elite " + getattr(enemy, 'display_name', 'Enemy')
        c = enemy.color
        enemy.color = rgba01(1.0, 0.8, 0.2, c[3])
        enemy.elite_aura = Entity(parent=enemy, model='sphere', color=rgba01(1.0, 0.85, 0.1, 0.22), scale=1.5, position=(0, 0.5, 0))
    except Exception as e:
        debug_log('make_elite', e)


def maybe_make_elite(enemy):
    if host_mode:
        return
    chance = 0.0
    if wave >= 12:
        chance += min(0.35, (wave - 11) * 0.02)
    if wave_modifier:
        chance += wave_modifier.get('elite_bonus', 0.0)
    if chance > 0 and random.random() < chance:
        make_elite(enemy)


def roll_wave_modifier():
    if wave < 8 or wave % BOSS_WAVE_INTERVAL == 0:
        return None
    if random.random() < 0.45:
        return random.choice(WAVE_MODIFIERS)
    return None


def refresh_modifier_text():
    try:
        if wave_modifier:
            modifier_text.text = f"MODIFIER: {wave_modifier['name']} - {wave_modifier['desc']}"
            modifier_text.enabled = True
        else:
            modifier_text.enabled = False
    except Exception as e:
        debug_log('refresh_modifier_text', e)


def spawn_boss():
    global current_boss
    try:
        boss_level = max(1, wave // BOSS_WAVE_INTERVAL)
        classes = [TitanBoss, WarlordBoss, StormBoss]
        cls = classes[(boss_level - 1) % len(classes)]
        angle = random.uniform(0, 360)
        bx, bz = clamp_xz(player.x + math.cos(math.radians(angle)) * 40, player.z + math.sin(math.radians(angle)) * 40)
        boss = cls(boss_level=boss_level, x=bx, z=bz)
        apply_wave_scaling(boss)
        current_boss = boss
        show_banner(f"!! {boss.boss_title} !!", color.red, 4.0)
        play_boss_roar_sound()
        flash_screen(1.0, 0.0, 0.0, 0.3)
    except Exception as e:
        debug_log('spawn_boss', e)


def spawn_champion():
    global current_boss
    try:
        pool = [cls for (cls, min_wave, wt) in SPECIAL_ENEMY_TABLE if wave >= min_wave]
        if not pool:
            pool = [TankZombie]
        cls = random.choice(pool)
        angle = random.uniform(0, 360)
        cx, cz = clamp_xz(player.x + math.cos(math.radians(angle)) * 35, player.z + math.sin(math.radians(angle)) * 35)
        champ = cls(x=cx, z=cz)
        apply_wave_scaling(champ)
        make_elite(champ, hp_mult=3.5, speed_mult=1.1, dmg_mult=1.3, score_mult=4.0)
        champ.is_champion = True
        champ.display_name = "CHAMPION " + getattr(champ, 'display_name', 'Enemy').replace("Elite ", "")
        current_boss = champ
        show_banner("A CHAMPION APPROACHES!", color.gold, 3.0)
        play_boss_roar_sound()
    except Exception as e:
        debug_log('spawn_champion', e)


def on_wave_start():
    global wave_modifier, pending_perk_choices
    try:
        wave_modifier = roll_wave_modifier()
        set_sky_blood(bool(wave_modifier and wave_modifier.get('sky', False)))
        if wave > 1:
            pending_perk_choices += 1
            add_grenades(1)
            gain_overdrive(10.0)
            heal_player(10)
        title = f"WAVE {wave}"
        if wave_modifier:
            title += f"  -  {wave_modifier['name'].upper()}"
        show_banner(title, color.yellow, 2.8)
        refresh_modifier_text()
        if wave >= BOSS_WAVE_INTERVAL and wave % BOSS_WAVE_INTERVAL == 0:
            spawn_boss()
        elif wave >= CHAMPION_WAVE_INTERVAL and wave % CHAMPION_WAVE_INTERVAL == 0:
            spawn_champion()
        if pending_perk_choices > 0 and not perk_offer:
            offer_perks()
        refresh_perk_panel()
    except Exception as e:
        debug_log('on_wave_start', e)


def spawn_enemy(gen=None):
    global enemies_left_in_wave, spawn_generation
    # NEW: generation token. A fresh external call starts a new chain; stale chains (old restarts / host toggles) die quietly.
    if gen is None:
        spawn_generation += 1
        gen = spawn_generation
    elif gen != spawn_generation:
        return

    if game_is_over:
        return

    if perk_menu_open:
        invoke(spawn_enemy, gen, delay=0.5)
        return

    if enemies_left_in_wave > 0:
        # NEW: soft cap - wait instead of flooding the map
        if count_active_enemies() >= MAX_ACTIVE_ENEMIES:
            invoke(spawn_enemy, gen, delay=1.0)
            return

        angle = random.uniform(0, 360)
        dist = random.uniform(25, 50)
        spawn_x = player.x + math.cos(math.radians(angle)) * dist
        spawn_z = player.z + math.sin(math.radians(angle)) * dist
        spawn_x, spawn_z = clamp_xz(spawn_x, spawn_z)
        
        roll = random.random()
        spawned = None

        try:
            special_cls = pick_special_enemy_class()
            if special_cls is not None:
                spawned = special_cls(x=spawn_x, z=spawn_z)
            elif host_mode:
                # Host mode treats spawning as after wave 5: all mob types can spawn
                if roll < 0.25:
                    spawned = ShooterZombie(x=spawn_x, z=spawn_z)
                elif roll < 0.50:
                    spawned = TankZombie(x=spawn_x, z=spawn_z)
                elif roll < 0.75:
                    spawned = RunnerZombie(x=spawn_x, z=spawn_z)
                else:
                    spawned = StandardZombie(x=spawn_x, z=spawn_z)
            else:
                # Gunman appears strictly AFTER wave 5 (wave > 5)
                if wave > 5 and roll < 0.20:
                    spawned = ShooterZombie(x=spawn_x, z=spawn_z)
                # Heavy Tank appears strictly AFTER wave 3 (wave > 3)
                elif wave > 3 and roll < 0.40:
                    spawned = TankZombie(x=spawn_x, z=spawn_z)
                elif roll < 0.60:
                    spawned = RunnerZombie(x=spawn_x, z=spawn_z)
                else:
                    spawned = StandardZombie(x=spawn_x, z=spawn_z)

            if spawned is not None:
                apply_wave_scaling(spawned)
                maybe_make_elite(spawned)
                spawn_burst((spawn_x, 0.5, spawn_z), color.white, count=4, speed=3.0, size=0.15, lifetime=0.4)
        except Exception as e:
            debug_log('spawn_enemy.create', e)

        play_spawn_sound()
        enemies_left_in_wave -= 1

        delay_mult = max(0.55, 1.0 - max(0, wave - 12) * 0.02)
        if wave_modifier:
            delay_mult *= wave_modifier.get('spawn_rate', 1.0)
        invoke(spawn_enemy, gen, delay=random.uniform(1.5, 3.5) * delay_mult)
    else:
        invoke(check_wave_cleared, gen, delay=4)

def check_wave_cleared(gen=None):
    global wave, enemies_left_in_wave
    if gen is None:
        gen = spawn_generation
    elif gen != spawn_generation:
        return

    if game_is_over:
        return

    active_enemies = [e for e in shootables_parent.children if e and not getattr(e, 'dead', False)]
    
    if len(active_enemies) == 0:
        wave += 1
        enemies_left_in_wave = 5 + wave * 3
        score_text.text = f"Score: {score}  |  Wave: {wave}"
        on_wave_start()
        spawn_enemy(gen)
    else:
        invoke(check_wave_cleared, gen, delay=3)

invoke(spawn_enemy, delay=2)
invoke(on_wave_start, delay=0.6)
invoke(show_notification, "SHIFT dash | G grenade | Q pulse | F overdrive | E next weapon | Z/X/C perks", color.cyan, 5.0, delay=1.0)


# ============================================================================
# PERK SYSTEM (one perk point per cleared wave, 2 per boss; choose with Z / X / C)
# ============================================================================
PERKS = [
    {"id": "vitality", "name": "Iron Lungs", "desc": "+25 max HP and heal 25", "max": 6},
    {"id": "firepower", "name": "Hollow Points", "desc": "+14% weapon damage", "max": 8},
    {"id": "tempo", "name": "Quick Hands", "desc": "-10% weapon cooldown", "max": 5},
    {"id": "sprint", "name": "Adrenal Gland", "desc": "+7% move speed", "max": 5},
    {"id": "leech", "name": "Crimson Rounds", "desc": "+1 HP lifesteal per hit", "max": 5},
    {"id": "arc", "name": "Arc Rounds", "desc": "hits chain lightning to a nearby foe", "max": 4},
    {"id": "dash", "name": "Phase Boots", "desc": "-0.3s dash cooldown", "max": 5},
    {"id": "shockdash", "name": "Shock Dash", "desc": "dash damages and staggers enemies", "max": 1},
    {"id": "grenadier", "name": "Grenadier", "desc": "+2 grenade cap, +1 grenade, +12% blast", "max": 4},
    {"id": "aegis", "name": "Aegis Overclock", "desc": "pulse: faster cooldown, wider radius", "max": 4},
    {"id": "scavenger", "name": "Scavenger", "desc": "+8% pickup drop chance", "max": 4},
    {"id": "bounty", "name": "Bounty Hunter", "desc": "+20% score from kills", "max": 4},
    {"id": "regen", "name": "Field Medic", "desc": "regen 1.2 HP/s when out of combat", "max": 4},
    {"id": "crit", "name": "Deadeye", "desc": "+6% crit chance (x2 damage)", "max": 5},
]


def recompute_player_stats():
    global player_stats
    try:
        s = default_player_stats()
        lv = perk_levels.get
        s["damage_mult"] = 1.0 + 0.14 * lv('firepower', 0)
        s["cooldown_mult"] = max(0.4, 1.0 - 0.10 * lv('tempo', 0))
        s["speed_mult"] = 1.0 + 0.07 * lv('sprint', 0)
        s["lifesteal_bonus"] = 1.0 * lv('leech', 0)
        s["arc_level"] = lv('arc', 0)
        s["regen"] = 1.2 * lv('regen', 0)
        s["score_mult"] = 1.0 + 0.2 * lv('bounty', 0)
        s["dash_cd"] = max(0.8, 2.5 - 0.3 * lv('dash', 0))
        s["shock_dash"] = lv('shockdash', 0) > 0
        s["grenade_cap"] = 3 + 2 * lv('grenadier', 0)
        s["blast_mult"] = 1.0 + 0.12 * lv('grenadier', 0)
        s["pulse_cd"] = max(5.0, 14.0 * (0.8 ** lv('aegis', 0)))
        s["pulse_radius"] = 10.0 * (1.0 + 0.2 * lv('aegis', 0))
        s["drop_bonus"] = 0.08 * lv('scavenger', 0)
        s["crit_chance"] = 0.05 + 0.06 * lv('crit', 0)
        player_stats = s
    except Exception as e:
        debug_log('recompute_player_stats', e)


def apply_perk(pid):
    global saved_player_hp, saved_player_max_hp
    try:
        perk_levels[pid] = perk_levels.get(pid, 0) + 1
        if pid == 'vitality':
            if host_mode:
                saved_player_max_hp += 25
                saved_player_hp = min(saved_player_max_hp, saved_player_hp + 25)
            else:
                player.max_hp += 25
                player.hp = min(player.max_hp, player.hp + 25)
                update_health_bar()
        recompute_player_stats()
        if pid == 'grenadier':
            add_grenades(1)
        refresh_player_speed()
        play_perk_sound()
        flash_screen(1.0, 0.9, 0.3, 0.15)
    except Exception as e:
        debug_log('apply_perk', e)


# ============================================================================
# CLICKABLE PERK MENU - pauses the game until every perk point is spent
# (click a card, or press Z / X / C)
# ============================================================================
def wrap_text(txt, width=22):
    words = txt.split(' ')
    lines = []
    current = ''
    for word in words:
        if current and (len(current) + 1 + len(word)) > width:
            lines.append(current)
            current = word
        else:
            current = (current + ' ' + word) if current else word
    if current:
        lines.append(current)
    return "\n".join(lines)


def perk_pick_0():
    choose_perk(0)

def perk_pick_1():
    choose_perk(1)

def perk_pick_2():
    choose_perk(2)

PERK_PICKERS = (perk_pick_0, perk_pick_1, perk_pick_2)
PERK_CARD_X = (-0.52, 0.0, 0.52)

perk_menu_bg = Entity(parent=camera.ui, model='quad', color=rgba01(0.0, 0.0, 0.0, 0.65), scale=(2, 2), z=-4, enabled=False)
perk_menu_title = Text(text="CHOOSE A PERK", origin=(0, 0), position=(0, 0.3), scale=2.6, color=color.gold, parent=camera.ui, z=-6, enabled=False)
perk_menu_sub = Text(text="", origin=(0, 0), position=(0, 0.23), scale=1.2, color=color.white, parent=camera.ui, z=-6, enabled=False)
perk_menu_hint = Text(text="Click a card  -  or press Z / X / C", origin=(0, 0), position=(0, -0.27), scale=1.0, color=color.light_gray, parent=camera.ui, z=-6, enabled=False)

perk_cards = []
for _i in range(3):
    _x = PERK_CARD_X[_i]
    _btn = Button(parent=camera.ui, color=color.dark_gray, highlight_color=color.gray, scale=(0.46, 0.38), position=(_x, 0.0), z=-5,
                  ignore_paused=True, on_click=PERK_PICKERS[_i], enabled=False)
    _name = Text(text="", origin=(0, 0), position=(_x, 0.12), scale=1.5, color=color.gold, parent=camera.ui, z=-6, enabled=False)
    _lvl = Text(text="", origin=(0, 0), position=(_x, 0.065), scale=1.0, color=color.cyan, parent=camera.ui, z=-6, enabled=False)
    _desc = Text(text="", origin=(0, 0), position=(_x, -0.01), scale=1.0, color=color.white, parent=camera.ui, z=-6, enabled=False)
    _key = Text(text="", origin=(0, 0), position=(_x, -0.12), scale=1.3, color=color.yellow, parent=camera.ui, z=-6, enabled=False)
    perk_cards.append((_btn, _name, _lvl, _desc, _key))


class PerkMenuInput(Entity):
    """Keeps receiving keyboard input while the game is paused (ignore_paused=True)."""
    def input(self, key):
        if not perk_menu_open:
            return
        try:
            if key == 'z':
                choose_perk(0)
            elif key == 'x':
                choose_perk(1)
            elif key == 'c':
                choose_perk(2)
            elif key == 'escape':
                application.quit()
        except Exception as e:
            debug_log('PerkMenuInput.input', e)

perk_menu_input = PerkMenuInput(ignore_paused=True)


def open_perk_menu():
    global perk_menu_open, perk_menu_prev_mouse_visible
    if perk_menu_open or game_is_over or player.dead:
        return
    perk_menu_open = True
    try:
        application.paused = True
        perk_menu_prev_mouse_visible = getattr(mouse, 'visible', False)
        mouse.visible = True
        mouse.locked = False
        held_keys['left mouse'] = 0
    except Exception as e:
        debug_log('open_perk_menu', e)


def close_perk_menu():
    global perk_menu_open
    was_open = perk_menu_open
    perk_menu_open = False
    try:
        perk_menu_bg.enabled = False
        perk_menu_title.enabled = False
        perk_menu_sub.enabled = False
        perk_menu_hint.enabled = False
        for card in perk_cards:
            for widget in card:
                widget.enabled = False
        if was_open:
            application.paused = False
            held_keys['left mouse'] = 0
            mouse.visible = perk_menu_prev_mouse_visible
            if not game_is_over and not player.dead:
                mouse.locked = True
    except Exception as e:
        debug_log('close_perk_menu', e)


def refresh_perk_panel():
    try:
        if not perk_offer or pending_perk_choices <= 0:
            close_perk_menu()
            return
        if game_is_over or player.dead:
            return
        open_perk_menu()
        perk_menu_bg.enabled = True
        perk_menu_title.enabled = True
        perk_menu_sub.enabled = True
        perk_menu_hint.enabled = True
        perk_menu_sub.text = f"Perk points available: {pending_perk_choices}"
        keys = ['Z', 'X', 'C']
        for i, card in enumerate(perk_cards):
            if i < len(perk_offer):
                perk = perk_offer[i]
                lvl = perk_levels.get(perk["id"], 0)
                card[1].text = perk["name"]
                card[2].text = f"Level {lvl + 1} / {perk['max']}"
                card[3].text = wrap_text(perk["desc"], 22)
                card[4].text = f"[{keys[i]}]"
                for widget in card:
                    widget.enabled = True
            else:
                for widget in card:
                    widget.enabled = False
    except Exception as e:
        debug_log('refresh_perk_panel', e)


def offer_perks():
    global perk_offer, pending_perk_choices
    try:
        if pending_perk_choices <= 0:
            perk_offer = []
            refresh_perk_panel()
            return
        candidates = [p for p in PERKS if perk_levels.get(p["id"], 0) < p["max"]]
        if not candidates:
            # Every perk maxed: convert leftover points into a supply drop (the game stays rewarding forever)
            pending_perk_choices = 0
            perk_offer = []
            heal_player(player.max_hp * 0.5)
            add_grenades(2)
            gain_overdrive(50.0)
            show_notification("All perks maxed - supply drop!", color.gold)
            refresh_perk_panel()
            return
        perk_offer = random.sample(candidates, min(3, len(candidates)))
        refresh_perk_panel()
        play_perk_sound()
    except Exception as e:
        debug_log('offer_perks', e)


def choose_perk(slot):
    global perk_offer, pending_perk_choices
    try:
        if game_is_over or player.dead:
            return
        if pending_perk_choices <= 0 or slot < 0 or slot >= len(perk_offer):
            return
        chosen = perk_offer[slot]
        apply_perk(chosen["id"])
        show_notification(f"Perk: {chosen['name']}!", color.gold)
        pending_perk_choices -= 1
        perk_offer = []
        if pending_perk_choices > 0:
            offer_perks()
        else:
            refresh_perk_panel()
    except Exception as e:
        debug_log('choose_perk', e)


# ============================================================================
# TACTICAL ABILITIES: Dash (SHIFT), Frag Grenade (G), Aegis Pulse (Q), Overdrive (F)
# ============================================================================
def try_dash():
    global dash_cooldown_timer, dash_time_left, dash_direction, dash_hit_ids, player_invuln_timer
    if player.dead or game_is_over or dash_cooldown_timer > 0 or dash_time_left > 0:
        return
    try:
        move = player.forward * (held_keys['w'] - held_keys['s']) + player.right * (held_keys['d'] - held_keys['a'])
        move = Vec3(move[0], 0, move[2])
        if move.length() < 0.1:
            move = Vec3(player.forward[0], 0, player.forward[2])
        dash_direction = move.normalized()
        dash_time_left = DASH_DURATION
        dash_cooldown_timer = player_stats["dash_cd"] * (0.2 if host_mode else 1.0)
        player_invuln_timer = max(player_invuln_timer, DASH_DURATION + 0.15)
        dash_hit_ids = set()
        play_dash_sound()
        add_fov_kick(14.0)
        flash_screen(0.6, 0.9, 1.0, 0.12)
        spawn_burst(player.position + Vec3(0, 0.6, 0), color.cyan, count=6, speed=4.0, size=0.14, lifetime=0.4)
    except Exception as e:
        debug_log('try_dash', e)


def update_dash():
    global dash_time_left
    if dash_time_left <= 0:
        return
    dt = time.dt
    dash_time_left -= dt
    step = DASH_SPEED * dt
    blocked = False
    try:
        hit = raycast(player.position + Vec3(0, 1, 0), dash_direction, distance=step + 0.7, ignore=(player,))
        if hit.hit and getattr(hit.entity, 'hp', None) is None:
            blocked = True
    except Exception as e:
        debug_log('update_dash.raycast', e)
    if blocked:
        dash_time_left = 0.0
    else:
        player.position += dash_direction * step
        clamp_player_to_map()
        spawn_burst(player.position + Vec3(0, 0.8, 0), rgba01(0.5, 0.9, 1.0, 1.0), count=1, speed=1.0, size=0.2, lifetime=0.3, gravity=0.0, up_bias=0.0)

    if player_stats["shock_dash"]:
        for en in list(shootables_parent.children):
            if not en or getattr(en, 'dead', False):
                continue
            if hasattr(en, 'isEmpty') and en.isEmpty():
                continue
            if id(en) in dash_hit_ids:
                continue
            try:
                if distance(en.position + Vec3(0, 1, 0), player.position + Vec3(0, 1, 0)) < 3.0:
                    dash_hit_ids.add(id(en))
                    hit_pos = Vec3(en.x, 1.2, en.z)
                    if hasattr(en, 'apply_stun'):
                        en.apply_stun(1.0)
                    en.hp -= 60 * player_stats["damage_mult"]
                    spawn_burst(hit_pos, color.cyan, count=6, speed=6.0, size=0.15, lifetime=0.4)
            except Exception as e:
                debug_log('update_dash.shock', e)


def try_grenade():
    global grenade_count
    if player.dead or game_is_over:
        return
    if grenade_count <= 0 and not host_mode:
        show_notification("No grenades!", color.red)
        return
    try:
        start = camera.world_position + camera.forward * 1.0 + Vec3(0, -0.2, 0)
        velocity = camera.forward * 26.0 + Vec3(0, 7.0, 0)
        Grenade(position=start, velocity=velocity,
                blast_radius=7.0 * player_stats["blast_mult"],
                blast_damage=130 * player_stats["damage_mult"] * player_stats["blast_mult"])
        if not host_mode:
            grenade_count -= 1
        play_grenade_throw_sound()
    except Exception as e:
        debug_log('try_grenade', e)


def try_pulse():
    global pulse_cooldown_timer
    if player.dead or game_is_over or pulse_cooldown_timer > 0:
        return
    try:
        pulse_cooldown_timer = player_stats["pulse_cd"] * (0.2 if host_mode else 1.0)
        radius = player_stats["pulse_radius"]
        for en in list(shootables_parent.children):
            if not en or getattr(en, 'dead', False):
                continue
            if hasattr(en, 'isEmpty') and en.isEmpty():
                continue
            try:
                d = distance_xz(en.position, player.position)
                if d > radius:
                    continue
                falloff = max(0.4, 1.0 - d / radius)
                if not getattr(en, 'is_dummy', False):
                    dx = en.x - player.x
                    dz = en.z - player.z
                    length = math.sqrt(dx * dx + dz * dz)
                    if length < 0.001:
                        dx, dz, length = 0.0, 1.0, 1.0
                    shove = 9.0 * falloff * (1.0 - getattr(en, 'knockback_resist', 0.0))
                    nx, nz = clamp_xz(en.x + (dx / length) * shove, en.z + (dz / length) * shove)
                    en.x = nx
                    en.z = nz
                    if hasattr(en, 'apply_stun'):
                        en.apply_stun(1.4 * falloff + 0.2)
                en.hp -= 45 * player_stats["damage_mult"] * falloff
                safe_blink(en, color.cyan, 0.15)
            except Exception as e:
                debug_log('try_pulse.enemy', e)
        for b in list(bullets_parent.children):
            if isinstance(b, EnemyBullet) and is_entity_valid(b):
                if distance(b.position, player.position) < radius * 1.2:
                    spawn_burst(b.position, color.orange, count=2, speed=3.0, size=0.12, lifetime=0.3)
                    safe_destroy(b)
        spawn_ring((player.x, 0, player.z), rgba01(0.3, 0.9, 1.0, 0.55), max_radius=radius, life=0.45, start_radius=1.0)
        spawn_burst(player.position + Vec3(0, 1, 0), color.cyan, count=14, speed=9.0, size=0.18, lifetime=0.5)
        play_pulse_sound()
        add_fov_kick(8.0)
        flash_screen(0.3, 0.9, 1.0, 0.18)
    except Exception as e:
        debug_log('try_pulse', e)


def try_overdrive():
    global overdrive_charge, overdrive_time_left
    if player.dead or game_is_over or overdrive_time_left > 0:
        return
    if overdrive_charge < 100.0 and not host_mode:
        show_notification(f"Overdrive charging: {int(overdrive_charge)}%", color.cyan)
        return
    overdrive_charge = 0.0
    overdrive_time_left = 8.0
    refresh_player_speed()
    play_overdrive_sound()
    flash_screen(0.2, 0.9, 1.0, 0.3)
    show_banner("OVERDRIVE!", color.cyan, 1.5)


def cycle_weapon(step):
    curr_i = unlocked_weapons.index(current_weapon_idx) if current_weapon_idx in unlocked_weapons else 0
    next_i = (curr_i + step) % len(unlocked_weapons)
    switch_weapon(unlocked_weapons[next_i])


def update_abilities():
    global dash_cooldown_timer, pulse_cooldown_timer, player_invuln_timer, overdrive_time_left
    global shift_was_down, combo_count, combo_timer
    dt = time.dt

    shift_now = bool(held_keys['left shift'] or held_keys['right shift'] or held_keys['shift'])
    if shift_now and not shift_was_down:
        try_dash()
    shift_was_down = shift_now

    if dash_cooldown_timer > 0:
        dash_cooldown_timer = max(0.0, dash_cooldown_timer - dt)
    if pulse_cooldown_timer > 0:
        pulse_cooldown_timer = max(0.0, pulse_cooldown_timer - dt)
    if player_invuln_timer > 0:
        player_invuln_timer = max(0.0, player_invuln_timer - dt)

    update_dash()

    if overdrive_time_left > 0:
        overdrive_time_left -= dt
        if overdrive_time_left <= 0:
            overdrive_time_left = 0.0
            refresh_player_speed()
            show_notification("Overdrive ended", color.gray)

    if combo_count > 0:
        combo_timer -= dt
        if combo_timer <= 0:
            combo_count = 0

    regen = player_stats["regen"]
    if regen > 0 and not host_mode and (game_time - last_damage_time) > 4.0 and player.hp < player.max_hp:
        player.hp = min(player.max_hp, player.hp + regen * dt)
        update_health_bar()


def update_ui_timers():
    global banner_timer, hitmarker_timer, fx_overlay_alpha, fov_kick
    dt = time.dt

    if banner_timer > 0:
        banner_timer -= dt
        if banner_timer <= 0:
            banner_text.enabled = False

    if hitmarker_timer > 0:
        hitmarker_timer -= dt
        if hitmarker_timer <= 0:
            hitmarker_a.enabled = False
            hitmarker_b.enabled = False

    base_alpha = 0.06 if overdrive_time_left > 0 else 0.0
    if fx_overlay_alpha > 0.0:
        fx_overlay_alpha = max(0.0, fx_overlay_alpha - dt * 1.2)
    shown_alpha = max(fx_overlay_alpha, base_alpha)
    if shown_alpha > 0.0 or fx_overlay.color[3] > 0.0:
        if fx_overlay_alpha > base_alpha:
            fx_overlay.color = rgba01(fx_overlay_rgb[0], fx_overlay_rgb[1], fx_overlay_rgb[2], shown_alpha)
        else:
            fx_overlay.color = rgba01(0.2, 0.9, 1.0, shown_alpha)

    target_fov = BASE_FOV + fov_kick + (6.0 if overdrive_time_left > 0 else 0.0)
    fov_kick = max(0.0, fov_kick - 70.0 * dt)
    try:
        if abs(camera.fov - target_fov) > 0.05:
            camera.fov = lerp(camera.fov, target_fov, min(1.0, dt * 12.0))
    except Exception as e:
        debug_log('update_ui_timers.fov', e)


def update_hud():
    dash_state = "READY" if dash_cooldown_timer <= 0 else f"{math.ceil(dash_cooldown_timer)}s"
    pulse_state = "READY" if pulse_cooldown_timer <= 0 else f"{math.ceil(pulse_cooldown_timer)}s"
    if overdrive_time_left > 0:
        od_state = f"ACTIVE {math.ceil(overdrive_time_left)}s"
    elif overdrive_charge >= 100.0:
        od_state = "READY"
    else:
        od_state = f"{int(overdrive_charge)}%"
    set_text_safe(ability_text, f"[SHIFT] Dash: {dash_state}   [G] Grenades: {grenade_count}   [Q] Pulse: {pulse_state}   [F] Overdrive: {od_state}")
    set_text_safe(tier_text, f"Threat: {get_tier_name()}")
    set_text_safe(kills_text, f"Kills: {kills}   Bosses: {bosses_defeated}")
    if combo_count >= 2:
        mult = (1.0 + min(combo_count, 25) * 0.04) * player_stats["score_mult"]
        set_text_safe(combo_text, f"{combo_count}x COMBO  (score x{mult:.2f})")
    else:
        set_text_safe(combo_text, "")
    if overdrive_time_left > 0:
        overdrive_bar.scale_x = max(0.0005, 0.39 * (overdrive_time_left / 8.0))
        overdrive_bar.color = color.gold
    else:
        overdrive_bar.scale_x = max(0.0005, 0.39 * min(1.0, overdrive_charge / 100.0))
        overdrive_bar.color = color.cyan


def update_boss_ui():
    global current_boss
    boss = current_boss
    if boss is not None and (not is_entity_valid(boss) or getattr(boss, 'dead', False)):
        current_boss = None
        boss = None
    if boss is None:
        if boss_bar_bg.enabled:
            boss_bar_bg.enabled = False
            boss_bar.enabled = False
            boss_name_text.enabled = False
        return
    if not boss_bar_bg.enabled:
        boss_bar_bg.enabled = True
        boss_bar.enabled = True
        boss_name_text.enabled = True
    name = getattr(boss, 'boss_title', None) or getattr(boss, 'display_name', 'BOSS')
    phase = getattr(boss, 'phase', 0)
    set_text_safe(boss_name_text, f"{name}  [Phase {phase}]" if phase else f"{name}")
    frac = max(0.0, min(1.0, boss.hp / boss.max_hp))
    boss_bar.scale_x = max(0.001, 0.6 * frac)


# ============================================================================
# SHOOTING
# ============================================================================
def arc_chain(source, src_pos, base_damage):
    """Arc Rounds perk: a bolt jumps to the nearest other enemy."""
    try:
        level = player_stats["arc_level"]
        reach = 8.0 + level * 1.5
        best = None
        best_d = 9999.0
        for en in list(shootables_parent.children):
            if en is source or not en or getattr(en, 'dead', False):
                continue
            if hasattr(en, 'isEmpty') and en.isEmpty():
                continue
            d = distance(en.position, src_pos)
            if d < reach and d < best_d:
                best = en
                best_d = d
        if best is not None:
            end_pos = Vec3(best.x, 1.2, best.z)
            spawn_beam(Vec3(src_pos[0], 1.2, src_pos[2]), end_pos, rgba01(0.5, 0.9, 1.0, 0.9), thickness=0.09, life=0.16)
            best.hp -= base_damage * (0.25 + 0.1 * level)
            safe_blink(best, color.cyan, 0.1)
    except Exception as e:
        debug_log('arc_chain', e)


def apply_player_hit(target, damage, w, hit_result):
    """Damage + feedback for one successful hit. The original damage / blink / lifesteal logic lives here unchanged."""
    crit = random.random() < player_stats["crit_chance"]
    dmg = damage * (2.0 if crit else 1.0)
    src_pos = Vec3(target.x, target.y, target.z)

    target.hp -= dmg
    safe_blink(target, color.yellow if crit else color.white, 0.1)

    killed = getattr(target, 'dead', False)
    show_hitmarker(killed)
    gain_overdrive(0.5)

    ls = w["lifesteal"] + player_stats["lifesteal_bonus"]
    if ls > 0:
        player.hp = min(player.max_hp, player.hp + ls)
        update_health_bar()

    try:
        spawn_burst(hit_result.world_point, color.yellow if crit else rgba01(1.0, 0.5, 0.3, 1.0), count=3, speed=4.0, size=0.1, lifetime=0.3)
    except Exception as e:
        debug_log('apply_player_hit.fx', e)

    slow = w.get("slow", 0.0)
    if slow > 0 and not killed and hasattr(target, 'apply_slow'):
        target.apply_slow(slow, 2.0)

    if player_stats["arc_level"] > 0:
        arc_chain(target, src_pos, dmg)


def shoot():
    if not player or player.dead or game_is_over or gun.on_cooldown:
        return

    w = WEAPONS[current_weapon_idx]
    od = overdrive_time_left > 0

    gun.on_cooldown = True
    gun.muzzle_flash.enabled = True
    
    gun.position += Vec3(0, 0.05, -0.1)
    gun.animate_position((.5, -.25, .5), duration=0.08)
    
    play_shoot_sound()

    cooldown = max(0.03, w["cooldown"] * player_stats["cooldown_mult"] * (0.6 if od else 1.0))
    damage = w["damage"] * player_stats["damage_mult"] * (1.3 if od else 1.0)
    
    invoke(setattr, gun.muzzle_flash, 'enabled', False, delay=.05)
    invoke(setattr, gun, 'on_cooldown', False, delay=cooldown)

    hit_info = raycast(camera.world_position, camera.forward, distance=150, ignore=(player, gun))
    hit_point = hit_info.world_point if hit_info.hit else (camera.world_position + camera.forward * 30)

    if w["aoe"] > 0:
        AOEExplosion(position=hit_point, radius=w["aoe"], max_damage=damage, color_theme=w["color"], lifesteal=w["lifesteal"])
        try:
            spawn_beam(gun.world_position + camera.forward * 0.6, hit_point, rgba01(1.0, 0.7, 1.0, 0.6), thickness=0.08, life=0.1)
        except Exception as e:
            debug_log('shoot.aoe_tracer', e)
    else:
        max_hits = 1 + w.get("pierce", 0)
        for pellet_i in range(w["pellets"]):
            spread_offset = Vec3(
                random.uniform(-w["spread"], w["spread"]),
                random.uniform(-w["spread"], w["spread"]),
                random.uniform(-w["spread"], w["spread"])
            )
            shoot_dir = (camera.forward + spread_offset).normalized()
            ignore_list = [player, gun]
            end_point = camera.world_position + shoot_dir * 60

            for _hit in range(max_hits):
                pellet_hit = raycast(camera.world_position, shoot_dir, distance=120, ignore=tuple(ignore_list))

                if not (pellet_hit.hit and pellet_hit.entity):
                    break
                end_point = pellet_hit.world_point
                target = pellet_hit.entity
                actual_target = target if hasattr(target, 'hp') else getattr(target, 'parent', None)

                if actual_target and hasattr(actual_target, 'hp') and not getattr(actual_target, 'dead', False):
                    try:
                        apply_player_hit(actual_target, damage, w, pellet_hit)
                    except Exception as e:
                        debug_log('shoot.apply_player_hit', e)
                    # a killed target has no collider any more, so only living ones need to be skipped on the next pierce step
                    if not getattr(actual_target, 'dead', False):
                        ignore_list.append(actual_target)
                else:
                    break

            if pellet_i == 0 and w["cooldown"] >= 0.09:
                try:
                    if w.get("pierce", 0) > 0:
                        spawn_beam(gun.world_position + camera.forward * 0.6, end_point, rgba01(0.4, 0.8, 1.0, 0.9), thickness=0.12, life=0.2)
                    else:
                        spawn_beam(gun.world_position + camera.forward * 0.6, end_point, rgba01(1.0, 0.9, 0.5, 0.7), thickness=0.04, life=0.07)
                except Exception as e:
                    debug_log('shoot.tracer', e)

def toggle_host_mode():
    global host_mode, saved_player_hp, saved_player_max_hp, enemies_left_in_wave, unlocked_weapons
    global current_boss, grenade_count, overdrive_charge
    host_mode = not host_mode
    if host_mode:
        print('spectator mode enabled')
        saved_player_hp = player.hp
        saved_player_max_hp = player.max_hp
        player.max_hp = 9999999
        player.hp = 9999999
        update_health_bar()

        unlocked_weapons = list(range(len(WEAPONS)))
        show_notification("HOST MODE: All Weapons & Dummies Unlocked!", color.cyan)

        # Spawn weapon pickups ring
        for i in range(len(WEAPONS)):
            angle = (i / len(WEAPONS)) * math.pi * 2
            pos = player.position + Vec3(math.cos(angle)*6, 0.8, math.sin(angle)*6)
            Pickup(pickup_type='weapon', weapon_idx=i, position=pos)
            
        Pickup(pickup_type='health', position=player.position + Vec3(0, 0.8, 3))
        Pickup(pickup_type='health', position=player.position + Vec3(3, 0.8, 0))

        # Spawn 1 stationary dummy of each mob type with 9,999,999 HP
        dummy_classes = [StandardZombie, RunnerZombie, TankZombie, ShooterZombie]
        for idx, MobClass in enumerate(dummy_classes):
            angle = (idx / len(dummy_classes)) * math.pi * 2 + (math.pi / 4.0)
            dummy_pos = player.position + Vec3(math.cos(angle) * 8, 0, math.sin(angle) * 8)
            dummy = MobClass(x=dummy_pos.x, z=dummy_pos.z)
            dummy.is_dummy = True
            dummy.max_hp = 9999999
            dummy._hp = 9999999
            if hasattr(dummy, 'health_bar') and dummy.health_bar and hasattr(dummy.health_bar, 'isEmpty') and not dummy.health_bar.isEmpty():
                try:
                    dummy.health_bar.world_scale_x = 1.5
                except Exception:
                    pass

        # NEW: outer ring with one stationary dummy of every NEW enemy type (the upgrade's test range)
        try:
            extra_dummy_classes = [LeaperZombie, BomberZombie, PhantomZombie, SplitterZombie, NecromancerZombie, AegisKnight]
            for idx, MobClass in enumerate(extra_dummy_classes):
                angle = (idx / len(extra_dummy_classes)) * math.pi * 2 + (math.pi / 6.0)
                dummy_pos = player.position + Vec3(math.cos(angle) * 15, 0, math.sin(angle) * 15)
                dummy = MobClass(x=dummy_pos.x, z=dummy_pos.z)
                dummy.is_dummy = True
                dummy.max_hp = 9999999
                dummy._hp = 9999999
                if hasattr(dummy, 'health_bar') and dummy.health_bar and hasattr(dummy.health_bar, 'isEmpty') and not dummy.health_bar.isEmpty():
                    try:
                        dummy.health_bar.world_scale_x = 1.5
                    except Exception:
                        pass
            grenade_count = player_stats["grenade_cap"]
            overdrive_charge = 100.0
        except Exception as e:
            debug_log('toggle_host_mode.extra_dummies', e)

    else:
        player.max_hp = saved_player_max_hp
        player.hp = saved_player_hp
        update_health_bar()
        unlocked_weapons = [0]
        switch_weapon(0)

        # Safely destroy host mode enemies, dummies, & pickups with anti-assertion wrappers
        for e in list(shootables_parent.children):
            safe_destroy(e)
        for p in list(pickups_parent.children):
            safe_destroy(p)
        current_boss = None

        enemies_left_in_wave = 5 + wave * 3
        spawn_enemy()

def restart_game():
    global score, wave, enemies_left_in_wave, game_is_over, host_mode, unlocked_weapons
    global kills, bosses_defeated, combo_count, combo_best, combo_timer, wave_modifier, pending_perk_choices
    global perk_offer, perk_levels, player_stats, current_boss, grenade_count, spawn_generation
    global dash_cooldown_timer, dash_time_left, player_invuln_timer, pulse_cooldown_timer
    global overdrive_charge, overdrive_time_left, fov_kick, banner_timer, hitmarker_timer
    global fx_overlay_alpha, last_damage_time
    
    for e in list(shootables_parent.children):
        safe_destroy(e)
    for b in list(bullets_parent.children):
        safe_destroy(b)
    for p in list(pickups_parent.children):
        safe_destroy(p)
    for fx in list(particles_parent.children):
        safe_destroy(fx)

    score = 0
    wave = 1
    enemies_left_in_wave = 6
    game_is_over = False
    host_mode = False

    # NEW: reset every upgrade system (and invalidate any pending spawn chain right away)
    spawn_generation += 1
    kills = 0
    bosses_defeated = 0
    combo_count = 0
    combo_best = 0
    combo_timer = 0.0
    wave_modifier = None
    pending_perk_choices = 0
    perk_offer = []
    perk_levels = {}
    player_stats = default_player_stats()
    current_boss = None
    grenade_count = 2
    dash_cooldown_timer = 0.0
    dash_time_left = 0.0
    player_invuln_timer = 0.0
    pulse_cooldown_timer = 0.0
    overdrive_charge = 0.0
    overdrive_time_left = 0.0
    fov_kick = 0.0
    banner_timer = 0.0
    hitmarker_timer = 0.0
    fx_overlay_alpha = 0.0
    last_damage_time = game_time
    set_sky_blood(False)
    try:
        banner_text.enabled = False
        modifier_text.enabled = False
        perk_panel_text.enabled = False
        boss_bar_bg.enabled = False
        boss_bar.enabled = False
        boss_name_text.enabled = False
        death_stats_text.enabled = False
        hitmarker_a.enabled = False
        hitmarker_b.enabled = False
        fx_overlay.color = rgba01(1.0, 1.0, 1.0, 0.0)
        camera.fov = BASE_FOV
    except Exception as e:
        debug_log('restart_game.ui_reset', e)
    close_perk_menu()
    application.paused = False

    unlocked_weapons = [0]
    switch_weapon(0)

    player.max_hp = 125
    player.hp = player.max_hp
    player.dead = False
    player.position = Vec3(0, 0, 0)

    update_health_bar()
    score_text.text = f"Score: {score}  |  Wave: {wave}"
    
    death_bg.enabled = False
    death_title.enabled = False
    death_score_text.enabled = False
    respawn_button.enabled = False

    mouse.locked = True
    invoke(spawn_enemy, delay=1)
    invoke(on_wave_start, delay=0.6)

def update():
    global game_time
    if player.dead or game_is_over:
        return
    if perk_menu_open:
        return

    try:
        game_time += time.dt
    except Exception as e:
        debug_log('update.clock', e)

    if held_keys['left mouse']:
        safe_call(shoot, 'shoot')

    safe_call(update_abilities, 'update_abilities')
    safe_call(update_ui_timers, 'update_ui_timers')
    safe_call(update_hud, 'update_hud')
    safe_call(update_boss_ui, 'update_boss_ui')
    safe_call(clamp_player_to_map, 'clamp_player_to_map')

def input(key):
    if perk_menu_open:
        return
    if key == 'escape':
        application.quit()
    if key == 'f1':
        toggle_host_mode()
    if key == 'r' and game_is_over:
        restart_game()

    if key in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0']:
        idx = 9 if key == '0' else int(key) - 1
        switch_weapon(idx)

    if key == 'scroll up':
        curr_i = unlocked_weapons.index(current_weapon_idx) if current_weapon_idx in unlocked_weapons else 0
        next_i = (curr_i + 1) % len(unlocked_weapons)
        switch_weapon(unlocked_weapons[next_i])
    elif key == 'scroll down':
        curr_i = unlocked_weapons.index(current_weapon_idx) if current_weapon_idx in unlocked_weapons else 0
        next_i = (curr_i - 1) % len(unlocked_weapons)
        switch_weapon(unlocked_weapons[next_i])

    # ---- NEW controls ----
    try:
        if game_is_over or player.dead:
            return
        if key in ('left shift', 'right shift', 'shift'):
            try_dash()
        elif key == 'g':
            try_grenade()
        elif key == 'q':
            try_pulse()
        elif key == 'f':
            try_overdrive()
        elif key == 'e':
            cycle_weapon(1)
    except Exception as err:
        debug_log('input', err)

app.run()