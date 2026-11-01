"""Shot renderers for "What if the world had no wind?".

Every shot is a function  shot_xxx(t, k) -> float32 (LH, LW, 3) image, where
  t  = seconds since the shot started
  k  = Ctx (see make_video.py): k.mode, k.dur, k.cue(i), k.cue_end(i), k.word(w),
       k.text(...)/k.stamp(...) to add hi-res HUD text, k.fi (frame index).
"""
import functools
import math
import os

import numpy as np

import pixelkit as pk
from pixelkit import C, LH, LW, YY, XX, BAYER, clamp01, decel, ease_out, mixc, ramp, smooth

HERE = os.path.dirname(os.path.abspath(__file__))
TAU = 2 * math.pi


@functools.lru_cache(None)
def GLOBE():
    return pk.Globe(os.path.join(HERE, 'assets', 'ne_110m_land.geojson'))


@functools.lru_cache(None)
def storm(w, h, seed):
    return pk.storm_sprite(w, h, seed)


def Hx(v):
    """low-res -> hi-res coordinate."""
    return v * 4


def RNG(seed):
    return np.random.RandomState(seed)


# =========================================================================== shared bits
def draw_turbine(img, x, base, h, L, theta, col, bw=1, light=None, t=0.0, taper=False, ph=0.0):
    hy = base - h
    pk.line(img, x, base, x, hy, col, bw)
    if bw >= 2:
        pk.line(img, x - 1, base, x - 1, hy + h * 0.45, col, 1)
    nb = max(1, bw)
    pk.rect(img, x - nb, hy - nb, x + nb + 2, hy + nb, col)
    for j in range(3):
        a = theta + j * TAU / 3
        ex, ey = x + L * math.sin(a), hy - L * math.cos(a)
        if taper:
            nx, ny = math.cos(a), math.sin(a)
            w0 = bw * 1.3
            pts = [(x + nx * w0, hy + ny * w0), (ex + nx * 0.6, ey + ny * 0.6), (ex - nx * 0.6, ey - ny * 0.6),
                   (x - nx * w0 * 0.3, hy - ny * w0 * 0.3)]
            pk.poly(img, pts, col)
        else:
            pk.line(img, x, hy, ex, ey, col, 1)
    pk.disk(img, x + 0.5, hy + 0.5, max(1.0, nb * 0.9), col)
    if light is not None and ((t + ph) % 1.6) < 0.55:
        pk.glow(img, x + 0.5, hy - nb, 3 + 2 * nb, light, 0.9, 1.5)
        pk.rect(img, x, hy - nb - 1, x + 1, hy - nb, '#ffe0e0')


def glitch(img, fi, strength=1.0, seed=0):
    rng = RNG(seed * 1000 + fi)
    out = img.copy()
    for _ in range(int(10 * strength)):
        y0 = rng.randint(0, LH - 4)
        h = rng.randint(2, 26)
        sh = rng.randint(-int(14 * strength) - 1, int(14 * strength) + 2)
        out[y0:y0 + h] = np.roll(out[y0:y0 + h], sh, axis=1)
    out[..., 0] = np.roll(out[..., 0], int(3 * strength), axis=1)
    out[..., 2] = np.roll(out[..., 2], -int(3 * strength), axis=1)
    noise = rng.rand(LH // 4 + 1, LW // 4 + 1) > 1 - 0.06 * strength
    nb = np.repeat(np.repeat(noise, 4, 0), 4, 1)[:LH, :LW]
    out[nb] = C('#ffffff') if rng.rand() < 0.5 else C('#ff2a4a')
    return out


def space_bg(seed=3):
    return _space_bg(seed).copy()


@functools.lru_cache(None)
def _space_bg(seed):
    img = pk.canvas('#04050d')
    pk.vgrad(img, 0, LH, ['#03040b', '#070a1c', '#0b1030', '#070a1c', '#03040b'])
    n = pk.fbm2d(LH, LW, 40, seed + 5)
    pk.dfill(img, '#141a45', np.clip((n - 0.55) * 1.6, 0, 0.6))
    pk.dfill(img, '#2a1745', np.clip((pk.fbm2d(LH, LW, 50, seed + 9) - 0.6) * 1.5, 0, 0.5))
    pk.stars(img, 170, seed)
    rng = RNG(seed + 1)
    for _ in range(9):
        x, y = rng.randint(4, LW - 4), rng.randint(4, LH - 4)
        img[y, x - 1:x + 2] = C('#9fb4ff')
        img[y - 1:y + 2, x] = C('#9fb4ff')
        img[y, x] = C('#ffffff')
    return img


class Particles:
    """Precomputed random particle attributes."""

    def __init__(self, n, seed):
        r = RNG(seed)
        self.n = n
        self.u = r.rand(n)
        self.a = r.rand(n)
        self.b = r.rand(n)
        self.c = r.rand(n)
        self.k = r.randint(0, 4, n)


def path_interp(pts, u):
    """pts: (N,2) polyline; u: array 0..1 by arc length -> (x, y)."""
    pts = np.asarray(pts, np.float32)
    seg = np.sqrt(((pts[1:] - pts[:-1]) ** 2).sum(1))
    cum = np.concatenate([[0], np.cumsum(seg)])
    s = np.clip(np.asarray(u), 0, 1) * cum[-1]
    x = np.interp(s, cum, pts[:, 0])
    y = np.interp(s, cum, pts[:, 1])
    return x, y


def sea_reflect(img, horizon, A, t, tintc='#1d3d6e', tinta=0.25, dashes=None, glitter_x=None, dark=0.82):
    rows = np.arange(horizon, LH)
    d = (rows - horizon).astype(np.float32)
    amp = A * (0.4 + d / 26.0)
    shift = np.rint(amp * np.sin(0.42 * d ** 1.12 + 2.4 * t) + A * 0.8 * np.sin(0.17 * d + 1.3 * t))
    vj = np.rint(A * (0.6 + d / 60.0) * np.sin(0.31 * d + 3.1 * t))
    src = np.clip(horizon - 1 - d - vj, 0, horizon - 1).astype(np.int32)
    xs = np.clip(np.arange(LW)[None, :] + shift[:, None], 0, LW - 1).astype(np.int32)
    out = img[src[:, None], xs] * dark
    out += (C(tintc) - out) * tinta
    img[horizon:] = out
    if dashes is not None and A > 0.01:
        dx_, dy_, dl_, dth = dashes
        for i in range(len(dx_)):
            if dth[i] > A:
                continue
            y = horizon + dy_[i]
            if y >= LH:
                continue
            x = dx_[i] + 2 * math.sin(1.7 * t + i)
            col = '#fff1c8' if glitter_x is not None and abs(x - glitter_x) < 6 + dy_[i] * 0.25 else '#a9c4e8'
            pk.line(img, x, y, x + dl_[i] * (0.4 + 0.6 * A), y, col)


def make_dashes(n, seed, depth=230):
    r = RNG(seed)
    dy = (r.rand(n) ** 1.6) * depth
    return (r.rand(n) * LW, dy.astype(np.int32), 2 + (dy / depth) * 9 * r.rand(n), r.rand(n))


# =========================================================================== 1. meadow
MEADOW = {
    'morning': dict(sky=['#1b2a5c', '#2d4f9e', '#5b86d6', '#9cc0ef', '#f6c9a0', '#ffd9a8'], sun='#fff4cf',
                    sunglow='#ffe2a8', sun_xy=(205, 226), sun_r=13, mtn='#7d8fc4', mtn_hi='#97a8d6',
                    hill='#4f9a5e', hill_hi='#6dbb73', near='#3f8a4c', near_hi='#58a95d', ground='#2f7440',
                    ground2='#357d46', grass=['#3c9a52', '#4fae5e', '#66c46f'], tower='#eef1f8',
                    cloud=('#ffffff', '#e6ecf7', '#b9c6de'), light=None, pole='#d8dce6'),
    'dusk': dict(sky=['#0d0b24', '#2b1d4f', '#6a2f63', '#c4506a', '#f2895c', '#ffc27a'], sun='#ffd28a',
                 sunglow='#ff9a5c', sun_xy=(200, 246), sun_r=17, mtn='#3d2b5e', mtn_hi='#56407a',
                 hill='#2c3550', hill_hi='#3e4868', near='#1f2a3e', near_hi='#2c3a52', ground='#18222f',
                 ground2='#1c2836', grass=['#24384a', '#2c4658', '#36566a'], tower='#9a8fbb',
                 cloud=('#ffc09a', '#e0809a', '#7a4f8a'), light='#ff3030', pole='#7d86a0'),
}
M_CLOUDS = [(10, 70, 64, 22, 1), (150, 40, 80, 26, 2), (200, 130, 50, 16, 3), (60, 160, 40, 14, 4)]
M_TURB = [(40, 44, 16, 0.3), (118, 36, 13, 1.7), (232, 50, 18, 0.9)]
_r = RNG(7)
GN = 320
_gy = 340 + (_r.rand(GN) ** 0.8) * 140
_o = np.argsort(_gy)
G_Y = _gy[_o]
G_X = (_r.rand(GN) * (LW + 6) - 3)[_o]
G_H = 3 + (G_Y - 340) / 140 * 11 + _r.rand(GN) * 3
G_C = _r.randint(0, 3, GN)
G_P = _r.rand(GN) * TAU
G_F = _r.rand(GN) < 0.13
G_FC = _r.randint(0, 3, GN)
FLOWER_C = ['#ff6b8a', '#ffd93d', '#ffffff']
PET = Particles(42, 9)


@functools.lru_cache(None)
def _meadow_static(name):
    P = MEADOW[name]
    sky = pk.canvas()
    pk.vgrad(sky, 0, 262, P['sky'])
    sx, sy = P['sun_xy']
    pk.glow(sky, sx, sy, P['sun_r'] * 3.4, P['sunglow'], 0.75, 1.5)
    pk.disk(sky, sx, sy, P['sun_r'], P['sun'])
    back = pk.keycanvas()
    pk.fill_below(back, pk.ridge_heights(252, 36, 11, 70, 3), P['mtn'], P['mtn_hi'], 1)
    hill = pk.ridge_heights(284, 20, 23, 55, 2)
    pk.fill_below(back, hill, P['hill'], P['hill_hi'], 2)
    front = pk.keycanvas()
    pk.fill_below(front, pk.ridge_heights(322, 16, 37, 45, 2), P['near'], P['near_hi'], 2)
    pk.fill_below(front, np.full(LW, 338.0) + 3 * np.sin(np.arange(LW) / 23.0), P['ground'])
    n = pk.fbm2d(LH, LW, 8, 5)
    m = (YY >= 342) & (n > 0.56)
    front[m] = C(P['ground2'])
    return sky, (back, pk.keymask(back)), (front, pk.keymask(front)), hill


def windsock(img, px, py, a0, droop, flutter, t):
    ws = [10, 9, 8, 7, 6, 5]
    x, y = px + 1, py
    for j in range(5):
        a = a0 + j * droop + flutter * 0.13 * math.sin(11 * t - j * 1.3)
        x2, y2 = x + 8 * math.sin(a), y + 8 * math.cos(a)
        nx, ny = math.cos(a), -math.sin(a)
        h0, h1 = ws[j] / 2, ws[j + 1] / 2
        quad = [(x + nx * h0, y + ny * h0), (x2 + nx * h1, y2 + ny * h1), (x2 - nx * h1, y2 - ny * h1), (x - nx * h0, y - ny * h0)]
        pk.poly(img, quad, '#ff6a2b' if j % 2 == 0 else '#f4f4f4')
        x, y = x2, y2


def wind_clock(Wf, t, dt=1 / 60):
    if t <= 0:
        return 0.0
    s = np.arange(0, t, dt)
    return float(sum(Wf(v) for v in s) * dt)


def shot_meadow(t, k):
    hook = k.mode != 'outro'
    P = MEADOW['morning' if hook else 'dusk']
    sky, back, front, hill = _meadow_static('morning' if hook else 'dusk')
    img = sky.copy()
    if hook:
        ts = k.word('stopped', 0)
        W = 1.0 if t < ts else 0.0
        tau = min(t, ts)
        env = 1.0 if t < ts else math.exp(-5 * (t - ts))
        rot = 2.4 * decel(t, ts, 2.4)
    else:
        tr = k.cue(2)
        Wf = lambda v: ramp(v, tr, tr + 1.6)
        W = Wf(t)
        tau = wind_clock(Wf, t)
        env = W
        rot = 2.4 * tau
        ts = None
    for (cx0, cy, w, h, seed) in M_CLOUDS:
        x = (cx0 + 5 * tau + 90) % (LW + 180) - 90
        pk.blit(img, pk.cloud_sprite(w, h, seed, *P['cloud']), x, cy)
    pk.layer_blit(img, back)
    for (x, hgt, L, ph) in M_TURB:
        draw_turbine(img, x, int(hill[x]) + 3, hgt, L, ph + rot, P['tower'], 1, light=P['light'], t=t, ph=ph)
    pk.layer_blit(img, front)
    # grass
    gust = 0.75 + 0.25 * math.sin(1.3 * t) * math.sin(0.7 * t + 1)
    for i in range(GN):
        d = (G_Y[i] - 340) / 140
        dx = env * ((1.5 + 3.8 * d) * gust * (0.8 + 0.2 * math.sin(2.1 * t + G_X[i] * 0.05))
                    + 0.7 * math.sin(9 * t + G_P[i]))
        x0, y0 = G_X[i], G_Y[i]
        x1, y1 = x0 + dx, y0 - G_H[i] * (1 - 0.04 * abs(dx))
        pk.line(img, x0, y0, x1, y1, P['grass'][G_C[i]])
        if G_F[i]:
            pk.rect(img, x1 - 1, y1 - 1, x1 + 1, y1 + 1, FLOWER_C[G_FC[i]] if hook else '#6a5a8a')
    # windsock
    px, py, base = 50, 244, 376
    pk.line(img, px, base, px, py - 3, P['pole'], 2)
    pk.rect(img, px - 1, py - 5, px + 2, py - 3, '#ffcf3f')
    if hook:
        aw = 1.42 + 0.06 * math.sin(7 * t) + 0.04 * math.sin(13 * t)
        if t < ts:
            a0, droop, fl = aw, -0.05, 1.0
        else:
            dt = t - ts
            a0 = 0.1 + (aw - 0.1) * math.exp(-2.6 * dt) * math.cos(4.2 * dt)
            droop, fl = -0.05 * math.exp(-3 * dt), math.exp(-4 * dt)
    else:
        aw = 1.42 + 0.06 * math.sin(7 * t) + 0.04 * math.sin(13 * t)
        a0 = 0.1 + (aw - 0.1) * W
        droop, fl = -0.05 * W, W
    windsock(img, px, py, a0, droop, fl, t)
    # petals / leaves
    pcols = ['#ffc6d6', '#ffffff', '#8fe07a', '#ffe08a']
    for i in range(PET.n):
        v = 45 + PET.a[i] * 55
        y0 = 120 + PET.b[i] * 250
        amp = 2 + PET.c[i] * 6
        ground = max(y0 + 8, 346 + PET.u[i] * 128)
        if hook:
            tt = min(t, ts)
            x = (PET.u[i] * 300 + v * tt) % 300 - 15
            y = y0 + amp * math.sin(2.1 * tt + i)
            if t >= ts:
                dt = t - ts
                x += v * 0.12 * (1 - math.exp(-6 * dt))
                y = min(y + 12 * dt + 70 * dt * dt, ground)
        else:
            if W < 0.05:
                continue
            x = (PET.u[i] * 300 + v * tau) % 300 - 15
            y = y0 + amp * math.sin(2.1 * tau + i)
        col = pcols[PET.k[i]]
        if not hook:
            col = ['#ffb08a', '#ffd9a8', '#c98aa8', '#ffe08a'][PET.k[i]]
        pk.rect(img, x, y, x + 2, y + 1, col, alpha=1.0 if hook else W)
    if not hook:
        # fireflies while the air is still
        ff = Particles(20, 77)
        for i in range(ff.n):
            if math.sin(1.7 * t + ff.c[i] * 9) < 0.2:
                continue
            fx = 20 + ff.u[i] * 230 + 6 * math.sin(0.4 * t + ff.a[i] * 6)
            fy = 300 + ff.b[i] * 150 + 4 * math.sin(0.55 * t + ff.c[i] * 6)
            pk.glow(img, fx, fy, 4, '#c8ff6a', 0.7, 1.2)
            pk.rect(img, fx, fy, fx + 1, fy + 1, '#f6ffc0')
    # freeze moment
    if hook:
        if t >= ts:
            img = pk.saturate(img, 1 - 0.35 * ramp(t, ts, ts + 0.7))
            if t < ts + 0.16:
                pk.tint(img, '#ffffff', 1 - (t - ts) / 0.16)
            k.stamp('WIND: 0 KM/H', 540, 720, 56, '#ff4d4d', t0=ts + 0.02, t1=k.cue_end(1) + 0.2)
            k.shake(14 * max(0.0, 1 - (t - ts) / 0.4))
    else:
        tr = k.cue(2)
        k.stamp('FOLLOW', 540, 600, 112, '#ffe14d', t0=tr + 0.1)
        k.text('FOR MORE "WHAT IF?"', 540, 720, 40, '#ffffff', t0=tr + 0.5)
    return img


# =========================================================================== 2. wind farm
@functools.lru_cache(None)
def _windfarm_static():
    img = pk.canvas()
    pk.vgrad(img, 0, 300, ['#1a1033', '#3d1f5c', '#7a2f6b', '#d9576b', '#ff9a5c', '#ffd08a'])
    pk.glow(img, 135, 298, 70, '#ffb070', 0.8, 1.6)
    pk.disk(img, 135, 298, 22, '#fff0c0')
    for (y, x0, x1, c) in [(236, 20, 120, '#ff9a7a'), (244, 150, 260, '#e2707a'), (256, 0, 90, '#c4507a'),
                           (262, 170, 270, '#ff8a6a'), (214, 60, 200, '#b04a7a')]:
        pk.rect(img, x0, y, x1, y + 2, c)
        pk.rect(img, x0 + 10, y + 2, x1 - 14, y + 3, c, 0.5)
    pk.fill_below(img, pk.ridge_heights(304, 6, 5, 50, 2), '#5a2f5f', '#7a3f6f', 1)
    return img


FAR_T = [(14, 30, 11), (48, 26, 10), (82, 32, 12), (112, 28, 11), (160, 30, 12), (205, 27, 10), (246, 31, 12)]
MID_T = [(30, 72, 27), (95, 62, 24), (150, 66, 25), (250, 70, 26)]


def shot_windfarm(t, k):
    img = _windfarm_static().copy()
    t_stop = k.cue(1) + 0.25 if k.nlines > 1 else 0.6
    rot = 2.6 * decel(t, t_stop, 2.6)
    for i, (x, h, L) in enumerate(FAR_T):
        draw_turbine(img, x, 304, h, L, rot * (1 + 0.07 * i) + i, '#5a2f5f', 1)
    pk.fill_below(img, pk.ridge_heights(344, 10, 8, 60, 2), '#2c1a3e', '#3e2652', 1)
    for i, (x, h, L) in enumerate(MID_T):
        draw_turbine(img, x, 344, h, L, rot * (0.9 + 0.05 * i) + 2 * i, '#36204a', 1, light='#ff2a2a', t=t, ph=i * 0.4)
    pk.fill_below(img, pk.ridge_heights(430, 26, 14, 80, 2), '#120a1e', '#1e1230', 1)
    rng = RNG(19)
    hn = pk.ridge_heights(430, 26, 14, 80, 2)
    for x in range(0, LW, 2):
        h = 2 + rng.rand() * 6
        pk.line(img, x, hn[x] + 1, x + rng.randint(-1, 2), hn[x] - h, '#120a1e')
    for x in range(6, 150, 22):
        pk.line(img, x, hn[x] + 2, x, hn[x] - 12, '#1e1230', 2)
    pk.polyline(img, [(x, hn[x] - 9) for x in range(6, 150, 22)], '#1e1230', 1)
    pk.polyline(img, [(x, hn[x] - 4) for x in range(6, 150, 22)], '#1e1230', 1)
    draw_turbine(img, 200, 480, 330, 92, rot * 0.8 + 0.4, '#170e26', 3, light='#ff2a2a', t=t, taper=True)
    if k.nlines > 2:
        c2 = k.cue(2)
        k.stamp('1 TERAWATT', 540, 640, 72, '#ff4d4d', t0=c2 + 0.05)
        k.text('OF WIND POWER: OFFLINE', 540, 730, 32, '#ffffff', t0=c2 + 0.35)
    return img


# =========================================================================== 3. city at night
@functools.lru_cache(None)
def _city_night():
    rng = RNG(21)
    img = pk.canvas()
    pk.vgrad(img, 0, 340, ['#04050f', '#0a0f2c', '#141d46', '#22305e', '#35406e'])
    pk.stars(img, 90, 4, y1=200)
    pk.disk(img, 214, 70, 11, '#f2efe0')
    pk.disk(img, 219, 66, 10, '#0a0f2c')
    hills = pk.ridge_heights(300, 26, 61, 60, 2)
    pk.fill_below(img, hills, '#161c3a', '#1f2850', 1)
    buildings = []
    win = []
    for layer, (n, hmin, hmax, col, wcol, base) in enumerate(
            [(14, 60, 140, '#1a2246', '#8a7a5a', 360), (11, 90, 230, '#10173a', '#ffd27a', 380), (7, 50, 130, '#080c22', '#ffe2a0', 400)]):
        x = rng.randint(-10, 5)
        while x < LW:
            w = rng.randint(14, 34) if layer < 2 else rng.randint(20, 40)
            h = rng.randint(hmin, hmax)
            top = base - h
            pk.rect(img, x, top, x + w, base + 40, col)
            if rng.rand() < 0.3:
                pk.rect(img, x + w // 2 - 1, top - 10, x + w // 2 + 1, top, col)
            if rng.rand() < 0.2:
                pk.rect(img, x + 2, top - 4, x + w - 2, top, col)
            ys, xs, cs = [], [], []
            for wy in range(top + 4, base, 4):
                for wx in range(x + 3, x + w - 2, 3):
                    if 0 <= wx < LW and rng.rand() < 0.55:
                        ys += [wy, wy + 1] if layer > 0 else [wy]
                        xs += [wx, wx] if layer > 0 else [wx]
                        c = C(wcol) * (0.75 + 0.25 * rng.rand())
                        if rng.rand() < 0.12:
                            c = C('#9fd3ff')
                        cs += [c, c] if layer > 0 else [c]
            buildings.append((x + w / 2, layer))
            win.append((np.array(ys, int), np.array(xs, int), np.array(cs, np.float32).reshape(-1, 3)))
            x += w + rng.randint(0, 4)
    pk.rect(img, 0, 420, LW, LH, '#06081a')
    pk.rect(img, 0, 426, LW, 444, '#10131f')
    for xx in range(0, LW, 16):
        pk.rect(img, xx, 434, xx + 7, 435, '#3a3a2a')
    return img, buildings, win, hills


def shot_city(t, k):
    base, buildings, win, hills = _city_night()
    img = base.copy()
    flick = k.mode == 'flicker'
    T0 = 0.35
    rng = RNG(5)
    offs = [T0 + 0.15 + (bx / LW) * 1.3 + rng.rand() * 0.35 for bx, _ in buildings]
    for i, (ys, xs, cs) in enumerate(win):
        if len(ys) == 0:
            continue
        if flick:
            v = math.sin(t * 2.3 + i * 1.7) + 0.6 * math.sin(t * 7.1 + i * 3.1)
            on = v > 0.2 or (k.fi + i) % 9 == 0
            level = 1.0 if on else 0.0
        else:
            tb = offs[i]
            if t < tb - 0.35:
                level = 1.0
            elif t < tb:
                level = 1.0 if math.sin(t * 70 + i) > 0 else 0.25
            else:
                level = 0.0
        if level > 0:
            img[ys, xs] = cs * level
        else:
            m = (np.arange(len(ys)) * 7 + i) % 23 == 0
            img[ys[m], xs[m]] = C('#ff8a4a') * 0.6
    # distant stopped turbines w/ red lights on the hills
    for j, x in enumerate([18, 60, 246]):
        draw_turbine(img, x, int(hills[x]) + 2, 26, 10, 0.3 + j, '#1f2850', 1, light='#ff2a2a', t=t, ph=j * 0.5)
    # street lights
    st_off = (not flick and t > T0 + 0.9) or (flick and math.sin(t * 3) < -0.3)
    for i, x in enumerate(range(12, LW, 42)):
        pk.line(img, x, 426, x, 404, '#2a2f44')
        pk.rect(img, x, 403, x + 5, 405, '#2a2f44')
        if not st_off:
            pk.glow(img, x + 4, 408, 12, '#ffcf7a', 0.55, 1.6)
            pk.rect(img, x + 3, 405, x + 6, 406, '#fff0c0')
    # cars
    for j in range(3):
        cx = (j * 97 + (40 + j * 9) * t) % (LW + 60) - 30
        y = 428 + (j % 2) * 8
        pk.rect(img, cx, y, cx + 10, y + 4, '#2b2f40')
        pk.rect(img, cx + 10, y + 2, cx + 11, y + 3, '#fff6c0')
        pk.glow(img, cx + 16, y + 2.5, 9, '#fff0b0', 0.5, 1.4)
    if flick:
        pass
    else:
        k.stamp('1 IN 10', 540, 620, 96, '#ffe14d', t0=0.3)
        k.text("OF THE WORLD'S ELECTRICITY", 540, 720, 32, '#ffffff', t0=0.6)
    return img


# =========================================================================== 4. denmark infographic
def info_bg(c1='#0d1128', c2='#151b3d'):
    img = pk.canvas(c1)
    pk.vgrad(img, 0, LH, [c1, c2, c1])
    m = ((XX.astype(int) % 6 == 0) & (YY.astype(int) % 6 == 0))
    img[m] = C(c2) * 1.4
    return img


def shot_denmark(t, k):
    img = info_bg()
    fx, fy, fw, fh = 90, 118, 90, 60
    pk.rect(img, fx + 3, fy + 3, fx + fw + 3, fy + fh + 3, '#05060f')
    pk.rect(img, fx, fy, fx + fw, fy + fh, '#c8102e')
    pk.rect(img, fx + 28, fy, fx + 39, fy + fh, '#ffffff')
    pk.rect(img, fx, fy + 25, fx + fw, fy + 36, '#ffffff')
    pk.rect(img, fx, fy, fx + fw, fy + 1, '#ff5a6e')
    # battery
    bx, by, bw, bh = 64, 198, 132, 52
    pk.rect(img, bx - 2, by - 2, bx + bw + 2, by + bh + 2, '#e8e8f0')
    pk.rect(img, bx, by, bx + bw, by + bh, '#0d1128')
    pk.rect(img, bx + bw + 2, by + 16, bx + bw + 8, by + bh - 16, '#e8e8f0')
    lvl = 10 - 5.6 * ease_out((t - 0.5) / 1.4)
    for i in range(10):
        if i + 1 <= lvl + 0.001 or (i < lvl and (k.fi // 4) % 2 == 0):
            col = '#5bd16a' if lvl > 6 else ('#ffc23f' if lvl > 4.5 else '#ff4b3e')
            pk.rect(img, bx + 4 + i * 12.6, by + 4, bx + 4 + i * 12.6 + 10, by + bh - 4, col)
    pct = int(round(lvl * 10))
    k.text('DENMARK', 540, 436, 48, '#ffffff', t0=0.0)
    k.text('%d%%' % pct if pct > 44 else '<45%', 540, 896, 48, '#ffffff' if pct > 60 else '#ff6a5a', t0=0.0, pop=False)
    k.text('WIND = OVER HALF ITS POWER', 540, 1062, 24, '#9fb4ff', t0=0.4)
    return img


# =========================================================================== 5. smog city
@functools.lru_cache(None)
def _city_day():
    rng = RNG(33)
    lay = pk.keycanvas()
    for layer, (hmin, hmax, col, base) in enumerate([(70, 150, '#8a9bbd', 350), (90, 220, '#5f6f94', 372), (40, 120, '#46557a', 396)]):
        x = rng.randint(-10, 0) + (60 if layer == 1 else 0)
        while x < LW:
            w = rng.randint(14, 36)
            h = rng.randint(hmin, hmax)
            top = base - h
            pk.rect(lay, x, top, x + w, base + 30, col)
            shade = C(col) * 0.85
            pk.rect(lay, x + w - 3, top, x + w, base + 30, shade)
            for wy in range(top + 4, base, 5):
                for wx in range(x + 3, x + w - 4, 4):
                    if rng.rand() < 0.6:
                        pk.rect(lay, wx, wy, wx + 2, wy + 2, C(col) * 1.25 if rng.rand() < 0.7 else '#c9dcf2')
            if rng.rand() < 0.25:
                pk.line(lay, x + w // 2, top, x + w // 2, top - 12, col)
            x += w + rng.randint(1, 6)
    # factory + stacks (left)
    pk.rect(lay, 0, 300, 58, 400, '#4a4f5c')
    for zx in range(0, 58, 12):
        pk.poly(lay, [(zx, 300), (zx + 12, 300), (zx + 12, 290)], '#4a4f5c')
    for (sx, top) in [(10, 196), (30, 214)]:
        pk.rect(lay, sx, top, sx + 7, 300, '#7a7470')
        pk.rect(lay, sx, top, sx + 7, top + 4, '#c8433a')
        pk.rect(lay, sx, top + 8, sx + 7, top + 11, '#e8e2dc')
        pk.rect(lay, sx + 5, top, sx + 7, 300, '#5f5955')
    # road
    pk.rect(lay, 0, 398, LW, 402, '#7a7d86')
    pk.rect(lay, 0, 402, LW, 424, '#353a45')
    for xx in range(0, LW, 18):
        pk.rect(lay, xx, 412, xx + 8, 413, '#d9d2a0')
    pk.rect(lay, 0, 424, LW, LH, '#2a2e38')
    return lay, pk.keymask(lay)


CAR_COLS = ['#d94a3a', '#e8e8e8', '#3a7ad9', '#f2c23a', '#2a2a2a']


def shot_smog(t, k):
    mode_b = k.mode == 'b'
    lvl = (0.86 + 0.14 * ramp(t, 0, k.dur)) if mode_b else (0.06 + 0.8 * ramp(t, 0.6, k.dur))
    img = pk.canvas()
    sky = [mixc(a, b, lvl) for a, b in zip(['#5d8fd1', '#8fb6e3', '#c6dbef', '#e6e2cf'],
                                            ['#6e6a58', '#8e8460', '#ab9a6a', '#bba774'])]
    pk.vgrad(img, 0, 330, sky)
    sun_c = mixc('#fff6d0', '#ff5a2a', min(1, lvl * 1.2))
    pk.glow(img, 196, 112, 40, mixc('#fff0b0', '#d0703a', lvl), 0.8 * (1 - lvl * 0.7), 1.5)
    pk.disk(img, 196, 112, 12, sun_c)
    lay = _city_day()
    pk.layer_blit(img, lay)
    tt = t + (30 if mode_b else 0)
    # stack smoke: rises straight up, then pancakes under the inversion
    for (sx, top, seed) in [(13, 196, 1), (33, 214, 2)]:
        rng = RNG(seed)
        n = int(tt / 0.14)
        for i in range(max(0, n - 220), n):
            age = tt - i * 0.14
            ph = (i * 0.618) % 1
            rise = min(age * 16, top - 150)
            y = top - 2 - rise
            spread = max(0.0, age - (top - 150) / 16)
            x = sx + 3 + (ph - 0.5) * (2 + age * 1.2) + (1 if i % 2 else -1) * spread * (2 + 5 * ph)
            r = min(2 + age * 0.5, 5.5)
            col = '#8f8a82' if i % 3 else '#a8a298'
            pk.disk(img, x, y, r, col, alpha=0.85 if age < 6 else 0.6)
    # cars and their lingering exhaust
    for j in range(5):
        v = (24 + j * 6) * (1 if j % 2 == 0 else -1)
        lane = 405 if v > 0 else 415
        x0 = j * 61
        def carx(s):
            return (x0 + v * s) % (LW + 40) - 20
        for e in range(max(0, int(tt / 0.3) - 26), int(tt / 0.3)):
            te = e * 0.3
            age = tt - te
            ex = carx(te) - (2 if v > 0 else -12)
            if ex < -10 or ex > LW + 10:
                continue
            r = min(1 + age * 0.45, 4)
            pk.disk(img, ex, lane + 1 - age * 0.6, r, '#8e8a80', alpha=max(0.2, 0.7 - age * 0.06))
        cx = carx(tt)
        pk.rect(img, cx, lane - 1, cx + 11, lane + 3, CAR_COLS[j])
        pk.rect(img, cx + 2, lane - 3, cx + 8, lane - 1, CAR_COLS[j])
        pk.rect(img, cx + 3, lane - 3, cx + 7, lane - 2, '#9fc3e6')
        pk.rect(img, cx + 1, lane + 3, cx + 3, lane + 4, '#111')
        pk.rect(img, cx + 8, lane + 3, cx + 10, lane + 4, '#111')
    # the haze
    a = np.clip(lvl * (0.2 + 0.85 * np.clip((YY - 60) / 320, 0, 1)), 0, 0.88)
    pk.dfill(img, mixc('#a8925e', '#8a6c40', lvl), a)
    pk.dfill(img, mixc('#b8a274', '#9a7c4c', lvl), np.clip(a - 0.35, 0, 0.5) * (YY > 150))
    img = pk.saturate(img, 1 - 0.25 * lvl)
    if not mode_b and k.nlines > 2:
        c2 = k.cue(2)
        p = ease_out((t - c2 - 0.1) / 2.4)
        aqi = int(38 + p * (312 - 38))
        col = '#5bd16a' if aqi <= 50 else '#ffe14d' if aqi <= 100 else '#ff9a3c' if aqi <= 150 else '#ff4d4d' if aqi <= 200 else '#c04ad9' if aqi <= 300 else '#ff2a5a'
        k.text('AIR QUALITY INDEX', 540, 470, 32, '#ffffff', t0=c2)
        k.text(str(aqi), 540, 560, 96, col, t0=c2, pop=False)
        if aqi > 300:
            k.stamp('HAZARDOUS', 540, 660, 48, '#ff2a5a', t0=c2 + 2.45)
    if mode_b:
        k.text('AQI', 540, 470, 32, '#ffffff', t0=0)
        k.text(str(int(400 + 60 * ramp(t, 0, k.dur))), 540, 560, 96, '#ff2a5a', t0=0, pop=False)
        k.stamp('PERMANENT', 540, 660, 48, '#ff2a5a', t0=k.word('permanent') + 0.2)
    return img


# =========================================================================== 6. London 1952
SEP = ['#1d160e', '#3a2c1c', '#5e4a31', '#8a7152', '#b89c74', '#e0c99e']


@functools.lru_cache(None)
def _london():
    img = pk.canvas()
    pk.vgrad(img, 0, 360, [SEP[2], SEP[3], SEP[4], SEP[4]])
    rng = RNG(52)
    # far rooftops
    x = -5
    while x < LW:
        w = rng.randint(10, 26)
        h = rng.randint(20, 60)
        pk.rect(img, x, 330 - h, x + w, 380, SEP[3])
        for c in range(rng.randint(0, 3)):
            pk.rect(img, x + 2 + c * 5, 330 - h - 6, x + 4 + c * 5, 330 - h, SEP[3])
        x += w
    pk.dfill(img, SEP[4], 0.45 * (YY > 250))
    # clock tower
    tx = 168
    pk.rect(img, tx, 128, tx + 28, 360, SEP[1])
    pk.rect(img, tx - 2, 122, tx + 30, 128, SEP[1])
    pk.poly(img, [(tx - 1, 122), (tx + 14, 72), (tx + 29, 122)], SEP[1])
    pk.line(img, tx + 14, 72, tx + 14, 58, SEP[1])
    for px_ in (tx - 2, tx + 29):
        pk.poly(img, [(px_, 122), (px_ + 1.5, 108), (px_ + 3, 122)], SEP[1])
    pk.disk(img, tx + 14, 146, 9, SEP[5])
    pk.disk(img, tx + 14, 146, 7.5, SEP[4])
    pk.line(img, tx + 14, 146, tx + 14, 140, SEP[1])
    pk.line(img, tx + 14, 146, tx + 18, 148, SEP[1])
    for wy in range(170, 350, 14):
        pk.rect(img, tx + 5, wy, tx + 8, wy + 8, SEP[2])
        pk.rect(img, tx + 20, wy, tx + 23, wy + 8, SEP[2])
    # street
    pk.rect(img, 0, 380, LW, LH, SEP[1])
    pk.rect(img, 0, 380, LW, 383, SEP[2])
    for xx in range(0, LW, 9):
        pk.rect(img, xx, 384 + (xx // 9 % 2) * 4, xx + 8, 385 + (xx // 9 % 2) * 4, SEP[0])
    # railings
    for xx in range(0, LW, 4):
        pk.line(img, xx, 372, xx, 380, SEP[0])
    pk.rect(img, 0, 371, LW, 372, SEP[0])
    return img


PERSON = pk.sprite([
    "..###..",
    ".#####.",
    "..###..",
    "..##...",
    ".####..",
    "######.",
    "######.",
    "######.",
    ".####..",
    ".####..",
    ".##.##.",
    ".##.##.",
    ".##.##.",
    "##..##.",
], {'#': SEP[0]})


def shot_london(t, k):
    img = _london().copy()
    for i, x in enumerate([46, 236]):
        pk.line(img, x, 380, x, 300, SEP[0], 2)
        pk.rect(img, x - 4, 292, x + 5, 300, SEP[0])
        pk.glow(img, x + 0.5, 296, 30, SEP[5], 0.7, 1.4)
        pk.rect(img, x - 2, 294, x + 3, 299, '#fff2c8')
    for j, (x0, v) in enumerate([(70, 6), (150, -5)]):
        x = x0 + v * t
        bob = int((t * 4 + j) % 2)
        pk.blit(img, PERSON, x, 363 - bob, flipx=v < 0)
    # fog banks (dead still, thickening)
    thick = 0.45 + 0.35 * ramp(t, 0, k.dur)
    a = thick * (0.35 + 0.65 * np.clip((YY - 150) / 260, 0, 1))
    pk.dfill(img, SEP[4], a * (0.75 + 0.25 * pk.fbm2d(LH, LW, 30, 4)))
    pk.dfill(img, SEP[5], np.clip(a - 0.4, 0, 0.4))
    # old film
    rng = RNG(k.fi)
    img *= 0.94 + 0.08 * rng.rand()
    if rng.rand() < 0.5:
        x = rng.randint(0, LW)
        pk.line(img, x, 0, x + rng.randint(-3, 4), LH, SEP[5], alpha=0.5)
    for _ in range(4):
        x, y = rng.randint(0, LW), rng.randint(0, LH)
        pk.rect(img, x, y, x + 1, y + 1, SEP[0] if rng.rand() < 0.5 else SEP[5])
    k.type('LONDON, DEC 1952', 540, 470, 40, '#f2deb0', t0=0.15)
    k.type('5 DAYS. NO WIND.', 540, 1340, 40, '#ffffff', t0=max(0.5, k.word('five') - 0.1))
    return img


# =========================================================================== 7. stuck storm
STORM_BOLTS = [0.7, 2.2, 3.6, 5.1]


@functools.lru_cache(None)
def _storm_static():
    img = pk.canvas()
    pk.vgrad(img, 0, 330, ['#2f62b0', '#5f97da', '#a9d1f2', '#e0f0fa'])
    pk.glow(img, 236, 64, 34, '#fff4c0', 0.8, 1.5)
    pk.disk(img, 236, 64, 11, '#fffbe6')
    # darkness under the storm (left)
    dark = np.clip((205 - XX) / 120, 0, 1) * np.clip((YY - 40) / 80, 0, 1)
    pk.dfill(img, '#3a4255', dark * 0.95)
    pk.dfill(img, '#2a3040', np.clip(dark - 0.5, 0, 0.5) * 1.2)
    hills = pk.ridge_heights(310, 18, 71, 60, 2)
    pk.fill_below(img, hills, '#4f9a5e', '#6dbb73', 1)
    pk.fill_below(img, np.full(LW, 340.0), '#3f8a4c')
    wet = np.clip((200 - XX) / 80, 0, 1)
    pk.dfill(img, '#2b4a3a', wet * 0.9 * (YY > 290))
    # town
    rng = RNG(12)
    for i in range(9):
        hx = 18 + i * 20 + rng.randint(-3, 4)
        hy = 336 + rng.randint(0, 22)
        wcol = ['#c9b79a', '#b8a58a', '#d9cbb0'][i % 3]
        pk.rect(img, hx, hy - 9, hx + 13, hy, wcol)
        pk.poly(img, [(hx - 2, hy - 9), (hx + 6.5, hy - 16), (hx + 15, hy - 9)], ['#8a3a3a', '#5a4a6a', '#7a4a3a'][i % 3])
        pk.rect(img, hx + 5, hy - 5, hx + 8, hy, '#4a3a2a')
        pk.rect(img, hx + 2, hy - 7, hx + 4, hy - 5, '#ffd27a')
    pk.dfill(img, '#1d2433', 0.35 * np.clip((205 - XX) / 100, 0, 1) * (YY > 290))
    return img


def shot_storm(t, k):
    img = _storm_static().copy()
    pk.blit(img, storm(220, 70, 5), -14, 52)
    rng = RNG(3)
    xs = rng.rand(170) * 205
    y0 = rng.rand(170) * 220
    for i in range(170):
        y = 128 + (y0[i] + 260 * t) % 230
        pk.line(img, xs[i] - 1, y, xs[i], y + 4, '#9fb3d0')
        if y > 350:
            pk.rect(img, xs[i] - 1, 356 + (i % 7) * 3, xs[i] + 1, 357 + (i % 7) * 3, '#c9d6ea')
    for tb in STORM_BOLTS:
        dt = t - tb
        if 0 <= dt < 0.22:
            r = RNG(int(tb * 10))
            pts = [(70 + r.randint(0, 80), 120)]
            while pts[-1][1] < 336:
                x, y = pts[-1]
                pts.append((x + r.randint(-9, 10), y + r.randint(10, 22)))
            if dt < 0.12 or 0.16 < dt < 0.2:
                pk.tint(img, '#e8eeff', 0.45 * (1 - dt / 0.22))
                pk.polyline(img, pts, '#bcd0ff', 3)
                pk.polyline(img, pts, '#ffffff', 1)
    if k.nlines > 1:
        k.text('FORECAST:', 540, 470, 32, '#ffffff', t0=k.cue(1))
        k.text('SAME. FOREVER.', 540, 540, 48, '#ffe14d', t0=k.cue(1) + 0.4)
    return img


# =========================================================================== 8. split flood / drought
@functools.lru_cache(None)
def _split_static():
    img = pk.canvas()
    pk.vgrad(img, 0, 238, ['#141924', '#252c3c', '#3c4558', '#4f5a70'])
    pk.blit(img, storm(280, 60, 8), -20, 10)
    pk.fill_below(img, pk.ridge_heights(214, 10, 4, 50, 2), '#2f4a3a', None)
    rng = RNG(9)
    for i in range(7):
        hx = 14 + i * 36 + rng.randint(-4, 5)
        pk.rect(img, hx, 196, hx + 20, 230, '#a89a86')
        pk.poly(img, [(hx - 3, 196), (hx + 10, 184), (hx + 23, 196)], '#6a3a3a')
        pk.rect(img, hx + 4, 202, hx + 8, 206, '#ffd27a')
        pk.rect(img, hx + 12, 202, hx + 16, 206, '#ffd27a')
    # dry half
    pk.vgrad(img, 242, 340, ['#ffe9a8', '#fff3cf', '#fff8e6'])
    pk.glow(img, 200, 280, 46, '#fff2b0', 0.9, 1.4)
    pk.disk(img, 200, 280, 16, '#ffffff')
    for a in range(12):
        ang = a * TAU / 12
        pk.line(img, 200 + 20 * math.cos(ang), 280 + 20 * math.sin(ang), 200 + 30 * math.cos(ang), 280 + 30 * math.sin(ang), '#fff6c8')
    pk.fill_below(img, pk.ridge_heights(330, 8, 44, 50, 2), '#d9a868', '#e8bf86', 1)
    pk.rect(img, 0, 340, LW, LH, '#c58a4e')
    n = pk.fbm2d(LH, LW, 6, 13)
    pk.dfill(img, '#d39a5c', (n > 0.55) * 0.8 * (YY > 340))
    rng = RNG(14)
    for _ in range(46):
        x, y = rng.rand() * LW, 344 + rng.rand() * 136
        pts = [(x, y)]
        for s in range(rng.randint(3, 7)):
            x += rng.randint(-8, 9)
            y += rng.randint(-3, 6)
            pts.append((x, y))
        pk.polyline(img, pts, '#7a4a24')
    # dead tree
    pk.line(img, 70, 392, 70, 330, '#5a4636', 3)
    for (x2, y2) in [(52, 312), (90, 318), (60, 345), (84, 340), (74, 306)]:
        pk.line(img, 70, 360 - (360 - y2) * 0.4, x2, y2, '#5a4636', 1)
    pk.ellipse(img, 70, 393, 10, 2, '#9a6a3a')
    pk.rect(img, 0, 238, LW, 242, '#000000')
    pk.rect(img, 0, 239, LW, 240, '#ffffff')
    return img


def shot_split(t, k):
    img = _split_static().copy()
    lvl = 232 - 22 * ease_out(t / max(1, k.dur))
    m = (YY >= lvl) & (YY < 238)
    img[m] = C('#3a5a80')
    for i in range(int(lvl), 238, 4):
        for xx in range(int((t * 20 + i * 7) % 18) - 18, LW, 18):
            pk.rect(img, xx, i, xx + 6, i + 1, '#6f93bd')
    pk.rect(img, 0, lvl, LW, lvl + 1, '#9fbfe0')
    rng = RNG(17)
    xs = rng.rand(140) * LW
    y0 = rng.rand(140) * 240
    for i in range(140):
        y = (y0[i] + 280 * t) % 238
        if y < lvl:
            pk.line(img, xs[i] - 1, y, xs[i], y + 4, '#8fa3c0')
    # heat shimmer on the dry half
    for y in range(300, 345):
        sh = int(round(1.2 * math.sin(y * 0.9 + t * 9)))
        if sh:
            img[y] = np.roll(img[y], sh, axis=0)
    k.text('RAIN, FOREVER', 540, 880, 32, '#9fd3ff', t0=0.1)
    k.text('DRY, FOREVER', 540, 1420, 32, '#ffcf6a', t0=max(0.4, k.word('dry') - 0.1))
    return img


# =========================================================================== 9. coast - rain falls back into the sea
@functools.lru_cache(None)
def _coast_static():
    img = pk.canvas()
    pk.vgrad(img, 0, 300, ['#4f6f9a', '#7f9fc4', '#b9cde0', '#dfe8ef'])
    pk.blit(img, storm(140, 46, 21), -6, 104)
    pk.rect(img, 0, 300, LW, LH, '#1d4f8a')
    pk.vgrad(img, 300, LH, ['#2f6fb0', '#1d4f8a', '#123a6a'])
    return img


def shot_coast(t, k):
    img = _coast_static().copy()
    p = ramp(t, 0.2, k.dur)
    xs = np.arange(LW)
    hgt = np.where(xs < 112, 999.0, np.maximum(238 + 14 * pk.fbm1d(xs, 77, 40), 304 - (xs - 112) * 1.4))
    land = (YY >= hgt[None, :]) & (YY < 304 + (XX - 112) * 3)
    land |= (YY >= hgt[None, :]) & (XX > 150)
    img[land] = mixc('#4f9a4f', '#c09a5c', p)
    top = land & ~np.roll(land, 2, axis=0)
    img[top] = mixc('#7fc46a', '#e0b878', p)
    beach = land & (YY > hgt[None, :] + 0) & (XX < 140) & (YY < 312)
    img[beach] = C('#e0cc96')
    deep = land & (YY > 330)
    pk.dfill(img, mixc('#3f7a3f', '#a07c4a', p), 0.6, deep)
    rng = RNG(4)
    for i in range(10):
        x = 150 + i * 12 + rng.randint(-3, 4)
        gy = hgt[min(LW - 1, x)] + 2
        pk.line(img, x, gy, x, gy - 9, '#5a4030', 1)
        if p < 0.85:
            pk.disk(img, x, gy - 11, 5 - 3 * p, mixc('#3f8a3a', '#9a8a3a', p), alpha=1 - p * 0.6)
    xs_ = rng.rand(110) * 128
    y0 = rng.rand(110) * 150
    for i in range(110):
        y = 150 + (y0[i] + 230 * t) % 150
        pk.line(img, xs_[i] - 1, y, xs_[i], y + 4, '#a9bfdd')
    for i in range(18):
        x = (i * 7.3) % 110
        if (k.fi + i) % 3 == 0:
            pk.rect(img, x, 298, x + 2, 299, '#e6f0ff')
    # blocked arrow
    for xx in range(118, 178, 6):
        pk.rect(img, xx, 176, xx + 3, 178, '#ffffff')
    pk.poly(img, [(178, 170), (190, 177), (178, 184)], '#ffffff')
    if t > 0.7:
        pk.line(img, 146, 166, 166, 188, '#ff3b3b', 3)
        pk.line(img, 166, 166, 146, 188, '#ff3b3b', 3)
    k.text('MOISTURE', 540, 650, 32, '#ffffff', t0=0.2)
    k.stamp('STUCK OFFSHORE', 540, 800, 40, '#ff6a5a', t0=0.8)
    return img


# =========================================================================== 10. globes
def stream_particles(img, P, pts_fn, u, alpha_fn, cols, gp, trail=2):
    """Draw particles given lat/lon positions."""
    g = GLOBE()
    cx, cy, R, lon0, lat0 = gp
    for j, col in enumerate(cols[:trail + 1]):
        lat, lon = pts_fn(u - j * 0.012)
        x, y, vis = g.project(lat, lon, cx, cy, R, lon0, lat0)
        a = alpha_fn
        for i in range(len(x)):
            if vis[i] and a[i] > BAYER[int(y[i]) % 4, int(x[i]) % 4]:
                img[int(y[i]), int(x[i])] = C(col)


def latlon_path(pts):
    pts = np.asarray(pts, np.float32)

    def f(u):
        u = np.mod(u, 1.0)
        a, b = path_interp(pts, u)
        return a, b
    return f


def shot_monsoon(t, k):
    img = space_bg()
    gp = (135, 195, 100, 80 + 6 * t / max(1, k.dur), 16)
    info = GLOBE().render(img, *gp)
    tn = k.word('never') if k.has_word('never') else k.dur * 0.6
    p = ramp(t, tn - 0.6, tn + 1.2)
    a0, b0, a1, b1 = info['box']
    lat, lon = info['lat'], info['lon']
    m = info['inside'] & (info['typ'] == 1) & (lat > 6) & (lat < 32) & (lon > 66) & (lon < 96)
    sub = img[b0:b1, a0:a1]
    mm = m & ((0.85 * p) > BAYER[b0:b1, a0:a1])
    sub[mm] = np.where(info['shade'][mm][:, None] > 0.5, C('#c9a060'), C('#9a7440'))
    P = Particles(80, 41)
    paths = [latlon_path([(-4, 58), (4, 64), (11, 69), (16, 72.5)]), latlon_path([(0, 88), (8, 89), (15, 90), (21, 90)])]
    halt = decel(t, tn - 0.6, 0.8)
    for s, f in enumerate(paths):
        sel = slice(s * 40, s * 40 + 40)
        u = (P.u[sel] + 0.32 * halt) % 1.0
        lat_, lon_ = f(u)
        lat_ = lat_ + (P.a[sel] - 0.5) * 5
        lon_ = lon_ + (P.b[sel] - 0.5) * 5
        x, y, vis = GLOBE().project(lat_, lon_, *gp)
        fade = (1 - ramp(t, tn + 0.2, tn + 1.0)) * np.clip((0.82 - u) / 0.25, 0, 1)
        for i in range(len(x)):
            if vis[i] and fade[i] > 0.05:
                pk.rect(img, x[i], y[i], x[i] + 2, y[i] + 1, '#bfe6ff', alpha=float(fade[i]))
    lx, ly, v = GLOBE().project(23, 79, *gp)
    if v:
        k.text('INDIA', Hx(float(lx)), Hx(float(ly)), 24, '#ffffff', t0=0.2)
    k.stamp('MONSOON:', 540, 1320, 40, '#ffffff', t0=tn)
    k.stamp('CANCELLED', 540, 1390, 48, '#ff4d4d', t0=tn + 0.15)
    return img


def shot_hurricane(t, k):
    img = space_bg(5)
    gp = (135, 195, 100, -58 + 4 * t / max(1, k.dur), 22)
    GLOBE().render(img, *gp)
    th = k.word('hurricanes') if k.has_word('hurricanes') else 0.8
    fade = 1 - ramp(t, th, th + 1.1)
    hx, hy, vis = GLOBE().project(24, -64, *gp)
    hx, hy = float(hx), float(hy)
    if fade > 0.02:
        rot = -2.6 * decel(t, th, 1.2)
        for arm in range(3):
            for s in np.linspace(0, 1, 70):
                r = 3 + 30 * s * (1 + 0.6 * (1 - fade))
                a = rot + arm * TAU / 3 + s * 4.6
                x = hx + r * math.cos(a)
                y = hy + r * math.sin(a) * 0.92
                pk.disk(img, x, y, 2.6 * (1 - s) + 0.9, '#f4f8ff', alpha=fade * (1 - 0.65 * s))
        pk.disk(img, hx, hy, 2.2, '#2a4a7a', alpha=fade)
    # tornado icon over the US plains
    tx, ty, tv = GLOBE().project(37, -97, *gp)
    tf = 1 - ramp(t, k.word('tornadoes') if k.has_word('tornadoes') else th + 1, (k.word('tornadoes') if k.has_word('tornadoes') else th + 1) + 0.7)
    if tv and tf > 0.02:
        tx, ty = float(tx), float(ty)
        for j in range(14):
            w = 1 + j * 0.45
            x = tx + 1.5 * math.sin(t * 9 + j * 0.5)
            pk.rect(img, x - w, ty - j * 1.5, x + w, ty - j * 1.5 + 1.5, '#c9c4b8', alpha=tf)
    bad = k.cue(1) if k.nlines > 1 else 1e9
    k.stamp('GOOD NEWS', 540, 470, 48, '#5bea6a', t0=0.05, t1=bad)
    if t >= bad:
        dt = t - bad
        pk.tint(img, '#ff1a2a', 0.25 + 0.15 * math.sin(t * 12))
        k.stamp('BAD NEWS', 540, 470, 64, '#ff3b3b', t0=bad)
        k.shake(10 * max(0.0, 1 - dt / 0.5))
        if dt < 0.45 or (1.1 < dt < 1.25) or (k.word('started') - 0.05 < t < k.word('started') + 0.2):
            img = glitch(img, k.fi, 1.0, 3)
    return img


GULF = [(24, -82), (27, -79.6), (31, -79), (35, -74.5), (38, -70), (40, -62), (42, -50), (45, -40), (49, -28),
        (53, -18), (57, -10), (62, -3), (67, 6)]


def shot_gulf(t, k):
    img = space_bg(7)
    c1 = k.cue(1) if k.nlines > 1 else k.dur
    lon0 = -50 + 22 * ramp(t, c1 - 0.3, c1 + 1.6)
    gp = (135, 195, 100, lon0, 40)
    info = GLOBE().render(img, *gp)
    tf = k.word('fade') if k.has_word('fade') else 1.8
    P = Particles(120, 61)
    f = latlon_path(GULF)
    adv = decel(t, tf - 0.9, 1.6)
    u = (P.u + 0.16 * adv) % 1.0
    alpha = (1 - ramp(t, tf + 0.2, tf + 1.4)) * np.ones(P.n)
    for j, col in enumerate(['#ffe08a', '#ff9a3c', '#d9502a']):
        lat_, lon_ = f(u - j * 0.01)
        lat_ = lat_ + (P.a - 0.5) * 2.4
        lon_ = lon_ + (P.b - 0.5) * 2.4
        x, y, vis = GLOBE().project(lat_, lon_, *gp)
        for i in range(P.n):
            if vis[i] and alpha[i] > 0.02:
                pk.rect(img, x[i], y[i], x[i] + 1, y[i] + 1, col, alpha=float(alpha[i]))
    lx, ly, v = GLOBE().project(30, -66, *gp)
    if v:
        k.text('GULF STREAM', Hx(float(lx)) - 40, Hx(float(ly)), 24, '#ffb36a', t0=0.2, t1=c1)
    if k.nlines > 1:
        s = 0.8 * ramp(t, c1 + 0.6, c1 + 2.2)
        a0, b0, a1, b1 = info['box']
        lat, lon = info['lat'], info['lon']
        m = info['inside'] & ((info['typ'] == 1) | (info['typ'] == 2)) & (lat > 42) & (lat < 72) & (lon > -11) & (lon < 45)
        sub = img[b0:b1, a0:a1]
        sub[m & (s > BAYER[b0:b1, a0:a1])] = C('#eef4ff')
        ex, ey, v = GLOBE().project(52, 12, *gp)
        if v:
            k.text('EUROPE', Hx(float(ex)), Hx(float(ey)) - 70, 24, '#ffffff', t0=c1 + 0.4)
        k.stamp('WINTERS BITE', 540, 1340, 40, '#9fd8ff', t0=k.word('mild') if k.has_word('mild') else c1 + 1)
    return img


SAHARA = (19, 3)
AMAZON = (-4, -60)


def shot_dust(t, k):
    img = space_bg(9)
    gp = (135, 195, 102, -30 + 5 * t / max(1, k.dur), 6)
    info = GLOBE().render(img, *gp)
    tc = k.word('cancelled') if k.has_word('cancelled') else 1e9
    P = Particles(160, 71)
    adv = decel(t + 4, tc + 4, 0.5)
    u = (P.u + 0.2 * adv) % 1.0
    la0 = SAHARA[0] + (P.a - 0.5) * 12
    lo0 = SAHARA[1] + (P.b - 0.5) * 24
    la2 = AMAZON[0] + (P.b - 0.5) * 10
    lo2 = AMAZON[1] + (P.a - 0.5) * 14
    la1, lo1 = 15 + (P.c - 0.5) * 8, -32 + (P.c - 0.5) * 6
    lat_ = (1 - u) ** 2 * la0 + 2 * u * (1 - u) * la1 + u * u * la2
    lon_ = (1 - u) ** 2 * lo0 + 2 * u * (1 - u) * lo1 + u * u * lo2
    drop = ramp(t, tc + 0.2, tc + 1.2)
    lat_ = lat_ - drop * 3 * P.c
    x, y, vis = GLOBE().project(lat_, lon_, *gp)
    fade = 1 - ramp(t, tc + 0.3, tc + 1.4)
    for i in range(P.n):
        if vis[i] and fade > 0.02:
            col = '#f0c27a' if P.k[i] % 2 else '#d4964a'
            pk.rect(img, x[i], y[i], x[i] + 2, y[i] + 1, col, alpha=fade * (0.6 + 0.4 * math.sin(math.pi * u[i])))
    if tc < 1e8:
        p = ramp(t, tc + 0.2, tc + 1.6)
        a0, b0, a1, b1 = info['box']
        lat, lon = info['lat'], info['lon']
        m = info['inside'] & (info['typ'] == 1) & (lat > -16) & (lat < 6) & (lon > -76) & (lon < -44)
        sub = img[b0:b1, a0:a1]
        sub[m & ((0.7 * p) > BAYER[b0:b1, a0:a1])] = C('#7a7038')
        k.stamp('CANCELLED', 540, 720, 72, '#ff4d4d', t0=tc + 0.05)
    sx, sy, v1 = GLOBE().project(SAHARA[0] + 4, SAHARA[1], *gp)
    ax, ay, v2 = GLOBE().project(AMAZON[0] - 6, AMAZON[1], *gp)
    if v1:
        k.text('SAHARA', Hx(float(sx)), Hx(float(sy)) - 50, 24, '#ffd08a', t0=0.2)
    if v2:
        k.text('AMAZON', Hx(float(ax)), Hx(float(ay)) + 50, 24, '#9fef8a', t0=0.5)
    if k.nlines > 1 or tc > 1e8:
        k.text('~28 MILLION TONS / YEAR', 540, 1340, 32, '#ffd08a', t0=0.9, t1=tc if tc < 1e8 else None)
    return img


def shot_heat(t, k):
    img = space_bg(11)
    off = k.mode == 'off'
    gp = (135, 196, 108, 12 + 3 * t / max(1, k.dur), 0)
    info = GLOBE().render(img, *gp)
    a0, b0, a1, b1 = info['box']
    lat = np.abs(info['lat'])
    ins = info['inside']
    bay = BAYER[b0:b1, a0:a1]
    sub = img[b0:b1, a0:a1]
    if off:
        hot = 0.35 + 0.35 * ramp(t, 0.2, 1.6) + 0.06 * math.sin(t * 6)
        cold = 0.35 + 0.5 * ramp(t, 0.4, 1.8)
        cold_lat = 58 - 12 * ramp(t, 0.4, 2.0)
    else:
        hot, cold, cold_lat = 0.3, 0.32, 60
    ah = hot * np.clip((26 - lat) / 14, 0, 1)
    ac = cold * np.clip((lat - cold_lat) / 10, 0, 1)
    sub[ins & (ah > bay)] = C('#ff5a2a')
    sub[ins & (ah > bay + 0.45)] = C('#ffb347')
    sub[ins & (ac > bay)] = C('#eaf6ff')
    # heat flowing poleward
    P = Particles(110, 81)
    t_stop = 0.3 if off else 1e9
    adv = decel(t + (8 if off else 0), t_stop + (8 if off else 0), 0.8)
    u = (P.u + 0.22 * adv) % 1.0
    sgn = np.where(P.k % 2 == 0, 1, -1)
    plat = sgn * (4 + u * 74)
    plon = gp[3] - 70 + P.a * 140
    x, y, vis = GLOBE().project(plat, plon, *gp)
    fade = 1 - (ramp(t, 0.4, 1.2) if off else 0)
    for i in range(P.n):
        if vis[i] and fade > 0.02:
            pk.rect(img, x[i], y[i], x[i] + 1, y[i] + 2, '#ffd08a' if u[i] < 0.5 else '#ff9a5a', alpha=float(fade * (1 - u[i] * 0.7)))
    if not off:
        k.text("EARTH'S A/C", 540, 1340, 32, '#ffd08a', t0=0.6)
    else:
        tb = k.word('bake') if k.has_word('bake') else 0.8
        tf = k.word('freeze') if k.has_word('freeze') else 1.6
        k.stamp('TROPICS ↑', 540, 780, 48, '#ff6a3a', t0=tb)
        k.stamp('POLES ↓', 540, 470, 48, '#bfe6ff', t0=tf)
        k.shake(4 * math.sin(t * 40) * ramp(t, tb, tb + 0.2) * (1 - ramp(t, tb + 0.2, tb + 0.6)))
    return img


# =========================================================================== 11. calm sea
SEA_DASH = make_dashes(260, 5)


@functools.lru_cache(None)
def _sea_sky(name):
    img = pk.canvas()
    if name == 'golden':
        pk.vgrad(img, 0, 250, ['#24396e', '#3f63a6', '#7da2d6', '#f3c08e', '#ffdca8'])
        pk.glow(img, 95, 214, 44, '#ffe7b0', 0.8, 1.6)
        pk.disk(img, 95, 214, 12, '#fff6dc')
        pk.blit(img, pk.cloud_sprite(70, 14, 31, '#ffe9d0', '#f2c0a0', '#c98a8a'), 150, 120)
        pk.blit(img, pk.cloud_sprite(46, 10, 32, '#ffe9d0', '#f2c0a0', '#c98a8a'), 20, 70)
    else:
        pk.vgrad(img, 0, 250, ['#1b1838', '#3a2a5e', '#8a4a78', '#e0786a', '#f7b07a'])
        pk.glow(img, 150, 252, 60, '#ffb070', 0.75, 1.6)
        pk.blit(img, pk.cloud_sprite(80, 12, 33, '#f7b07a', '#c46a78', '#6a3a6a'), 20, 150)
        pk.stars(img, 30, 8, y1=110)
    return img


def shot_sea(t, k):
    img = _sea_sky('golden').copy()
    A = 1 - ramp(t, 0.3, (k.cue(1) + 1.6) if k.nlines > 1 else 2.5)
    # far sailboat at the horizon, sail going limp
    bx = 182
    pk.rect(img, bx - 7, 246, bx + 8, 250, '#3a2a2a')
    pk.rect(img, bx - 8, 246, bx + 9, 247, '#5a3a2a')
    pk.line(img, bx, 246, bx, 222, '#3a2a2a')
    bulge = 7 * A
    pts = [(bx + 1, 223), (bx + 1, 245)] + [(bx + 2 + bulge * math.sin(math.pi * s) + 7 * s * A + 2 * s, 245 - 21 * (1 - s) * (0.4 + 0.6 * A)) for s in np.linspace(0, 1, 6)]
    if A < 0.3:
        pts = [(bx + 1, 223), (bx + 1, 245), (bx + 4, 244), (bx + 3, 236), (bx + 5, 230), (bx + 3, 224)]
    pk.poly(img, pts, '#f4efe2')
    sea_reflect(img, 250, 2.2 * A, t, dashes=SEA_DASH, glitter_x=95)
    if k.nlines > 1:
        c1 = k.cue(1)
        k.text('WAVE HEIGHT', 540, 470, 32, '#ffffff', t0=c1)
        k.text('%.1f M' % (2.4 * A), 540, 550, 72, '#9fd8ff' if A > 0.02 else '#ffe14d', t0=c1, pop=False)
    return img


ALBATROSS = pk.sprite([
    "...................www......",
    "..................wwwww.....",
    "..................wwwkwbbb..",
    "...................wwwwbbbbo",
    "....ddddddddddd...wwww......",
    "..dddddddddddddddwwwww......",
    ".ddddddddddddddwwwwwww......",
    "dddddwwwwwwwwwwwwwwww.......",
    ".wwwwwwwwwwwwwwwwwwww.......",
    "..sswwwwwwwwwwwwwwss........",
    "....ssssssssssssss..........",
], {'w': '#f4f1ea', 'd': '#3b3e4c', 'k': '#111111', 'b': '#f0c39a', 'o': '#c98a5a', 's': '#b9b4aa'})


def shot_albatross(t, k):
    img = _sea_sky('dusk').copy()
    sea_reflect(img, 250, 0.0, t, tintc='#2a1f45', tinta=0.2)
    bx, by = 107, 318 + int(round(0.7 * math.sin(t * 1.8)))
    for j in range(4):
        r = (t * 10 + j * 16) % 64
        pk.ellipse(img, bx + 28, by + 22, 22 + r, 3 + r * 0.22, '#f7c08a', alpha=max(0.0, 0.7 - r / 80), ring=1)
    rgb, m = ALBATROSS
    big = (np.repeat(np.repeat(rgb, 2, 0), 2, 1), np.repeat(np.repeat(m, 2, 0), 2, 1))
    pk.blit(img, big, bx, by)
    pk.blit(img, (big[0] * 0.5 + C('#2a1f45') * 0.3, big[1]), bx, by + 22, flipy=True)
    k.text('WINGSPAN: UP TO 3.5 M', 540, 470, 32, '#ffffff', t0=0.3)
    k.stamp('GROUNDED', 540, 640, 64, '#ff6a5a', t0=k.word('stranded') if k.has_word('stranded') else 1.2)
    return img


# =========================================================================== 12. upwelling cross-section
COAST = [(270, 140), (252, 158), (234, 170), (217, 205), (197, 260), (172, 330), (132, 400), (82, 445), (0, 465), (0, LH), (LW, LH)]
NUTRIENT_PATH = [(30, 460), (100, 432), (150, 380), (182, 312), (204, 250), (220, 196), (214, 178), (180, 176), (130, 178), (80, 182)]
FISH = pk.sprite([
    "....##..",
    "#..####.",
    "##aaaak#",
    "#..####.",
    "....##..",
], {'#': '#9fb8cc', 'a': '#dbe8f2', 'k': '#111111'})


@functools.lru_cache(None)
def _upwell_static():
    img = pk.canvas()
    pk.vgrad(img, 0, 170, ['#4f8fd0', '#8fc0ea', '#cfe6f6'])
    pk.glow(img, 40, 50, 26, '#fff2c0', 0.8, 1.5)
    pk.disk(img, 40, 50, 9, '#fffbe8')
    pk.vgrad(img, 170, LH, ['#2f8fd0', '#1f6fb0', '#17508c', '#0f3566', '#0a2246'])
    m = pk.pmask(COAST)
    img[m] = C('#6b5a3e')
    edge = m & ~np.roll(m, -3, axis=1)
    img[edge & (YY > 170)] = C('#b39b6a')
    img[m & (YY < 170)] = C('#7a6048')
    img[m & (YY < 145)] = C('#5a9a4a')
    pk.rect(img, 0, 170, 236, 171, '#bfe6ff')
    return img


def shot_upwell(t, k):
    img = _upwell_static().copy()
    off = k.mode == 'off'
    life = (1 - ramp(t, 0.2, 2.2)) if off else 1.0
    # wind arrow above the sea
    wa = life if off else 1.0
    pk.rect(img, 140, 138, 215, 145, '#ffffff', alpha=wa)
    pk.poly(img, [(142, 128), (142, 155), (120, 141.5)], '#ffffff', alpha=wa)
    if off:
        pk.line(img, 152, 124, 188, 160, '#ff3b3b', 3)
        pk.line(img, 188, 124, 152, 160, '#ff3b3b', 3)
    # surface water pushed offshore
    adv = decel(t + 9, 9.2, 1.0) if off else t + 9
    for xx in range(0, 230, 22):
        x = (xx - 14 * adv) % 230
        pk.polyline(img, [(x + 4, 176), (x, 179), (x + 4, 182)], '#bfe6ff', 1, alpha=life)
    # plankton bloom
    a = 0.55 * life * np.clip(1 - np.abs(YY - 186) / 24, 0, 1) * np.clip((XX - 100) / 60, 0, 1) * (XX < 236)
    pk.dfill(img, '#3fae6a', a)
    # nutrients rising
    P = Particles(110, 91)
    u = (P.u + 0.09 * adv) % 1.0
    x, y = path_interp(NUTRIENT_PATH, u)
    x = x + (P.a - 0.5) * 10
    y = y + (P.b - 0.5) * 8
    if off:
        y = y + 8 * max(0.0, t - 0.2) * P.c
    for i in range(P.n):
        col = '#c8f07a' if P.k[i] % 2 else '#8fe36a'
        pk.rect(img, x[i], y[i], x[i] + 1, y[i] + 1, col, alpha=(0.35 + 0.65 * life) if not off else max(0.15, life))
    # fish
    rng = RNG(5)
    for j in range(10):
        fx0 = 120 + rng.rand() * 100
        fy = 182 + rng.rand() * 34
        ph = rng.rand() * 6
        if off:
            x = fx0 - 30 * t * (1 + j % 3) + 8 * math.sin(t + ph)
            al = life
            flip = True
        else:
            x = fx0 + 16 * math.sin(0.7 * t + ph)
            flip = math.cos(0.7 * t + ph) < 0
            al = 1.0
        if al > 0.02:
            pk.blit(img, FISH, x, fy + 2 * math.sin(1.3 * t + ph), flipx=flip, alpha=al)
    if not off:
        k.text('WIND', Hx(178), Hx(120), 32, '#ffffff', t0=0.1)
        k.text('NUTRIENTS ↑', Hx(84), Hx(352), 24, '#c8f07a', t0=0.6)
        k.text('PLANKTON + FISH', Hx(176), Hx(232), 24, '#9fef8a', t0=1.2)
    else:
        k.stamp('FOOD CHAIN ↓', 540, 1340, 48, '#ff6a5a', t0=k.word('bottom') if k.has_word('bottom') else 1.0)
    return img


def shot_fishstat(t, k):
    img = info_bg('#061630', '#0b2a52')
    gx, gy = 80, 115
    hl = (3, 7)
    for r in range(10):
        for c in range(10):
            idx = r * 10 + c
            if t < 0.1 + idx * 0.006:
                continue
            x, y = gx + c * 11, gy + r * 11
            if (r, c) == hl and t > 0.9:
                col = '#ffe14d' if (k.fi // 5) % 2 == 0 or t > 2.0 else '#fff6b0'
            else:
                col = '#1f5fa8'
            pk.rect(img, x, y, x + 9, y + 9, col)
            pk.rect(img, x, y, x + 9, y + 1, mixc(col, '#ffffff', 0.25))
    tf = k.word('fifth') if k.has_word('fifth') else 2.0
    for j in range(5):
        if t < tf - 0.4 + j * 0.08:
            continue
        x = 48 + j * 36
        spr_c = '#ffd23f' if (j == 2 and t > tf) else '#4f7fb0'
        rgb, m = FISH
        big = (np.repeat(np.repeat(rgb, 3, 0), 3, 1), np.repeat(np.repeat(m, 3, 0), 3, 1))
        pk.blit(img, big, x, 326, color=spr_c)
        if j == 2 and t > tf:
            pk.rect(img, x + 18, 332, x + 20, 334, '#111111')
    k.text('UPWELLING ZONES', 540, 430, 32, '#ffffff', t0=0.1)
    k.stamp('< 1% OF THE OCEAN', 540, 950, 40, '#ffe14d', t0=0.9)
    k.stamp('UP TO 1 IN 5', 540, 1420, 48, '#ffd23f', t0=tf)
    k.text('WILD-CAUGHT FISH', 540, 1490, 32, '#ffffff', t0=tf + 0.2)
    return img


# =========================================================================== 13. corn field
def corn_stalk(img, x, base, top, s, cols, rng, tassel=True, cob=True):
    stalk, leaf, rim, tas = cols
    w = max(1, int(round(2 * s)))
    pk.line(img, x, base, x, top, stalk, w)
    pk.line(img, x + w - 1, base, x + w - 1, top + 4, rim, 1)
    for j in range(5):
        ly = top + 16 * s + j * (base - top - 16 * s) / 5.5
        d = 1 if (j + int(x)) % 2 == 0 else -1
        L = (16 + 10 * rng.rand()) * s
        pts = [(x + d * i * L / 6, ly - 2 * s * i + 0.55 * i * i * s) for i in range(7)]
        pk.polyline(img, pts, leaf, max(1, int(round(1.6 * s))))
        pk.polyline(img, [(px, py - 1) for px, py in pts[:4]], rim, 1)
    if cob:
        cy = top + (base - top) * 0.42
        pk.ellipse(img, x + 3 * s, cy, 2.2 * s, 6 * s, '#6f9a3a')
        pk.ellipse(img, x + 3.6 * s, cy - 1, 1.3 * s, 4.5 * s, '#e0b84a')
        pk.line(img, x + 3 * s, cy - 6 * s, x + 4 * s, cy - 9 * s, '#c9a060')
    if tassel:
        pk.line(img, x, top, x, top - 10 * s, tas, 1)
        for j in range(4):
            yy = top - 2 * s - j * 2 * s
            pk.line(img, x, yy, x - (3 + j) * s, yy + (5 - j) * s, tas, 1)
            pk.line(img, x, yy, x + (3 + j) * s, yy + (5 - j) * s, tas, 1)


TASSELS = []


@functools.lru_cache(None)
def _corn_static():
    img = pk.canvas()
    pk.vgrad(img, 0, 222, ['#2a1f4a', '#5b2f5e', '#b8505a', '#f08a4b', '#ffc56b'])
    pk.glow(img, 66, 206, 60, '#ffd88a', 0.9, 1.4)
    pk.disk(img, 66, 206, 19, '#fff3c4')
    rng = RNG(23)
    pk.rect(img, 0, 220, LW, LH, '#2e2a18')
    for x in range(-2, LW + 2, 3):
        h = 10 + rng.rand() * 6
        pk.line(img, x, 226, x, 226 - h, '#3d3a22')
        pk.rect(img, x - 1, 226 - h - 2, x + 2, 226 - h, '#ffb85a')
    for row in range(3):
        base = 250 + row * 26
        s = 0.45 + row * 0.18
        for x in range(-4 + row * 5, LW + 6, int(16 + row * 6)):
            top = base - (40 + 18 * row) * (0.9 + 0.2 * rng.rand())
            corn_stalk(img, x + rng.randint(-2, 3), base + 30, top, s, ('#3a4a20', '#46602a', '#e0a050', '#d9a050'), rng, cob=row == 2)
            TASSELS.append((x, top - 6 * s, s))
    pk.rect(img, 0, 330, LW, LH, '#22200f')
    for i, x in enumerate([14, 74, 132, 196, 252]):
        top = 120 + (i % 2) * 34 + rng.randint(-8, 8)
        corn_stalk(img, x, LH + 10, top, 1.6, ('#3e5a24', '#4f7a2a', '#ffcf6a', '#e8c070'), rng)
        TASSELS.append((x, top - 9, 1.6))
    return img


def shot_corn(t, k):
    img = _corn_static().copy()
    # god rays from the low sun
    ang = np.arctan2(YY - 206, XX - 66)
    rays = 0.12 * (0.5 + 0.5 * np.sin(ang * 22 + 0.15 * t)) * np.clip(1 - np.hypot(YY - 206, XX - 66) / 300, 0, 1)
    pk.dfill(img, '#ffd88a', rays * (YY < 330))
    # pollen: hanging, going nowhere
    rng = RNG(44)
    for (x, y, s) in TASSELS:
        n = int(3 + 6 * s)
        for j in range(n):
            px = x + (rng.rand() - 0.5) * 22 * s
            py = y + rng.rand() * 26 * s + t * (1.2 + rng.rand() * 1.5) * s
            tw = math.sin(t * 3 + j + x) > -0.4
            if tw:
                pk.rect(img, px, py, px + (2 if s > 1 else 1), py + (2 if s > 1 else 1), '#fff2a0')
    k.text('POLLEN: STUCK', 540, 470, 32, '#fff2a0', t0=k.word('pollen') if k.has_word('pollen') else 1.5)
    return img


# =========================================================================== 14. pines / oaks / grasses
def pine(img, x, base, h, col, hi):
    tiers = 5
    for j in range(tiers):
        ty = base - h + j * h / (tiers + 1)
        w = (j + 1) * h / (tiers * 2.6)
        pk.poly(img, [(x - w, ty + h / (tiers) * 1.1), (x, ty), (x + w, ty + h / tiers * 1.1)], col)
        pk.line(img, x, ty, x - w, ty + h / tiers * 1.1, hi)
    pk.rect(img, x - 1, base - h * 0.12, x + 1, base, '#2a1f18')


@functools.lru_cache(None)
def _pines():
    img = pk.canvas()
    pk.vgrad(img, 0, 300, ['#8fb0c4', '#bcd2dc', '#e6ece4', '#f4ecd0'])
    pk.glow(img, 180, 120, 50, '#fff6d8', 0.8, 1.4)
    pk.fill_below(img, pk.ridge_heights(230, 40, 3, 70, 3), '#9fb4c0', None)
    rng = RNG(30)
    for layer, (n, hmin, hmax, base, col, hi) in enumerate([
            (26, 22, 34, 262, '#7a9a98', '#8fb0aa'), (14, 46, 70, 300, '#4f7066', '#6a8a7a'), (5, 120, 170, 360, '#1f3a32', '#2f5246')]):
        if layer == 2:
            xs = [150, 196, 238, 268, 110]
        else:
            xs = sorted(rng.rand(n) * (LW + 20) - 10)
        pk.fill_below(img, pk.ridge_heights(base + 6, 8, 40 + layer, 50, 2), col, None)
        for x in xs:
            pine(img, x, base + 8, hmin + rng.rand() * (hmax - hmin), col, hi)
        pk.dfill(img, '#e6ece4', 0.25 * (2 - layer) / 2 * (YY < base + 10) * (YY > base - 120))
    # oak on the left
    pk.rect(img, 34, 250, 44, 380, '#3a2a1f')
    pk.line(img, 39, 290, 18, 252, '#3a2a1f', 3)
    pk.line(img, 40, 280, 66, 246, '#3a2a1f', 3)
    for (cx, cy, r) in [(40, 214, 30), (14, 236, 22), (68, 232, 24), (30, 196, 22), (56, 200, 22), (40, 250, 18)]:
        pk.disk(img, cx, cy, r, '#2f4f26')
    for (cx, cy, r) in [(36, 208, 24), (12, 230, 16), (64, 226, 18), (28, 190, 15), (52, 194, 15)]:
        pk.disk(img, cx - 2, cy - 3, r, '#4a7a34')
    for (cx, cy, r) in [(30, 200, 10), (58, 188, 8), (10, 226, 7)]:
        pk.disk(img, cx - 3, cy - 4, r, '#6a9a44')
    pk.rect(img, 0, 372, LW, LH, '#3a5a2a')
    for i in range(140):
        x = rng.rand() * LW
        y = 380 + rng.rand() * 100
        h = 8 + rng.rand() * 16
        pk.line(img, x, y, x + rng.rand() * 2 - 1, y - h, '#7a9a3a')
        pk.rect(img, x - 1, y - h - 3, x + 1, y - h, '#d9c070')
    return img


def shot_pines(t, k):
    img = _pines().copy()
    n = pk.fbm2d(LH, LW, 16, 7)
    a = (0.25 + 0.2 * ramp(t, 0, k.dur)) * np.clip((n - 0.45) * 3, 0, 1) * (YY > 140) * (YY < 400)
    pk.dfill(img, '#e8d26a', a)
    rng = RNG(66)
    for i in range(160):
        x = rng.rand() * LW
        y = 160 + rng.rand() * 230 + t * 1.5
        if math.sin(t * 2 + i) > -0.5:
            pk.rect(img, x, y, x + 1, y + 1, '#fff0a0')
    k.text('PINES', Hx(200), Hx(176), 32, '#fff2a0', t0=k.word('pines') if k.has_word('pines') else 0.2)
    k.text('OAKS', Hx(40), Hx(160), 32, '#fff2a0', t0=k.word('oaks') if k.has_word('oaks') else 0.6)
    k.text('GRASSES', 540, Hx(352), 32, '#fff2a0', t0=k.word('grasses') if k.has_word('grasses') else 1.0)
    return img


# =========================================================================== 15. dandelion
_dr = RNG(88)
DN = 96
_v = _dr.randn(DN, 3)
_v /= np.linalg.norm(_v, axis=1, keepdims=True)
D_SX, D_SY, D_SZ = _v[:, 0], _v[:, 1], _v[:, 2]
D_ORDER = np.argsort(D_SZ)
D_DET = np.sort(_dr.rand(DN)) ** 0.7
D_PERM = _dr.permutation(DN)
D_LAND = 438 + _dr.rand(DN) * 36


@functools.lru_cache(None)
def _dand_bg():
    img = pk.canvas()
    pk.vgrad(img, 0, LH, ['#bfe0f0', '#a6d4c0', '#7ab07a', '#4a7f4a', '#2f5a35'])
    rng = RNG(3)
    for _ in range(14):
        pk.disk(img, rng.rand() * LW, rng.rand() * 330, 8 + rng.rand() * 20, '#e8f6e0', alpha=0.25)
    pk.vgrad(img, 430, LH, ['#3a6a35', '#2a4f2a'])
    for i in range(160):
        x = rng.rand() * LW
        y = 440 + rng.rand() * 40
        pk.line(img, x, y, x + rng.rand() * 2 - 1, y - 6 - rng.rand() * 10, '#5a9a4a')
    pts = [(135 + 6 * math.sin(s * 2.4), 480 - s * 316) for s in np.linspace(0, 1, 20)]
    pk.polyline(img, pts, '#6f9a4a', 3)
    pk.polyline(img, [(x + 1, y) for x, y in pts], '#9fc66a', 1)
    return img


def draw_seed(img, x, y, ang, scale=1.0):
    """tuft at (x, y), stalk pointing at angle ang (radians, screen) away from tuft."""
    ex, ey = x + 7 * scale * math.cos(ang), y + 7 * scale * math.sin(ang)
    pk.line(img, x, y, ex, ey, '#e6e0d0')
    pk.rect(img, ex, ey, ex + 1, ey + 2, '#8a6a3a')
    for j in range(5):
        a = ang + math.pi + (j - 2) * 0.55
        pk.line(img, x, y, x + 3 * math.cos(a), y + 3 * math.sin(a), '#ffffff')


def shot_dandelion(t, k):
    img = _dand_bg().copy()
    cx, cy, R = 135, 164, 40
    pk.disk(img, cx, cy, 5, '#b8a77a')
    t0 = 0.3
    span = max(1.0, k.dur - 0.4)
    for idx in D_ORDER:
        td = t0 + D_DET[D_PERM[idx]] * span
        sx, sy, sz = D_SX[idx], D_SY[idx], D_SZ[idx]
        if t < td:
            tx, ty = cx + sx * R, cy + sy * R
            col = '#f6f2ea' if sz > 0 else '#cfc8b8'
            pk.line(img, cx + sx * 4, cy + sy * 4, tx, ty, col)
            for j in range(5):
                a = math.atan2(sy, sx) + (j - 2) * 0.6
                pk.line(img, tx, ty, tx + 3 * math.cos(a), ty + 3 * math.sin(a), col)
        else:
            dt = t - td
            x0, y0 = cx + sx * R, cy + sy * R
            y = min(y0 + 26 * dt + 8 * dt * dt, D_LAND[idx])
            ang0 = math.atan2(sy, sx) + math.pi
            ang = ang0 + (math.pi / 2 - ang0) * min(1.0, dt * 2.5)
            draw_seed(img, x0, y, ang)
    k.text('SEED TRAVEL:', 540, 470, 32, '#ffffff', t0=0.4)
    k.text('0 M', 540, 540, 64, '#ffe14d', t0=0.6)
    return img


# =========================================================================== 16. desertification
@functools.lru_cache(None)
def _desert_layers():
    g = pk.canvas()
    d = pk.canvas()
    hf = pk.ridge_heights(250, 26, 91, 60, 3)
    hm = pk.ridge_heights(300, 30, 92, 70, 2)
    hn = pk.ridge_heights(370, 34, 93, 90, 2)
    for img, cols in [(g, ['#7fae7a', '#5a9a52', '#447f3c', '#8fc47a']), (d, ['#e0b880', '#d4a265', '#c08a50', '#f0cf96'])]:
        pk.fill_below(img, hf, cols[0], cols[3], 1)
        pk.fill_below(img, hm, cols[1], cols[3], 2)
        pk.fill_below(img, hn, cols[2], cols[3], 2)
    # river vs dry riverbed
    rv = [(0, 336), (60, 326), (120, 334), (180, 322), (270, 330)]
    pk.polyline(g, rv, '#4a8fd0', 5)
    pk.polyline(g, [(x, y - 1) for x, y in rv], '#8fc4f0', 1)
    pk.polyline(d, rv, '#a07a4a', 5)
    for x in range(0, LW, 7):
        pk.rect(d, x, 330 + (x * 13) % 6 - 3, x + 3, 331 + (x * 13) % 6 - 3, '#7a5a34')
    # dune ripples
    for y in range(310, LH, 7):
        for x in range((y * 7) % 23, LW, 23):
            pk.line(d, x, y, x + 9, y - 1, '#e8c48a')
    rng = RNG(5)
    for i in range(12):
        x = rng.rand() * LW
        y = hm[int(min(LW - 1, x))] + 4 + rng.rand() * 50
        sc = 0.8 + (y - 280) / 160
        pk.line(g, x, y, x, y - 9 * sc, '#5a4030', 2)
        pk.disk(g, x, y - 13 * sc, 6 * sc, '#2f6f2f')
        pk.disk(g, x - 1, y - 15 * sc, 4 * sc, '#4f9a3f')
        pk.line(d, x, y, x, y - 10 * sc, '#6b5038', 2)
        pk.line(d, x, y - 7 * sc, x - 5 * sc, y - 13 * sc, '#6b5038')
        pk.line(d, x, y - 8 * sc, x + 4 * sc, y - 14 * sc, '#6b5038')
    gm = YY >= 200
    noise = pk.fbm2d(LH, LW, 28, 17) * 0.85 + BAYER[:LH, :LW] * 0.15
    return g, d, gm, noise


def shot_desert(t, k):
    g, d, gm, noise = _desert_layers()
    p = 0.05 + 1.0 * ramp(t, 0.1, k.dur - 0.2)
    img = pk.canvas()
    sky = [mixc(a, b, p) for a, b in zip(['#4a86cf', '#8fbbe8', '#cfe5f5'], ['#d9c79a', '#eadbb0', '#f6eccc'])]
    pk.vgrad(img, 0, 260, sky)
    pk.glow(img, 200, 70, 50, '#fffbe6', 0.6 + 0.3 * p, 1.4)
    pk.disk(img, 200, 70, 13, '#ffffff')
    land = np.where((noise < p)[..., None], d, g)
    m = ~np.all(g == 0, axis=2) | ~np.all(d == 0, axis=2)
    img[gm & m] = land[gm & m]
    if p > 0.5:
        for y in range(200, 280):
            sh = int(round((p - 0.5) * 3 * math.sin(y * 0.8 + t * 10)))
            if sh:
                img[y] = np.roll(img[y], sh, axis=0)
    k.stamp('DESERTS SPREAD', 540, 470, 48, '#ffcf6a', t0=k.word('desert') if k.has_word('desert') else k.dur * 0.6)
    return img


# =========================================================================== 17. solar
@functools.lru_cache(None)
def _solar():
    img = pk.canvas()
    pk.vgrad(img, 0, 262, ['#8fc8f2', '#c4e2f6', '#eef4ea', '#fff4d8'])
    pk.glow(img, 58, 96, 60, '#fffbe6', 0.9, 1.3)
    pk.disk(img, 58, 96, 16, '#ffffff')
    for a in range(16):
        ang = a * TAU / 16
        pk.line(img, 58 + 21 * math.cos(ang), 96 + 21 * math.sin(ang), 58 + (30 + 8 * (a % 2)) * math.cos(ang), 96 + (30 + 8 * (a % 2)) * math.sin(ang), '#fff8d8')
    pk.fill_below(img, pk.ridge_heights(258, 10, 2, 50, 2), '#c9b48e', None)
    pk.rect(img, 0, 260, LW, LH, '#cdb88c')
    pk.dfill(img, '#bfa87a', (pk.fbm2d(LH, LW, 5, 3) > 0.55) * 0.9 * (YY > 260))
    for (y, h) in [(264, 6), (278, 9), (298, 13), (326, 18), (364, 25), (414, 33)]:
        sk = h * 0.45
        mw = h * 2.3
        x = -((y * 3) % int(mw + 3))
        while x < LW:
            pts = [(x + sk, y), (x + sk + mw, y), (x + mw, y + h), (x, y + h)]
            pk.poly(img, pts, '#1b3466')
            for gx in range(1, 4):
                f = gx / 4
                pk.line(img, x + sk * 1 + mw * f, y, x + mw * f, y + h, '#3d64a8')
            pk.line(img, x + sk * 0.5, y + h / 2, x + sk * 0.5 + mw, y + h / 2, '#3d64a8')
            pk.line(img, x + sk, y, x + sk + mw, y, '#d6dde6')
            pk.line(img, x, y + h, x + mw, y + h, '#0d1830')
            pk.line(img, x + mw * 0.2, y + h, x + mw * 0.2, y + h + h * 0.5, '#555b66', max(1, int(h / 12)))
            pk.line(img, x + mw * 0.8, y + h, x + mw * 0.8, y + h + h * 0.5, '#555b66', max(1, int(h / 12)))
            pk.line(img, x + sk + mw * 0.15, y + 1, x + sk + mw * 0.35, y + 1, '#9fc3ff')
            x += mw + 3
    return img


def shot_solar(t, k):
    img = _solar().copy()
    p = ramp(t, 0.2, k.dur - 0.3)
    for y in range(230, 300):
        sh = int(round((0.6 + 1.8 * p) * math.sin(y * 0.9 + t * 11)))
        if sh:
            img[y] = np.roll(img[y], sh, axis=0)
    pk.dfill(img, '#ff9a3c', 0.18 * p * np.clip((YY - 200) / 280, 0, 1))
    temp = int(45 + 27 * p)
    col = '#ffe14d' if temp < 55 else '#ff9a3c' if temp < 65 else '#ff4d4d'
    k.text('PANEL TEMP', 540, 470, 32, '#ffffff', t0=0.1)
    k.text('%d°C' % temp, 540, 550, 72, col, t0=0.1, pop=False)
    k.stamp('OUTPUT ↓', 540, 650, 48, '#ff4d4d', t0=k.word('power') if k.has_word('power') else k.dur * 0.7)
    return img


# =========================================================================== 18. dam
@functools.lru_cache(None)
def _dam():
    img = pk.canvas()
    pk.vgrad(img, 0, 190, ['#6fa8dc', '#a9cdee', '#dfeaf2'])
    strata = ['#8f4a32', '#a8583a', '#c26e45', '#d9895a', '#b8643f']
    rock = pk.canvas()
    for i in range(0, LH, 9):
        pk.rect(rock, 0, i, LW, i + 9, strata[(i // 9) % len(strata)])
    pk.dfill(rock, '#6f3a26', (pk.fbm2d(LH, LW, 9, 4) > 0.6) * 0.8)
    left = [(0, 110), (40, 122), (80, 150), (98, 196), (104, 262), (0, 480)]
    right = [(270, 104), (232, 120), (192, 150), (174, 196), (168, 262), (270, 480)]
    for poly_ in (left, right):
        m = pk.pmask(poly_ + [(poly_[-1][0], 480)])
        img[m] = rock[m]
    shade = pk.pmask([(80, 150), (98, 196), (104, 262), (0, 480), (40, 480)])
    pk.dfill(img, '#4a2418', 0.35, shade)
    # dam wall
    pk.rect(img, 98, 192, 174, 330, '#b8b4aa')
    for x in range(100, 174, 6):
        pk.rect(img, x, 200, x + 1, 330, '#a19c90')
    pk.rect(img, 96, 188, 176, 194, '#d6d2c8')
    for x in (116, 152):
        pk.rect(img, x, 172, x + 8, 194, '#cfcac0')
        pk.rect(img, x - 1, 170, x + 9, 173, '#8a857a')
        pk.rect(img, x + 3, 178, x + 5, 182, '#4a4a4a')
    # power lines on the rim
    for (x, y) in [(30, 122), (240, 116)]:
        pk.line(img, x, y, x, y - 22, '#4a4a52')
        pk.line(img, x - 5, y - 18, x + 5, y - 18, '#4a4a52')
    pk.line(img, 30, 104, 120, 172, '#4a4a52')
    pk.line(img, 240, 98, 156, 172, '#4a4a52')
    return img


def shot_dam(t, k):
    img = _dam().copy()
    p = ease_out((t - 0.2) / max(0.5, k.dur - 0.4))
    L0 = 200
    L = L0 + 120 * p
    xl = lambda y: 98 - (y - 196) * 0.55
    xr = lambda y: 174 + (y - 196) * 0.55
    interior = (XX > xl(YY)) & (XX < xr(YY)) & (YY > 196)
    ring = interior & (YY >= L0) & (YY < L) & ((XX < 98) | (XX > 174))
    pk.dfill(img, '#ece2d0', 0.85, ring)
    face = (YY >= L0) & (YY < L) & (XX >= 98) & (XX < 174)
    pk.dfill(img, '#cfc9bc', 0.3, face)
    water = interior & (YY >= L)
    img[water] = C('#2a6fa8')
    deep = water & (YY > L + 30)
    pk.dfill(img, '#1d5a8f', 0.7, deep)
    for i in range(int(L) + 4, LH, 7):
        for xx in range(int((i * 13 + t * 12) % 30) - 30, LW, 30):
            pk.rect(img, xx, i, xx + 8, i + 1, '#5f9fd6', alpha=0.9)
    pk.rect(img, xl(L), L, xr(L), L + 1, '#9fd0f6')
    pct = int(round(100 - 69 * p))
    k.text('RESERVOIR', 540, 470, 32, '#ffffff', t0=0.1)
    k.text('%d%%' % pct, 540, 550, 72, '#9fd8ff' if pct > 60 else '#ff6a5a', t0=0.1, pop=False)
    k.stamp('HYDRO ↓', 540, 650, 48, '#ff6a5a', t0=k.word('mountains') if k.has_word('mountains') else k.dur * 0.7)
    return img


# =========================================================================== 19. tidal
@functools.lru_cache(None)
def _tidal_sky():
    img = pk.canvas()
    pk.vgrad(img, 0, 220, ['#050818', '#0c1433', '#1a2650', '#2a3a6a'])
    pk.stars(img, 80, 12, y1=200)
    return img


def shot_tidal(t, k):
    img = _tidal_sky().copy()
    mx, my = 190, 130
    pulse = 0.5 + 0.5 * math.sin(t * 2.2)
    pk.glow(img, mx, my, 50, '#9fb4ff', 0.45 + 0.15 * pulse, 1.6)
    pk.disk(img, mx, my, 24, '#e8e6da')
    for (dx, dy, r) in [(-8, -6, 5), (6, 4, 4), (-3, 10, 3), (10, -10, 3)]:
        pk.disk(img, mx + dx, my + dy, r, '#c9c6b6')
    pk.disk(img, mx - 6, my - 8, 2, '#f6f4ec')
    sea = 210 + 5 * math.sin(TAU * t / 5.0)
    under = YY >= sea
    pk.vgrad(img, 200, LH, ['#14506a', '#0d3a50', '#082636', '#05182a'])
    img[~under & (YY >= 200)] = _tidal_sky()[~under & (YY >= 200)]
    pk.rect(img, 0, sea, LW, sea + 1, '#6fb6d6')
    for y in range(int(sea) + 3, int(sea) + 30, 4):
        w = 3 + (y - sea) * 0.3
        pk.rect(img, mx - w, y, mx + w, y + 1, '#c9d6ff', alpha=0.6)
    pk.vgrad(img, 440, LH, ['#2a3a30', '#1f2a24'])
    rng = RNG(8)
    for i in range(10):
        x = rng.rand() * LW
        pk.ellipse(img, x, 446 + rng.rand() * 20, 4 + rng.rand() * 8, 3, '#3a4a3a')
    # current streaks
    for i in range(70):
        x = (rng.rand() * LW + 40 * t * (0.6 + rng.rand())) % (LW + 20) - 10
        y = sea + 10 + rng.rand() * (430 - sea)
        pk.line(img, x, y, x + 4, y, '#2f7a94')
    # tidal turbine
    hx, hy = 135, 372
    pk.rect(img, hx - 3, hy, hx + 3, 446, '#5f6f80')
    pk.rect(img, hx - 12, 440, hx + 12, 448, '#4a5664')
    th = 1.3 * t
    for j in range(3):
        a = th + j * TAU / 3
        ex, ey = hx + 48 * math.sin(a), hy - 48 * math.cos(a)
        nx, ny = math.cos(a), math.sin(a)
        pk.poly(img, [(hx + nx * 5, hy + ny * 5), (ex + nx, ey + ny), (ex - nx, ey - ny), (hx - nx * 2, hy - ny * 2)], '#c9d3dc')
        pk.line(img, hx, hy, ex, ey, '#e8eef4')
    pk.disk(img, hx, hy, 6, '#8f9fb0')
    pk.disk(img, hx - 1, hy - 1, 3, '#dfe6ee')
    for j in range(7):
        fx = (j * 41 - 30 * t) % (LW + 30) - 15
        pk.blit(img, FISH, fx, 300 + (j * 17) % 60, flipx=True)
    tw = k.word('tidal') if k.has_word('tidal') else 0.5
    k.stamp('WINNER:', 540, 960, 40, '#5bea6a', t0=tw)
    k.stamp('TIDAL POWER', 540, 1030, 56, '#5bea6a', t0=tw + 0.12)
    if k.nlines > 1:
        c1 = k.cue(1)
        for s in np.linspace(0, 1, 14):
            if (s * 14 + t * 6) % 2 < 1:
                x = mx + (110 - mx) * s
                y = my + 26 + (sea - my - 26) * s - 30 * math.sin(math.pi * s)
                pk.rect(img, x, y, x + 2, y + 2, '#9fb4ff', alpha=ramp(t, c1, c1 + 0.5))
        k.text('NO WIND NEEDED', 540, 1340, 32, '#9fb4ff', t0=c1 + 0.4)
    return img


SHOTS = {
    'meadow': shot_meadow, 'windfarm': shot_windfarm, 'city': shot_city, 'denmark': shot_denmark,
    'smog': shot_smog, 'london': shot_london, 'storm': shot_storm, 'split': shot_split, 'coast': shot_coast,
    'monsoon': shot_monsoon, 'hurricane': shot_hurricane, 'sea': shot_sea, 'gulf': shot_gulf,
    'upwell': shot_upwell, 'fishstat': shot_fishstat, 'corn': shot_corn, 'pines': shot_pines,
    'dandelion': shot_dandelion, 'albatross': shot_albatross, 'dust': shot_dust, 'heat': shot_heat,
    'desert': shot_desert, 'solar': shot_solar, 'dam': shot_dam, 'tidal': shot_tidal,
}
