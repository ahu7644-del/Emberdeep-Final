"""Procedural soundtrack + SFX for the no-wind video (numpy/scipy only)."""
import math

import numpy as np
from scipy import signal

SR = 48000


def n_(sec):
    return max(1, int(round(sec * SR)))


def tt(n):
    return np.arange(n, dtype=np.float64) / SR


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def _sos(kind, f, order=2):
    if kind == 'band':
        lo, hi = f
        return signal.butter(order, [lo / (SR / 2), min(0.99, hi / (SR / 2))], 'band', output='sos')
    return signal.butter(order, min(0.99, f / (SR / 2)), kind, output='sos')


def lp(x, f, order=2):
    return signal.sosfilt(_sos('low', f, order), x)


def hp(x, f, order=2):
    return signal.sosfilt(_sos('high', f, order), x)


def bp(x, lo, hi, order=2):
    return signal.sosfilt(_sos('band', (lo, hi), order), x)


def env_exp(n, decay):
    return np.exp(-tt(n) / decay)


def env_adsr(n, a=0.01, r=0.1):
    e = np.ones(n)
    na, nr = min(n, n_(a)), min(n, n_(r))
    e[:na] = np.linspace(0, 1, na)
    e[n - nr:] *= np.linspace(1, 0, nr)
    return e


def saw(f, n, ph=0.0):
    return 2 * ((f * tt(n) + ph) % 1.0) - 1


def square(f, n, duty=0.5):
    return np.where((f * tt(n)) % 1.0 < duty, 1.0, -1.0)


def sine(f, n, ph=0.0):
    return np.sin(2 * np.pi * f * tt(n) + ph)


def sweep_sine(f0, f1, n, curve='exp'):
    if curve == 'exp':
        f = f0 * (f1 / f0) ** (np.arange(n) / n)
    else:
        f = np.linspace(f0, f1, n)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def noise(n, seed=0):
    return np.random.RandomState(seed).randn(n)


def place(buf, x, t0, gain=1.0):
    i = int(round(t0 * SR))
    if i >= len(buf):
        return
    if i < 0:
        x = x[-i:]
        i = 0
    m = min(len(x), len(buf) - i)
    if buf.ndim == 2 and x.ndim == 1:
        buf[i:i + m] += (x[:m] * gain)[:, None]
    else:
        buf[i:i + m] += x[:m] * gain


def reverb(x, secs=1.8, decay=2.6, mix=0.3, seed=1, stereo=True):
    n = n_(secs)
    out = []
    for ch in range(2 if stereo else 1):
        ir = noise(n, seed + ch) * np.exp(-tt(n) * decay)
        ir = lp(ir, 5000)
        ir /= np.sqrt((ir ** 2).sum())
        wet = signal.fftconvolve(x, ir)
        dry = np.concatenate([x, np.zeros(len(wet) - len(x))])
        out.append(dry * (1 - mix) + wet * mix * 0.8)
    return np.stack(out, 1) if stereo else out[0]


def norm(x, peak=1.0):
    m = np.max(np.abs(x)) + 1e-9
    return x / m * peak


# =========================================================================== SFX
def sfx_wind(dur, seed=3):
    n = n_(dur)
    w = noise(n, seed)
    body = lp(hp(w, 140), 900)
    whistle = bp(noise(n, seed + 1), 620, 760, 2) * 0.6
    gust_ctrl = np.interp(tt(n), np.arange(0, dur + 2, 0.7), np.random.RandomState(seed).rand(int((dur + 2) / 0.7) + 1))
    gust_ctrl = lp(gust_ctrl, 2)
    g = 0.45 + 0.55 * np.clip(gust_ctrl, 0, 1)
    return norm((body + whistle * g) * g, 0.8)


def sfx_stop():
    n = n_(2.2)
    sub = sweep_sine(110, 26, n) * env_exp(n, 0.7)
    crack = lp(noise(n, 9), 1800) * env_exp(n, 0.06)
    return norm(sub * 0.9 + crack * 0.4, 1.0)


def sfx_boom(seed=0):
    n = n_(2.6)
    sub = sweep_sine(70, 32, n) * env_exp(n, 0.9)
    hit = lp(noise(n, seed + 20), 2500) * env_exp(n, 0.08)
    x = norm(sub + hit * 0.5, 1.0)
    return reverb(x, 2.2, 2.2, 0.25, seed)


def sfx_riser(dur=1.4, seed=0):
    n = n_(dur)
    w = noise(n, seed + 30)
    out = np.zeros(n)
    blk = 1024
    zi = None
    for i in range(0, n, blk):
        f = 300 * (16 ** (i / n))
        sos = _sos('band', (f * 0.7, f * 1.4))
        if zi is None:
            zi = signal.sosfilt_zi(sos) * 0
        out[i:i + blk], zi = signal.sosfilt(sos, w[i:i + blk], zi=zi)
    tone = sweep_sine(110, 880, n) * 0.25
    e = (np.arange(n) / n) ** 2.2
    return norm((out * 3 + tone) * e, 0.9)


def sfx_blip(notes=(76, 83), step=0.05, duty=0.25):
    parts = []
    for m in notes:
        k = n_(step)
        parts.append(square(hz(m), k, duty) * env_exp(k, step * 0.8))
    return lp(np.concatenate(parts), 7000) * 0.5


def sfx_tick():
    k = n_(0.05)
    return hp(noise(k, 5), 2000) * env_exp(k, 0.006) + square(1800, k, 0.5) * env_exp(k, 0.01) * 0.3


def sfx_turbine_down(dur=3.2):
    n = n_(dur)
    t = tt(n)
    rate = np.clip(1 - t / (dur * 0.85), 0, 1)
    f = 30 + 90 * rate
    hum = np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.4 * np.sin(4 * np.pi * np.cumsum(f) / SR)
    bpf = 0.6 + 2.4 * rate
    swish = bp(noise(n, 4), 400, 2000) * (0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(bpf) / SR)) ** 4 * rate
    return norm(hum * rate ** 0.5 * 0.6 + swish * 1.4, 0.8)


def sfx_power_down():
    n = n_(1.8)
    t = tt(n)
    f = 120 * (0.2 ** (t / 1.2))
    buzz = lp(np.sign(np.sin(2 * np.pi * np.cumsum(f) / SR)), 900) * np.clip(1 - t / 1.4, 0, 1)
    thunk = sweep_sine(90, 40, n) * env_exp(n, 0.25)
    click = hp(noise(n, 2), 3000) * env_exp(n, 0.004)
    return norm(buzz * 0.4 + thunk + click * 0.5, 0.9)


def sfx_rain(dur, seed=6):
    n = n_(dur)
    r = bp(noise(n, seed), 1200, 9000) * 0.7
    drops = (np.random.RandomState(seed).rand(n) > 0.9993) * 1.0
    drops = hp(lp(drops, 6000), 1500) * 4
    e = env_adsr(n, 0.4, 0.6)
    return norm((r + drops) * e, 0.6)


def sfx_thunder(seed=0):
    n = n_(3.2)
    t = tt(n)
    rumble = lp(noise(n, seed + 40), 220, 4)
    rumble *= (0.6 + 0.4 * np.abs(lp(noise(n, seed + 41), 6)) * 6)
    crack = bp(noise(n, seed + 42), 300, 4000) * env_exp(n, 0.12)
    e = np.minimum(1, t / 0.03) * np.exp(-t / 1.1)
    return norm(rumble * e + crack * 0.6, 0.9)


def sfx_waves(dur, calm_at, seed=7):
    n = n_(dur)
    t = tt(n)
    w = lp(noise(n, seed), 700)
    am = 0.55 + 0.45 * np.sin(2 * np.pi * 0.13 * t) ** 2
    fade = np.clip(1 - (t - calm_at * 0.3) / max(0.1, calm_at), 0, 1)
    return norm(w * am * fade, 0.6)


def sfx_glitch(seed=0):
    n = n_(0.45)
    rng = np.random.RandomState(seed)
    x = noise(n, seed + 50)
    x = np.round(x * 3) / 3
    hold = np.repeat(x[::12], 12)[:n]
    chirp = square(1, n) * 0
    for i in range(6):
        a, b = rng.randint(0, n - 2000), rng.randint(800, 2000)
        chirp[a:a + b] += square(rng.choice([220, 440, 660, 1320]), b, 0.3)
    gate = (np.floor(tt(n) * 40) % 2 == 0) * 1.0
    return norm((hold * 0.6 + chirp * 0.4) * gate * env_adsr(n, 0.005, 0.05), 0.7)


def sfx_type():
    k = n_(0.06)
    return hp(noise(k, 11), 2500) * env_exp(k, 0.008) * 0.8 + sine(140, k) * env_exp(k, 0.02) * 0.4


def sfx_drain():
    return sfx_blip((84, 81, 77, 74, 72, 69, 65, 62), 0.09, 0.5)


def sfx_stamp(seed=0):
    n = n_(0.6)
    sub = sweep_sine(140, 50, n) * env_exp(n, 0.12)
    nz = lp(noise(n, seed + 60), 3000) * env_exp(n, 0.03)
    return norm(sub + nz * 0.6, 0.8)


def sfx_counter(dur):
    n = n_(dur)
    out = np.zeros(n)
    t = 0.0
    k = 0
    while t < dur:
        place(out, sfx_tick() * 0.5, t)
        k += 1
        t += 0.05 + 0.04 * (t / dur)
    return out


def sfx_chime(base=81):
    n = n_(3.0)
    x = np.zeros(n)
    for i, m in enumerate([0, 7, 12, 16, 19]):
        k = n - n_(i * 0.07)
        y = (sine(hz(base + m), k) + 0.3 * sine(hz(base + m) * 2.76, k)) * env_exp(k, 0.9)
        place(x, y, i * 0.07)
    return norm(x, 0.6)


def sfx_fanfare():
    seq = [(74, 0.09), (78, 0.09), (81, 0.09), (86, 0.35)]
    parts = []
    for m, d in seq:
        k = n_(d)
        parts.append((square(hz(m), k, 0.25) * 0.6 + square(hz(m) * 1.003, k, 0.5) * 0.4) * env_adsr(k, 0.005, 0.05))
    return lp(np.concatenate(parts), 6000) * 0.5


SFX = {
    'stop': lambda: sfx_stop(), 'boom': lambda: sfx_boom(), 'hit': lambda: sfx_stamp(1), 'riser': lambda: sfx_riser(),
    'blip': lambda: sfx_blip(), 'tick': lambda: sfx_tick(), 'turbine': lambda: sfx_turbine_down(),
    'powerdown': lambda: sfx_power_down(), 'thunder': lambda: sfx_thunder(), 'thunder2': lambda: sfx_thunder(3),
    'glitch': lambda: sfx_glitch(), 'drain': lambda: sfx_drain(), 'stamp': lambda: sfx_stamp(), 'chime': lambda: sfx_chime(),
    'fanfare': lambda: sfx_fanfare(), 'down': lambda: sfx_blip((79, 74, 67, 62), 0.07, 0.5),
    'up': lambda: sfx_blip((67, 74, 79, 86), 0.06, 0.25),
}


# =========================================================================== music
CHORDS = [  # D minor: i - VI - III - VII
    ([50, 53, 57, 62], 38),
    ([50, 53, 58, 62], 34),
    ([48, 53, 57, 60], 41),
    ([48, 52, 55, 60], 36),
]
LEAD = [[69, None, 72, None, 74, None, 72, 69], [70, None, 69, None, 65, None, 67, None],
        [69, None, 72, None, 77, None, 76, 74], [72, None, None, None, 67, None, None, None]]


def music(D, plan, bpm=96):
    """plan: dict of layer -> list of (t0, t1, gain) windows; returns stereo array."""
    N = n_(D) + n_(3)
    beat = 60.0 / bpm
    bar = beat * 4
    out = {k: np.zeros(N) for k in ['pad', 'bass', 'arp', 'kick', 'snare', 'hat', 'lead', 'airy']}
    nb = int(D / bar) + 2
    for b in range(nb):
        t0 = b * bar
        notes, root = CHORDS[b % 4]
        k = n_(bar + 0.6)
        e = env_adsr(k, 0.25, 0.6)
        pad = np.zeros(k)
        for m in notes:
            for det in (-0.08, 0.08):
                pad += saw(hz(m + det), k, ph=np.random.RandomState(b * 10 + m).rand())
        pad += sine(hz(root + 12), k) * 2
        place(out['pad'], pad * e * 0.12, t0)
        for s in range(8):
            kk = n_(beat / 2)
            bs = (sine(hz(root), kk) + 0.35 * saw(hz(root), kk)) * env_exp(kk, 0.16)
            place(out['bass'], bs * 0.5, t0 + s * beat / 2)
        seq = notes[1:] + [notes[1] + 12, notes[2] + 12, notes[3] + 12, notes[2] + 12]
        for s in range(16):
            kk = n_(beat / 4)
            m = seq[s % len(seq)] + 12
            place(out['arp'], square(hz(m), kk, 0.25) * env_exp(kk, 0.07) * 0.2, t0 + s * beat / 4)
        for s in range(4):
            kk = n_(0.5)
            if s % 2 == 0:
                kick = sweep_sine(150, 42, kk) * env_exp(kk, 0.22)
                place(out['kick'], kick, t0 + s * beat)
            else:
                sn = bp(noise(kk, b * 4 + s), 900, 6000) * env_exp(kk, 0.11) + sine(185, kk) * env_exp(kk, 0.05) * 0.5
                place(out['snare'], sn * 0.6, t0 + s * beat)
        for s in range(8):
            kk = n_(0.08)
            place(out['hat'], hp(noise(kk, b * 8 + s + 999), 7000) * env_exp(kk, 0.02) * (0.5 if s % 2 else 0.3), t0 + s * beat / 2)
        for s, m in enumerate(LEAD[b % 4]):
            if m is None:
                continue
            ln = 1
            while s + ln < 8 and LEAD[b % 4][s + ln] is None:
                ln += 1
            kk = n_(ln * beat / 2)
            vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * tt(kk))
            ph = np.cumsum(hz(m) * vib) / SR
            ld = (2 * (ph % 1) - 1) * 0.5 + np.where(ph % 1 < 0.5, 1, -1) * 0.5
            place(out['lead'], ld * env_adsr(kk, 0.02, 0.12) * 0.18, t0 + s * beat / 2)
    # airy intro (major-ish shimmer)
    for s in range(int(6 / (beat / 2))):
        kk = n_(1.2)
        m = [74, 78, 81, 85, 86, 81, 78, 76][s % 8]
        place(out['airy'], (sine(hz(m), kk) + 0.25 * sine(hz(m) * 2, kk)) * env_exp(kk, 0.5) * 0.25, s * beat / 2)
    out['pad'] = lp(out['pad'], 1400)
    out['bass'] = lp(out['bass'], 500)
    out['arp'] = lp(out['arp'], 5000)
    out['lead'] = lp(out['lead'], 4200)
    tl = tt(N)
    mix = np.zeros((N, 2))
    pan = {'pad': 0.0, 'bass': 0.0, 'arp': 0.35, 'kick': 0.0, 'snare': -0.1, 'hat': 0.25, 'lead': -0.2, 'airy': 0.0}
    for name, sig in out.items():
        g = np.zeros(N)
        for (a, b, gain) in plan.get(name, []):
            g = np.maximum(g, gain * np.clip((tl - a) / 0.4, 0, 1) * np.clip((b - tl) / 0.5, 0, 1))
        s = sig * g
        p = pan[name]
        mix[:, 0] += s * (1 - p) * 0.5 * 2 ** 0.5
        mix[:, 1] += s * (1 + p) * 0.5 * 2 ** 0.5
    # stereo widening of the pad with a short Haas delay
    d = n_(0.012)
    padw = out['pad'] * 0
    for (a, b, gain) in plan.get('pad', []):
        padw += out['pad'] * gain * ((tl > a) & (tl < b))
    mix[d:, 1] += padw[:-d] * 0.25
    return mix, out


def tape_stop(x, t_stop, dur=0.7):
    """Slow a mono signal to a halt from t_stop (returns new array)."""
    i0 = n_(t_stop)
    k = n_(dur)
    rate = np.linspace(1, 0, k) ** 1.5
    pos = i0 + np.cumsum(rate)
    tail = np.interp(pos, np.arange(len(x)), x)
    y = x.copy()
    y[i0:i0 + k] = tail[:max(0, min(k, len(y) - i0))]
    y[i0 + k:] = 0
    return y
