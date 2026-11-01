"""pixelkit - a tiny numpy toolkit for drawing low-res pixel art.

Everything draws into float32 RGB arrays (values 0..1) of any size.
Transparency is done the old-school way: ordered (Bayer) dithering.
"""
import functools
import json
import math

import numpy as np
from PIL import Image, ImageDraw

LW, LH = 270, 480  # the low-res "pixel" canvas (upscaled 4x to 1080x1920)

_B4 = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32)
BAYER = ((np.tile(_B4, (256, 256)) + 0.5) / 16.0).astype(np.float32)  # 1024 x 1024
YY, XX = np.mgrid[0:LH, 0:LW].astype(np.float32)
KEY = np.array([1.0, 0.0, 1.0], np.float32)  # transparent key colour for layers

# --------------------------------------------------------------------------- colour
_cc = {}


def C(c):
    """Hex string / tuple / array -> float32 RGB array."""
    if isinstance(c, np.ndarray):
        return c.astype(np.float32)
    if isinstance(c, str):
        v = _cc.get(c)
        if v is None:
            h = c.lstrip('#')
            if len(h) == 3:
                h = ''.join(ch * 2 for ch in h)
            v = np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255.0
            _cc[c] = v
        return v
    return np.asarray(c, np.float32)


def mixc(a, b, t):
    a, b = C(a), C(b)
    return a + (b - a) * float(t)


def clamp01(x):
    return max(0.0, min(1.0, float(x)))


def smooth(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in(x):
    x = clamp01(x)
    return x ** 3


def ramp(t, t0, t1):
    """0 before t0, 1 after t1, smoothstep between."""
    if t1 <= t0:
        return 1.0 if t >= t0 else 0.0
    return smooth((t - t0) / (t1 - t0))


def decel(t, t0, dur):
    """Distance travelled at unit speed that linearly brakes to 0 between t0 and t0+dur."""
    if t <= t0:
        return t
    tau = min(t - t0, dur)
    return t0 + tau - tau * tau / (2 * dur)


def canvas(c='#000000', h=LH, w=LW):
    img = np.empty((h, w, 3), np.float32)
    img[:] = C(c)
    return img


def keycanvas(h=LH, w=LW):
    img = np.empty((h, w, 3), np.float32)
    img[:] = KEY
    return img


def keymask(img):
    return ~np.all(img == KEY, axis=2)


def _clip(img, x0, y0, x1, y1):
    H, W = img.shape[:2]
    return max(0, int(math.floor(x0))), max(0, int(math.floor(y0))), min(W, int(math.ceil(x1))), min(H, int(math.ceil(y1)))


# --------------------------------------------------------------------------- fills
def vgrad(img, y0, y1, cols, x0=0, x1=None):
    """Vertical dithered gradient through a list of colours between rows y0..y1."""
    H, W = img.shape[:2]
    x1 = W if x1 is None else x1
    cs = np.stack([C(c) for c in cols])
    n = len(cs)
    a0, b0, a1, b1 = _clip(img, x0, y0, x1, y1)
    if a1 <= a0 or b1 <= b0:
        return
    ys = np.arange(b0, b1, dtype=np.float32)
    p = np.clip((ys - y0) / max(1e-6, (y1 - y0)) * (n - 1), 0, n - 1 - 1e-4)
    i = p.astype(np.int32)
    f = p - i
    up = f[:, None] > BAYER[b0:b1, a0:a1]
    ca = cs[i][:, None, :]
    cb = cs[np.minimum(i + 1, n - 1)][:, None, :]
    img[b0:b1, a0:a1] = np.where(up[..., None], cb, ca)


def rect(img, x0, y0, x1, y1, c, alpha=1.0):
    a0, b0, a1, b1 = _clip(img, x0, y0, x1, y1)
    if a1 <= a0 or b1 <= b0:
        return
    if alpha >= 1:
        img[b0:b1, a0:a1] = C(c)
    elif alpha > 0:
        m = alpha > BAYER[b0:b1, a0:a1]
        img[b0:b1, a0:a1][m] = C(c)


def dfill(img, c, alpha, mask=None):
    """Dithered fill over the whole image. alpha: scalar or HxW array."""
    H, W = img.shape[:2]
    m = alpha > BAYER[:H, :W]
    if mask is not None:
        m &= mask
    img[m] = C(c)


def tint(img, c, alpha, mask=None):
    """True (non-dithered) alpha blend toward a colour. alpha scalar or HxW."""
    c = C(c)
    if np.isscalar(alpha):
        if mask is None:
            img += (c - img) * alpha
        else:
            img[mask] += (c - img[mask]) * alpha
    else:
        a = alpha[..., None]
        if mask is not None:
            a = a * mask[..., None]
        img += (c - img) * a


def disk(img, cx, cy, r, c, alpha=1.0):
    if r <= 0:
        return
    a0, b0, a1, b1 = _clip(img, cx - r - 1, cy - r - 1, cx + r + 2, cy + r + 2)
    if a1 <= a0 or b1 <= b0:
        return
    yy, xx = np.ogrid[b0:b1, a0:a1]
    m = (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= r * r
    if alpha < 1:
        m &= alpha > BAYER[b0:b1, a0:a1]
    img[b0:b1, a0:a1][m] = C(c)


def ellipse(img, cx, cy, rx, ry, c, alpha=1.0, ring=None):
    if rx <= 0 or ry <= 0:
        return
    a0, b0, a1, b1 = _clip(img, cx - rx - 1, cy - ry - 1, cx + rx + 2, cy + ry + 2)
    if a1 <= a0 or b1 <= b0:
        return
    yy, xx = np.ogrid[b0:b1, a0:a1]
    d = ((xx + 0.5 - cx) / rx) ** 2 + ((yy + 0.5 - cy) / ry) ** 2
    m = d <= 1
    if ring is not None:
        inner = ((xx + 0.5 - cx) / max(0.5, rx - ring)) ** 2 + ((yy + 0.5 - cy) / max(0.5, ry - ring)) ** 2
        m &= inner > 1
    if alpha < 1:
        m &= alpha > BAYER[b0:b1, a0:a1]
    img[b0:b1, a0:a1][m] = C(c)


def glow(img, cx, cy, r, c, strength=1.0, power=2.0, soft=False):
    """Radial glow; dithered by default, additive-soft if soft=True."""
    a0, b0, a1, b1 = _clip(img, cx - r, cy - r, cx + r + 1, cy + r + 1)
    if a1 <= a0 or b1 <= b0:
        return
    yy, xx = np.ogrid[b0:b1, a0:a1]
    d = np.sqrt((xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2) / r
    a = strength * np.clip(1 - d, 0, 1) ** power
    sub = img[b0:b1, a0:a1]
    if soft:
        sub += (C(c) - sub) * a[..., None]
    else:
        m = a > BAYER[b0:b1, a0:a1]
        sub[m] = C(c)


def line(img, x0, y0, x1, y1, c, w=1, alpha=1.0):
    H, W = img.shape[:2]
    n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    xs = np.rint(np.linspace(x0, x1, n)).astype(np.int32)
    ys = np.rint(np.linspace(y0, y1, n)).astype(np.int32)
    if w > 1:
        o = range(-(w // 2), w - w // 2)
        xs = np.concatenate([xs + dx for dx in o for dy in o])
        ys = np.concatenate([ys + dy for dx in o for dy in o])
    v = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    xs, ys = xs[v], ys[v]
    if alpha < 1:
        k = alpha > BAYER[ys, xs]
        xs, ys = xs[k], ys[k]
    img[ys, xs] = C(c)


def polyline(img, pts, c, w=1, alpha=1.0):
    for (xa, ya), (xb, yb) in zip(pts[:-1], pts[1:]):
        line(img, xa, ya, xb, yb, c, w, alpha)


def pmask(pts, h=LH, w=LW):
    im = Image.new('L', (w, h), 0)
    ImageDraw.Draw(im).polygon([(float(x), float(y)) for x, y in pts], fill=255)
    return np.asarray(im) > 0


def poly(img, pts, c, alpha=1.0):
    H, W = img.shape[:2]
    m = pmask(pts, H, W)
    if alpha < 1:
        m &= alpha > BAYER[:H, :W]
    img[m] = C(c)


def pset(img, xs, ys, c, alpha=1.0):
    """Plot many single pixels."""
    H, W = img.shape[:2]
    xs = np.rint(np.asarray(xs)).astype(np.int32)
    ys = np.rint(np.asarray(ys)).astype(np.int32)
    v = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
    xs, ys = xs[v], ys[v]
    if alpha < 1:
        k = alpha > BAYER[ys, xs]
        xs, ys = xs[k], ys[k]
    img[ys, xs] = C(c)


# --------------------------------------------------------------------------- sprites
def sprite(rows, pal):
    """Build a sprite from ascii rows; pal maps char -> colour ('.'/' ' transparent)."""
    h = len(rows)
    w = max(len(r) for r in rows)
    rgb = np.zeros((h, w, 3), np.float32)
    m = np.zeros((h, w), bool)
    for y, r in enumerate(rows):
        for x, ch in enumerate(r):
            if ch in pal:
                rgb[y, x] = C(pal[ch])
                m[y, x] = True
    return rgb, m


def blit(img, spr, x, y, flipx=False, flipy=False, alpha=1.0, color=None, mul=None):
    rgb, m = spr
    if flipx:
        rgb, m = rgb[:, ::-1], m[:, ::-1]
    if flipy:
        rgb, m = rgb[::-1], m[::-1]
    H, W = img.shape[:2]
    x = int(round(x))
    y = int(round(y))
    h, w = m.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    sm = m[y0 - y:y1 - y, x0 - x:x1 - x]
    if alpha < 1:
        sm = sm & (alpha > BAYER[y0:y1, x0:x1])
    src = rgb[y0 - y:y1 - y, x0 - x:x1 - x]
    if color is not None:
        img[y0:y1, x0:x1][sm] = C(color)
    elif mul is not None:
        img[y0:y1, x0:x1][sm] = src[sm] * mul
    else:
        img[y0:y1, x0:x1][sm] = src[sm]


def layer_blit(img, lay, alpha=1.0, mask=None):
    """Composite a full-size key-coloured layer (rgb, mask) onto img."""
    rgb, m = lay
    if mask is not None:
        m = m & mask
    if alpha < 1:
        H, W = img.shape[:2]
        m = m & (alpha > BAYER[:H, :W])
    img[m] = rgb[m]


# --------------------------------------------------------------------------- noise
@functools.lru_cache(maxsize=64)
def _rand1d(seed):
    return np.random.RandomState(seed).rand(4096).astype(np.float32)


def vnoise1d(x, seed=0, scale=20.0):
    vals = _rand1d(seed)
    xs = np.asarray(x, np.float32) / scale
    xi = np.floor(xs).astype(np.int64)
    f = xs - xi
    f = f * f * (3 - 2 * f)
    a = vals[xi % 4096]
    b = vals[(xi + 1) % 4096]
    return a + (b - a) * f


def fbm1d(x, seed=0, scale=40.0, octaves=3):
    tot, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        tot = tot + amp * vnoise1d(x, seed + o * 17, scale / (2 ** o))
        norm += amp
        amp *= 0.5
    return tot / norm


@functools.lru_cache(maxsize=64)
def noise2d(h, w, cell, seed=0):
    rng = np.random.RandomState(seed)
    gh, gw = h // cell + 3, w // cell + 3
    small = (rng.rand(gh, gw) * 255).astype(np.uint8)
    im = Image.fromarray(small).resize((gw * cell, gh * cell), Image.BICUBIC)
    return (np.asarray(im, np.float32)[:h, :w] / 255.0)


def fbm2d(h, w, cell, seed=0, octaves=3):
    tot, amp, norm = 0.0, 1.0, 0.0
    for o in range(octaves):
        tot = tot + amp * noise2d(h, w, max(2, cell // (2 ** o)), seed + 31 * o)
        norm += amp
        amp *= 0.5
    return tot / norm


# --------------------------------------------------------------------------- terrain
def ridge_heights(base, amp, seed, scale=60.0, octaves=3, w=LW, offset=0.0):
    x = np.arange(w, dtype=np.float32) + offset
    return base - amp * fbm1d(x, seed, scale, octaves)


def fill_below(img, ys, c, hi=None, hi_rows=1, alpha=1.0):
    H, W = img.shape[:2]
    m = YY[:H, :W] >= ys[None, :W]
    if alpha < 1:
        m &= alpha > BAYER[:H, :W]
    img[m] = C(c)
    if hi is not None:
        mh = m & (YY[:H, :W] < ys[None, :W] + hi_rows)
        img[mh] = C(hi)


# --------------------------------------------------------------------------- clouds
@functools.lru_cache(maxsize=128)
def cloud_sprite(w, h, seed, light='#ffffff', mid='#dfe6f2', dark='#aab6cf'):
    rng = np.random.RandomState(seed)
    img = keycanvas(h + 6, w + 6)
    blobs = []
    n = max(3, w // 9)
    for i in range(n):
        u = (i + 0.5) / n
        r = (0.45 + 0.55 * math.sin(math.pi * u)) * h * 0.55 * (0.8 + 0.4 * rng.rand())
        cx = 3 + r + u * (w - 2 * r)
        cy = 3 + h - r
        blobs.append((cx, cy, r))
    for (cx, cy, r) in blobs:
        disk(img, cx, cy, r, dark)
    for (cx, cy, r) in blobs:
        disk(img, cx - 0.6, cy - 1.6, r - 1.2, mid)
    for (cx, cy, r) in blobs:
        disk(img, cx - 1.6, cy - 3.0, r - 3.2, light)
    # flat-ish bottom
    img[h + 4:, :] = KEY
    return img, keymask(img)


def storm_sprite(w, h, seed, cols=('#262a35', '#353b4a', '#4b5366', '#616a80')):
    rng = np.random.RandomState(seed)
    img = keycanvas(h + 8, w + 8)
    blobs = []
    for i in range(int(w * h / 80)):
        u = rng.rand()
        hump = math.sin(math.pi * u) ** 0.8
        r = 5 + rng.rand() * h * 0.32 * (0.5 + 0.5 * hump)
        top = 4 + h * (1 - hump) * 0.75
        cy = max(top + r, h + 2 - r * 0.6 - rng.rand() * h * 0.3)
        cx = 4 + r * 0.8 + u * (w - 1.6 * r)
        blobs.append((cx, cy, r))
    for k, c in enumerate(cols):
        for (cx, cy, r) in blobs:
            disk(img, cx - k * 0.8, cy - k * 2.2, r - k * 2.4, c)
    img[h + 6:, :] = KEY
    return img, keymask(img)


# --------------------------------------------------------------------------- post
@functools.lru_cache(maxsize=8)
def vignette_mask(strength=0.55, h=LH, w=LW):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx = (xx + 0.5) / w * 2 - 1
    ny = (yy + 0.5) / h * 2 - 1
    d = np.sqrt((nx * 1.0) ** 2 + (ny * 0.85) ** 2)
    v = np.clip((d - 0.55) / 0.75, 0, 1) ** 1.6 * strength
    # quantise to 4 dithered steps for a pixel feel
    q = np.floor(v * 4 + BAYER[:h, :w]) / 4
    return (1 - np.clip(q, 0, 1)).astype(np.float32)


def saturate(img, s):
    g = img.mean(axis=2, keepdims=True)
    return g + (img - g) * s


# --------------------------------------------------------------------------- globe
class Globe:
    """Orthographic pixel-art globe from Natural Earth land polygons."""

    PAL = {
        0: ('#0a1838', '#11356f', '#1c58a4'),   # ocean
        1: ('#1b4527', '#2c7337', '#4f9f45'),   # land
        2: ('#6e5230', '#a9804a', '#d8b073'),   # desert
        3: ('#8496ab', '#c6d3df', '#eef4f8'),   # ice
    }

    def __init__(self, geojson_path, mw=1440, mh=720):
        self.mw, self.mh = mw, mh
        im = Image.new('L', (mw, mh), 0)
        d = ImageDraw.Draw(im)
        with open(geojson_path) as f:
            gj = json.load(f)

        def ring_pts(ring):
            return [((lon + 180) / 360 * mw, (90 - lat) / 180 * mh) for lon, lat in ring]

        for feat in gj['features']:
            g = feat['geometry']
            polys = [g['coordinates']] if g['type'] == 'Polygon' else g['coordinates']
            for p in polys:
                d.polygon(ring_pts(p[0]), fill=1)
                for hole in p[1:]:
                    d.polygon(ring_pts(hole), fill=0)
        land = np.asarray(im) > 0
        lat = 90 - (np.arange(mh) + 0.5) / mh * 180
        lon = (np.arange(mw) + 0.5) / mw * 360 - 180
        LAT, LON = np.meshgrid(lat, lon, indexing='ij')
        t = land.astype(np.uint8)

        def box(la0, la1, lo0, lo1):
            return (LAT >= la0) & (LAT <= la1) & (LON >= lo0) & (LON <= lo1)

        wob = (fbm2d(mh, mw, 24, 3) - 0.5) * 9.0
        LAT = LAT + wob
        LON = LON + (fbm2d(mh, mw, 24, 9) - 0.5) * 9.0
        des = (box(15, 32, -17, 33) | box(13, 32, 33, 58) | box(24, 36, 58, 72) | box(-31, -19, 116, 141)
               | box(38, 46, 92, 112) | box(-28, -17, 12, 22) | box(28, 37, -117, -104))
        t[land & des] = 2
        ice = (LAT < -60) | box(59, 84, -75, -10) | (LAT > 76)
        t[land & ice] = 3
        self.types = t
        self.pal = {k: np.stack([C(c) for c in v]) for k, v in self.PAL.items()}
        self.L = np.array([-0.55, 0.5, 0.67], np.float32)
        self.L /= np.linalg.norm(self.L)

    def render(self, img, cx, cy, R, lon0, lat0, rim=True):
        H, W = img.shape[:2]
        a0, b0, a1, b1 = _clip(img, cx - R - 4, cy - R - 4, cx + R + 5, cy + R + 5)
        yy, xx = np.mgrid[b0:b1, a0:a1].astype(np.float32)
        x = (xx + 0.5 - cx) / R
        y = (cy - (yy + 0.5)) / R
        rho2 = x * x + y * y
        inside = rho2 <= 1.0
        z = np.sqrt(np.clip(1 - rho2, 0, 1))
        p0, l0 = math.radians(lat0), math.radians(lon0)
        lat = np.arcsin(np.clip(z * math.sin(p0) + y * math.cos(p0), -1, 1))
        lon = l0 + np.arctan2(x, z * math.cos(p0) - y * math.sin(p0))
        latd = np.degrees(lat)
        lond = (np.degrees(lon) + 180) % 360 - 180
        iy = np.clip(((90 - latd) / 180 * self.mh).astype(np.int32), 0, self.mh - 1)
        ix = np.clip(((lond + 180) / 360 * self.mw).astype(np.int32), 0, self.mw - 1)
        typ = self.types[iy, ix]
        shade = np.clip(0.18 + 0.95 * (x * self.L[0] + y * self.L[1] + z * self.L[2]), 0, 1)
        lvl = np.clip(np.floor(shade * 2.6 + (BAYER[b0:b1, a0:a1] - 0.5) * 0.9), 0, 2).astype(np.int32)
        out = np.zeros(x.shape + (3,), np.float32)
        for k, pal in self.pal.items():
            mk = typ == k
            out[mk] = pal[lvl[mk]]
        night = shade < 0.12
        out[night] *= 0.55
        sub = img[b0:b1, a0:a1]
        sub[inside] = out[inside]
        if rim:
            rho = np.sqrt(rho2)
            limb = inside & (rho > 0.9)
            a = (rho - 0.9) / 0.1 * 0.55
            m = limb & (a > BAYER[b0:b1, a0:a1])
            sub[m] = C('#6fb7ff')
            outer = (rho > 1) & (rho <= 1 + 3.5 / R)
            a2 = (1 - (rho - 1) / (3.5 / R)) * 0.8
            m2 = outer & (a2 > BAYER[b0:b1, a0:a1])
            sub[m2] = C('#3d7fd6')
        return dict(box=(a0, b0, a1, b1), inside=inside, lat=latd, lon=lond, typ=typ, shade=shade)

    @staticmethod
    def project(lat, lon, cx, cy, R, lon0, lat0):
        lat = np.radians(np.asarray(lat, np.float32))
        lon = np.radians(np.asarray(lon, np.float32))
        p0, l0 = math.radians(lat0), math.radians(lon0)
        dl = lon - l0
        x = np.cos(lat) * np.sin(dl)
        y = math.cos(p0) * np.sin(lat) - math.sin(p0) * np.cos(lat) * np.cos(dl)
        vis = (math.sin(p0) * np.sin(lat) + math.cos(p0) * np.cos(lat) * np.cos(dl)) > 0.02
        return cx + x * R, cy - y * R, vis


def stars(img, n, seed, y1=None, cols=('#ffffff', '#c9d6ff', '#8a96c8'), twinkle_t=None):
    H, W = img.shape[:2]
    y1 = H if y1 is None else y1
    rng = np.random.RandomState(seed)
    xs = rng.randint(0, W, n)
    ys = rng.randint(0, y1, n)
    ks = rng.randint(0, len(cols), n)
    ph = rng.rand(n) * 6.28
    for i in range(n):
        if twinkle_t is not None and math.sin(twinkle_t * 2.5 + ph[i]) > 0.85:
            continue
        img[ys[i], xs[i]] = C(cols[ks[i]])
