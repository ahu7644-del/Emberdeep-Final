#!/usr/bin/env python3
""""Whaddup My Dawg" - an original pixel-art TikTok skit.

Biscuit, a chill dog in shades and a gold chain, greets EVERYTHING as his dawg:
the mailman, a squirrel, a cat, his own reflection... with one exception.
And then he meets a hot dog.

Reuses the pixel toolkit / audio helpers / text sprites from ../no-wind.

    python make_dawg.py              # -> out/whaddup_my_dawg.mp4
    python make_dawg.py --preview 1  # contact sheet -> build/preview.png
"""
import argparse
import bisect
import functools
import hashlib
import json
import math
import os
import re
import subprocess
import sys

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'no-wind'))
import audio as au          # noqa: E402
import make_video as mv     # noqa: E402  (text sprites, compositing, word timing)
import pixelkit as pk       # noqa: E402
from pixelkit import C, LH, LW, XX, YY, BAYER, ease_out, ramp, smooth  # noqa: E402

BUILD = os.path.join(HERE, 'build')
OUT = os.path.join(HERE, 'out')
FPS = 30
SC = 4
W, H = LW * SC, LH * SC
SR = au.SR
BPM = 86
TAU = 2 * math.pi

# =========================================================================== cast + script
SPK = {
    'dog': dict(voice='am_michael', speed=1.0, pitch=0.9, color='#ffe14d', name='BISCUIT'),
    'mail': dict(voice='am_adam', speed=1.25, pitch=1.08, color='#7fc8ff', name='MAILMAN'),
    'sq': dict(voice='am_puck', speed=1.05, pitch=1.5, color='#ffb36a', name='SQUIRREL'),
    'cat': dict(voice='bm_george', speed=0.9, pitch=1.0, color='#ff9ad5', name='CAT'),
}
WH = '[Whaddup|Wuddup]'


def L(spk, text, shot, gap=0.2, sfx=(), **kw):
    d = dict(spk=spk, text=text, shot=shot, gap=gap, sfx=list(sfx), greet=False, echo=False, speed=None, cut=0.0)
    d.update(kw)
    return d


LINES = [
    L('dog', WH + " my *dawg.*", 'closeup:start', 0.45, greet=True),
    L("dog", WH + " my *dawg!*", "mail", 0.1, greet=True),
    L('mail', "Not today, Biscuit!", 'mail', 0.75, sfx=[('whoosh', 'end+0.02', 0.7)]),
    L('dog', WH + " my *dawg.*", 'squirrel', 0.12, greet=True),
    L('sq', "Bro. I'm *literally* a squirrel.", 'squirrel', 0.95, sfx=[('slide', 'end+0.05', 0.45), ('bonk', 'end+0.5', 0.9)]),
    L('dog', "Love you too, *dawg.*", 'squirrel', 0.3),
    L('dog', WH + " my *dawg.*", 'cat', 0.18, greet=True),
    L('cat', "I am a *cat.*", 'cat', 0.3),
    L('dog', "Dawg is a *state of mind.*", 'cat', 0.3),
    L('cat', "*Ugh.*", 'cat', 0.45),
    L('dog', WH + " my-", 'robot', 1.15, cut=0.08, sfx=[('scratch', 'end', 0.9), ('beep_q', 'end+0.55', 0.5)]),
    L('dog', "Nah. Not you. You know *what you did.*", 'robot', 0.7,
      sfx=[('growl', 'start-0.15', 0.8), ('beep_x', 'end+0.05', 0.55)]),
    L('dog', WH + " my *dawg!*", 'puddle', 0.3, greet=True, echo=True),
    L('dog', "Finally. Someone who *gets it.*", 'puddle', 0.45, sfx=[('up', 'end-0.3', 0.4)]),
    L('dog', "[Whaddup...|Wuddup...] my *dawg.*", 'hotdog', 1.45, greet=True, speed=0.78,
      sfx=[('choir', 'start-0.7', 0.6), ('chomp', 'end+0.6', 1.0)]),
    L('dog', "You will be *missed.*", 'hotdog', 0.6, sfx=[('violin', 'start-0.15', 0.55), ('burp', 'end+0.3', 0.85)]),
]
END_HOLD = 1.9
CAP_Y = 1384


# =========================================================================== TTS + timing
_kok = None


def kokoro():
    global _kok
    if _kok is None:
        from kokoro_onnx import Kokoro
        d = os.environ.get('KOKORO_DIR', os.path.join(HERE, '..', 'no-wind', 'assets', 'kokoro'))
        _kok = Kokoro(os.path.join(d, 'kokoro-v1.0.onnx'), os.path.join(d, 'voices-v1.0.bin'))
    return _kok


def tts(text, voice, speed):
    os.makedirs(os.path.join(BUILD, 'tts'), exist_ok=True)
    h = hashlib.sha1(('%s|%s|%.3f' % (voice, text, speed)).encode()).hexdigest()[:16]
    p = os.path.join(BUILD, 'tts', h + '.wav')
    if not os.path.exists(p):
        y, sr = kokoro().create(text, voice=voice, speed=speed, lang='en-us')
        sf.write(p, y, sr)
    y, sr = sf.read(p, dtype='float64')
    a = np.abs(y)
    idx = np.where(a > a.max() * 0.02)[0]
    y = y[max(0, idx[0] - int(sr * 0.02)):min(len(y), idx[-1] + int(sr * 0.05))]
    return y, sr


def pitch_shift(y, sr, p):
    """Resample so that pitch (and tempo) scale by p; return at SR."""
    y48 = signal.resample_poly(y, SR, sr)
    if abs(p - 1) < 1e-3:
        return y48
    up, down = 1000, int(round(1000 * p))
    return signal.resample_poly(y48, up, down)


class Line:
    pass


class Shot:
    pass


def build():
    lines = []
    t = 0.15
    for i, spec in enumerate(LINES):
        sp = SPK[spec['spk']]
        spoken, toks = mv.parse_line(spec['text'])
        y, sr = tts(spoken, sp['voice'], spec['speed'] or sp['speed'])
        y = pitch_shift(y, sr, sp['pitch'])
        if spec['cut']:
            y = y[:len(y) - int(spec['cut'] * SR)]
            y[-int(0.01 * SR):] *= np.linspace(1, 0, int(0.01 * SR))
        ln = Line()
        ln.__dict__.update(spec)
        ln.i = i
        ln.toks = toks
        ln.audio = y
        ln.start = t
        ln.dur = len(y) / SR
        ln.end = t + ln.dur
        for tk, r in zip(toks, mv.time_words(toks, y, SR)):
            tk['t'] = t + r
        hop = SR // 100
        n = len(y) // hop
        env = np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1))
        ln.env = np.clip(env / (np.percentile(env, 90) + 1e-9), 0, 1.4)
        lines.append(ln)
        t = ln.end + spec['gap']
    shots = []
    for ln in lines:
        if not shots or shots[-1].key != ln.shot:
            sh = Shot()
            sh.key = ln.shot
            sh.kind, _, sh.mode = ln.shot.partition(':')
            sh.lines = [ln]
            sh.start = 0.0 if not shots else ln.start - 0.12
            shots.append(sh)
        else:
            shots[-1].lines.append(ln)
    end = Shot()
    end.key, end.kind, end.mode, end.lines, end.start = 'closeup:end', 'closeup', 'end', [], t - 0.05
    shots.append(end)
    total = t + END_HOLD
    for i, sh in enumerate(shots):
        sh.end = shots[i + 1].start if i + 1 < len(shots) else total
        sh.dur = sh.end - sh.start
    return lines, shots, total


def when(ln, spec):
    m = re.match(r'^(start|end|w:[a-z0-9]+)([+-][0-9.]+)?$', spec)
    base, off = m.group(1), float(m.group(2) or 0)
    if base == 'start':
        return ln.start + off
    if base == 'end':
        return ln.end + off
    w = base[2:]
    for tk in ln.toks:
        if mv.norm_word(tk['disp']) == w or mv.norm_word(tk['spoken']) == w:
            return tk['t'] + off
    raise KeyError(w)


# =========================================================================== characters
FUR, FUR_D, FUR_L, EAR, NOSE = '#d9954a', '#a8692e', '#f6d29b', '#7a4a22', '#1a1a1a'
GOLD, GOLD_L, GOLD_D = '#ffd23f', '#fff3a0', '#c99a1a'


def _local(w, h):
    return pk.keycanvas(h, w)


def dog_side(img, x, feet, s, t, walk=0.0, mouth=0.0, facing=1, wag=1.0, glasses='on', mood='chill',
             look_down=0.0, lunge=0.0, alpha=1.0):
    """Side-view Biscuit. (x, feet) = body centre x, ground y."""
    Wc, Hc = int(92 * s), int(70 * s)
    cx, cy = 40 * s, 40 * s
    c = _local(Wc, Hc)

    def P(u, v):
        return (cx + u * s, cy + v * s)
    ph = t * 11
    # tail
    ta = -2.25 + 0.55 * math.sin(t * 17) * wag
    pts = [P(-16, -4)]
    for j in range(1, 5):
        a = ta + j * 0.18
        u, v = pts[-1]
        pts.append((u + 3 * s * math.sin(a), v + 3 * s * math.cos(a) * -1 * -1))
    pk.polyline(c, pts, FUR_D, max(2, int(3 * s)))
    # legs (far side darker first)
    for k, (hu, off, col) in enumerate([(-8, math.pi, FUR_D), (14, 0.0, FUR_D), (-12, 0.0, FUR), (10, math.pi, FUR)]):
        a = walk * 0.6 * math.sin(ph + off)
        hx, hy = P(hu, 5)
        fx, fy = hx + 11 * s * math.sin(a), hy + 11 * s * math.cos(a)
        pk.line(c, hx, hy, fx, fy, col, max(2, int(3 * s)))
        pk.rect(c, fx - 1.5 * s, fy - 1 * s, fx + 2.5 * s, fy + 0.6 * s, FUR_L if k > 1 else FUR_D)
    # body
    bx, by = P(0, 0)
    pk.ellipse(c, bx, by, 17 * s, 9 * s, FUR)
    pk.ellipse(c, bx - 1 * s, by - 3 * s, 13 * s, 4 * s, '#e6a85e')
    pk.ellipse(c, bx + 3 * s, by + 4.5 * s, 11 * s, 3.5 * s, FUR_L)
    # head position
    hu, hv = 18 + lunge * 6, -11 + look_down * 6
    hx, hy = P(hu, hv)
    pk.ellipse(c, *P(13 + lunge * 3, -5 + look_down * 3), 6 * s, 7 * s, FUR)   # neck
    # chain
    for j in range(9):
        a = math.radians(20 + j * 17)
        u, v = P(13 + lunge * 3 + 6.2 * math.cos(a), -4 + look_down * 3 + 5 * math.sin(a))
        pk.disk(c, u, v, 0.9 * s, GOLD if j % 2 else GOLD_D)
    pu, pv = P(13 + lunge * 3, 2.5 + look_down * 3)
    pk.rect(c, pu - 1.6 * s, pv - 0.8 * s, pu + 1.6 * s, pv + 0.8 * s, GOLD)
    pk.disk(c, pu - 1.8 * s, pv, 0.9 * s, GOLD)
    pk.disk(c, pu + 1.8 * s, pv, 0.9 * s, GOLD)
    pk.rect(c, pu - 1 * s, pv - 0.8 * s, pu + 0.5 * s, pv - 0.3 * s, GOLD_L)
    # head
    pk.disk(c, hx, hy, 8 * s, FUR)
    pk.disk(c, hx - 1.5 * s, hy - 2.5 * s, 4.5 * s, '#e6a85e')
    # snout
    sx, sy = hx + 7 * s, hy + 2.5 * s
    pk.ellipse(c, sx, sy, 6.2 * s, 4 * s, FUR_L)
    pk.disk(c, sx + 5.5 * s, sy - 2 * s, 1.8 * s, NOSE)
    pk.rect(c, sx + 5 * s, sy - 3.3 * s, sx + 5.8 * s, sy - 2.6 * s, '#777777')
    m = max(0.0, min(1.0, mouth))
    if m > 0.12:
        pts = [(sx - 2 * s, sy + 2.4 * s), (sx + 5.5 * s, sy + 2.0 * s), (sx + 4 * s, sy + (2.4 + 4 * m) * s),
               (sx - 1 * s, sy + (2.4 + 3.2 * m) * s)]
        pk.poly(c, pts, '#5a1e24')
        pk.ellipse(c, sx + 1.5 * s, sy + (2.3 + 3 * m) * s, 2.4 * s, 1.1 * s, '#ff7a8a')
    else:
        pk.line(c, sx - 2 * s, sy + 2.4 * s, sx + 4.5 * s, sy + 2.2 * s, '#5a3a22')
    # ear
    ex, ey = hx - 3 * s, hy - 6 * s
    flap = 0.6 * math.sin(t * 9) * walk
    pk.poly(c, [(ex - 1 * s, ey), (ex + 4 * s, ey - 0.5 * s), (ex + 2 * s + flap * s, ey + 9 * s), (ex - 2.5 * s + flap * s, ey + 8 * s)], EAR)
    # eyes / glasses
    gx, gy = hx + 2.5 * s, hy - 2.5 * s
    if glasses == 'on':
        pk.rect(c, gx - 0.5 * s, gy - 2.2 * s, gx + 8 * s, gy + 1.6 * s, '#111111')
        pk.line(c, gx - 0.5 * s, gy - 1.5 * s, hx - 6 * s, gy - 2.5 * s, '#111111')
        sh = (t * 0.7) % 1.0
        if sh < 0.25:
            u = gx + sh * 4 * 8 * s
            pk.line(c, u, gy - 2 * s, u + 1.5 * s, gy + 1.4 * s, '#ffffff')
        else:
            pk.rect(c, gx + 1 * s, gy - 1.6 * s, gx + 2.2 * s, gy - 0.9 * s, '#cfd8ff')
    else:
        pk.disk(c, gx + 3 * s, gy, 2.1 * s, '#ffffff')
        pk.disk(c, gx + 3.6 * s, gy + 0.2 * s, 1.1 * s, '#111111')
        if mood == 'angry':
            pk.line(c, gx + 0.5 * s, gy - 3.6 * s, gx + 5.5 * s, gy - 1.8 * s, '#3a2410', max(1, int(1.2 * s)))
        if glasses == 'up':
            pk.rect(c, hx - 4 * s, hy - 9.5 * s, hx + 6 * s, hy - 7 * s, '#111111')
        elif glasses == 'down':
            pk.rect(c, gx + 1 * s, gy + 1.6 * s, gx + 9 * s, gy + 4.2 * s, '#111111')
    if mood == 'angry':
        for j in range(3):
            pk.line(c, sx - 1 * s + j * 2 * s, sy + 2.2 * s, sx - 0.2 * s + j * 2 * s, sy + 3.6 * s, '#ffffff')
    m_ = pk.keymask(c)
    if facing < 0:
        c, m_ = c[:, ::-1], m_[:, ::-1]
        ox = x - (Wc - cx)
    else:
        ox = x - cx
    pk.blit(img, (c, m_), ox, feet - (cy + 16 * s), alpha=alpha)
    return (x + facing * (hu + 9) * s, feet - (cy + 16 * s) + hy)  # approx. mouth position


def dog_front(img, cx, cy, S, t, mouth=0.0, glasses='on', tint=None, bob=0.0):
    """Front-facing head + shoulders. S = head radius in px."""
    cy = cy + bob
    def P(u, v):
        return (cx + u * S, cy + v * S)
    pk.ellipse(img, *P(0, 1.65), 1.3 * S, 0.85 * S, FUR)
    pk.ellipse(img, *P(0, 1.6), 0.62 * S, 0.6 * S, FUR_L)
    for side in (-1, 1):
        pk.poly(img, [P(side * 0.7, -0.7), P(side * 1.28, -0.38), P(side * 1.22, 0.55), P(side * 0.86, 0.42)], EAR)
    pk.ellipse(img, *P(0, 0), 1.0 * S, 0.92 * S, FUR)
    pk.ellipse(img, *P(-0.2, -0.38), 0.55 * S, 0.32 * S, '#e6a85e')
    pk.ellipse(img, *P(0, 0.44), 0.52 * S, 0.37 * S, FUR_L)
    # chain + bone pendant
    for j in range(17):
        a = math.radians(15 + j * 9.4)
        u, v = P(0.95 * math.cos(a), 0.92 + 0.32 * math.sin(a))
        pk.disk(img, u, v, 0.055 * S, GOLD if j % 2 else GOLD_D)
    bx, by = P(0, 1.3)
    pk.rect(img, bx - 0.16 * S, by - 0.06 * S, bx + 0.16 * S, by + 0.06 * S, GOLD)
    for sx_ in (-1, 1):
        for sy_ in (-1, 1):
            pk.disk(img, bx + sx_ * 0.17 * S, by + sy_ * 0.06 * S, 0.075 * S, GOLD)
    pk.rect(img, bx - 0.12 * S, by - 0.05 * S, bx + 0.05 * S, by - 0.02 * S, GOLD_L)
    # nose + mouth
    nx, ny = P(0, 0.26)
    pk.ellipse(img, nx, ny, 0.17 * S, 0.12 * S, NOSE)
    pk.rect(img, nx - 0.08 * S, ny - 0.08 * S, nx - 0.02 * S, ny - 0.04 * S, '#888888')
    m = max(0.0, min(1.0, mouth))
    if m > 0.12:
        mx, my = P(0, 0.55)
        pk.ellipse(img, mx, my, 0.24 * S, (0.05 + 0.2 * m) * S, '#5a1e24')
        pk.ellipse(img, mx, my + 0.1 * m * S, 0.15 * S, 0.08 * S, '#ff7a8a')
    else:
        pk.line(img, *P(0, 0.37), *P(0, 0.47), '#5a3a22')
        pk.line(img, *P(0, 0.47), *P(-0.2, 0.52), '#5a3a22')
        pk.line(img, *P(0, 0.47), *P(0.2, 0.52), '#5a3a22')
    # eyes / glasses
    if glasses == 'on':
        for side in (-1, 1):
            gx, gy = P(side * 0.42, -0.16)
            pk.ellipse(img, gx, gy, 0.33 * S, 0.19 * S, '#111111')
            pk.rect(img, gx - 0.33 * S, gy - 0.19 * S, gx + 0.33 * S, gy - 0.05 * S, '#111111')
            sh = (t * 0.6 + (0.1 if side > 0 else 0)) % 1.0
            if sh < 0.3:
                u = gx - 0.3 * S + sh / 0.3 * 0.6 * S
                pk.line(img, u, gy - 0.15 * S, u + 0.1 * S, gy + 0.12 * S, '#ffffff', max(1, int(S / 25)))
            pk.rect(img, gx - 0.2 * S, gy - 0.12 * S, gx - 0.12 * S, gy - 0.07 * S, '#cfd8ff')
        pk.rect(img, *P(-0.1, -0.22), *P(0.1, -0.17), '#111111')
    else:
        for side in (-1, 1):
            gx, gy = P(side * 0.4, -0.16)
            pk.disk(img, gx, gy, 0.15 * S, '#ffffff')
            pk.disk(img, gx, gy + 0.02 * S, 0.085 * S, '#111111')
    if tint is not None:
        pass


def mailman(img, x, feet, s, t, run=0.0, mouth=0.0, facing=-1, scared=0.0):
    Wc, Hc = int(30 * s), int(48 * s)
    cx, cy = 15 * s, 44 * s
    c = _local(Wc, Hc)

    def P(u, v):
        return (cx + u * s, cy + v * s)
    ph = t * 16
    lean = run * 3
    for k, (hu, off, col) in enumerate([(-2, math.pi, '#1f2a4f'), (2, 0.0, '#2b3a67')]):
        a = run * 0.8 * math.sin(ph + off)
        hx, hy = P(hu + lean * 0.3, -14)
        fx, fy = hx + 13 * s * math.sin(a), hy + 13 * s * math.cos(a)
        pk.line(c, hx, hy, fx, fy, col, max(2, int(3 * s)))
        pk.rect(c, fx - 1 * s, fy - 1 * s, fx + 3 * s, fy + 0.5 * s, '#111111')
    pk.rect(c, *P(-5 + lean, -28), *P(5 + lean, -13), '#8ec5ff')
    pk.rect(c, *P(-5 + lean, -28), *P(5 + lean, -26), '#6aa6e6')
    pk.line(c, *P(-4 + lean, -27), *P(5 + lean, -16), '#6a4220', max(1, int(s)))
    pk.rect(c, *P(3 + lean, -20), *P(9 + lean, -13), '#8a5a2b')
    pk.rect(c, *P(3 + lean, -20), *P(9 + lean, -18.5), '#a8743a')
    for k, off in enumerate((0.0, math.pi)):
        a = (run * 1.0 + 0.15) * math.sin(ph + off + 0.5) + (scared * -2.4 if k == 0 else 0)
        sx_, sy_ = P(-0.5 + lean, -26)
        pk.line(c, sx_, sy_, sx_ + 10 * s * math.sin(a), sy_ + 10 * s * math.cos(a), '#7fb8f0' if k else '#8ec5ff', max(2, int(2.2 * s)))
    hx, hy = P(0.5 + lean * 1.2, -33)
    pk.disk(c, hx, hy, 5.5 * s, '#c68642')
    pk.rect(c, hx - 6 * s, hy - 7 * s, hx + 5 * s, hy - 3 * s, '#2b3a67')
    pk.rect(c, hx + 3 * s, hy - 4 * s, hx + 8 * s, hy - 3 * s, '#1f2a4f')
    pk.rect(c, hx - 1 * s, hy - 6.5 * s, hx + 1 * s, hy - 5 * s, GOLD)
    if scared > 0.3:
        pk.disk(c, hx + 2.6 * s, hy - 0.6 * s, 1.5 * s, '#ffffff')
        pk.disk(c, hx + 3 * s, hy - 0.4 * s, 0.7 * s, '#111111')
        pk.rect(c, hx - 4.5 * s, hy - 2 * s, hx - 3.5 * s, hy, '#9fd8ff')
    else:
        pk.rect(c, hx + 2 * s, hy - 1 * s, hx + 3.2 * s, hy + 0.2 * s, '#111111')
    if mouth > 0.12:
        pk.ellipse(c, hx + 3 * s, hy + 2.6 * s, 1.4 * s, (0.6 + 1.4 * mouth) * s, '#4a1a1a')
    else:
        pk.line(c, hx + 2 * s, hy + 2.6 * s, hx + 4 * s, hy + 2.6 * s, '#4a2a1a')
    m_ = pk.keymask(c)
    if facing < 0:
        c, m_ = c[:, ::-1], m_[:, ::-1]
        ox = x - (Wc - cx)
    else:
        ox = x - cx
    pk.blit(img, (c, m_), ox, feet - cy)


def squirrel(img, x, feet, s, t, mouth=0.0, arm=0.0, facing=-1, acorn=True):
    Wc, Hc = int(26 * s), int(28 * s)
    cx, cy = 13 * s, 25 * s
    c = _local(Wc, Hc)

    def P(u, v):
        return (cx + u * s, cy + v * s)
    sw = 0.6 * math.sin(t * 5)
    for j in range(9):
        a = j / 8
        u, v = -4 - 5 * math.sin(a * 2.6) - sw * a, -1 - 16 * a
        pk.disk(c, *P(u, v), (2.6 + 1.6 * math.sin(a * 3)) * s, '#b08a66')
    for j in range(9):
        a = j / 8
        u, v = -4 - 5 * math.sin(a * 2.6) - sw * a, -1 - 16 * a
        pk.disk(c, *P(u + 0.4, v + 0.3), (1.8 + 1.4 * math.sin(a * 3)) * s, '#8a6a4a')
    pk.ellipse(c, *P(0, -5), 4.2 * s, 5.2 * s, '#9a7654')
    pk.ellipse(c, *P(1.2, -4), 2.2 * s, 3.6 * s, '#e8d2b0')
    hx, hy = P(2, -12)
    pk.disk(c, hx, hy, 3.6 * s, '#9a7654')
    pk.poly(c, [(hx - 1.5 * s, hy - 2 * s), (hx - 0.5 * s, hy - 5.5 * s), (hx + 1 * s, hy - 2.5 * s)], '#8a6a4a')
    pk.disk(c, hx + 1.3 * s, hy - 0.6 * s, 0.9 * s, '#111111')
    pk.rect(c, hx + 3.2 * s, hy + 0.3 * s, hx + 4 * s, hy + 1 * s, '#3a2410')
    if mouth > 0.12:
        pk.rect(c, hx + 1.8 * s, hy + 1.6 * s, hx + 3.6 * s, hy + (1.8 + 1.4 * mouth) * s, '#4a1a1a')
    pk.rect(c, hx + 2 * s, hy + 1.6 * s, hx + 3 * s, hy + 2.4 * s, '#ffffff')
    ax, ay = P(3, -7 - arm * 7)
    pk.line(c, *P(1, -7), ax, ay, '#8a6a4a', max(1, int(1.5 * s)))
    if acorn:
        pk.ellipse(c, ax + 1 * s, ay, 1.6 * s, 2 * s, '#a0662a')
        pk.rect(c, ax - 0.8 * s, ay - 2.4 * s, ax + 2.8 * s, ay - 1 * s, '#5a3a1a')
    for u in (-2, 2):
        pk.rect(c, *P(u - 1, -1), *P(u + 2, 0), '#8a6a4a')
    m_ = pk.keymask(c)
    if facing < 0:
        c, m_ = c[:, ::-1], m_[:, ::-1]
        ox = x - (Wc - cx)
    else:
        ox = x - cx
    pk.blit(img, (c, m_), ox, feet - cy)


def cat(img, x, feet, s, t, mouth=0.0, facing=-1, turned=False):
    Wc, Hc = int(30 * s), int(30 * s)
    cx, cy = 15 * s, 27 * s
    c = _local(Wc, Hc)

    def P(u, v):
        return (cx + u * s, cy + v * s)
    base, dark, light = '#6f7380', '#4f5360', '#9ea2ae'
    tail_a = 0.5 * math.sin(t * 2.2)
    pts = [P(5, -1)] + [P(5 + 4 * j + tail_a * j * j * 0.5, -1 - j * j * 0.9) for j in range(1, 5)]
    pk.polyline(c, pts, dark, max(2, int(2.4 * s)))
    pk.ellipse(c, *P(0, -7), 6.5 * s, 7.2 * s, base)
    for j in range(3):
        pk.line(c, *P(-5 + j * 2, -12), *P(-3.5 + j * 2, -7), dark, max(1, int(s)))
    pk.ellipse(c, *P(-2.5, -5), 2.5 * s, 4.5 * s, light)
    pk.ellipse(c, *P(-4, -0.8), 2 * s, 1.2 * s, light)
    hx, hy = P(-1.5, -16)
    pk.disk(c, hx, hy, 5.2 * s, base)
    for side in (-1, 1):
        ex = hx + side * 3 * s
        pk.poly(c, [(ex - 2 * s, hy - 3 * s), (ex + side * 1.2 * s, hy - 8 * s), (ex + 2 * s, hy - 3 * s)], base)
        pk.poly(c, [(ex - 1 * s, hy - 3.5 * s), (ex + side * 0.9 * s, hy - 6.5 * s), (ex + 1 * s, hy - 3.5 * s)], '#e8a0b0')
    if not turned:
        for side in (-1, 1):
            ex = hx + side * 2.2 * s
            pk.rect(c, ex - 1.4 * s, hy - 1.2 * s, ex + 1.4 * s, hy + 0.4 * s, '#e8d24a')
            pk.rect(c, ex - 1.6 * s, hy - 1.8 * s, ex + 1.6 * s, hy - 0.6 * s, base)
            pk.rect(c, ex - 0.3 * s, hy - 0.6 * s, ex + 0.4 * s, hy + 0.4 * s, '#111111')
        pk.rect(c, hx - 0.6 * s, hy + 1.2 * s, hx + 0.6 * s, hy + 1.8 * s, '#e8a0b0')
        if mouth > 0.12:
            pk.ellipse(c, hx, hy + 3 * s, 1.2 * s, (0.4 + 1.2 * mouth) * s, '#4a1a1a')
        for side in (-1, 1):
            pk.line(c, hx + side * 2 * s, hy + 2 * s, hx + side * 6 * s, hy + 1.4 * s, '#d8d8d8')
    m_ = pk.keymask(c)
    if facing > 0:
        c, m_ = c[:, ::-1], m_[:, ::-1]
        ox = x - (Wc - cx)
    else:
        ox = x - cx
    pk.blit(img, (c, m_), ox, feet - cy)


def robot(img, x, feet, s, t, face='eyes', flash=0.0):
    def P(u, v):
        return (x + u * s, feet + v * s)
    for u in (-8, -2, 4, 9):
        pk.disk(img, *P(u, -2), 2.2 * s, '#1a1a1a')
        pk.disk(img, *P(u, -2), 0.8 * s, '#777777')
    pk.rect(img, *P(-11, -17), *P(12, -4), '#e8ecf0')
    pk.rect(img, *P(-11, -9), *P(12, -7), '#3a3f4a')
    pk.rect(img, *P(-11, -17), *P(12, -15.5), '#ffffff')
    pk.rect(img, *P(-11, -6), *P(12, -4), '#b9c0c8')
    pk.line(img, *P(8, -17), *P(8, -34), '#555555')
    pk.poly(img, [P(8, -34), P(8.6, -34), P(9.4, -26), P(7.6, -27)], '#ff7a2a')   # limp flag (no wind!)
    pk.rect(img, *P(-10, -15), *P(-2, -10), '#111a22')
    ec = '#ff3b3b' if flash > 0 else '#4fe0ff'
    if face == 'eyes':
        pk.rect(img, *P(-9, -14), *P(-7.5, -11.5), ec)
        pk.rect(img, *P(-5, -14), *P(-3.5, -11.5), ec)
    elif face == '?':
        pk.rect(img, *P(-9, -13.5), *P(-7.5, -12), '#ffe14d')
        pk.rect(img, *P(-5.2, -14.5), *P(-3.2, -11.5), '#ffe14d')
    elif face == '!':
        pk.line(img, *P(-9.5, -14.5), *P(-7, -12.5), '#ff3b3b', max(1, int(s)))
        pk.line(img, *P(-3, -14.5), *P(-5.5, -12.5), '#ff3b3b', max(1, int(s)))


def hotdog(img, x, y, s, alpha=1.0, halo=False, wing=0.0):
    if halo:
        pk.ellipse(img, x, y - 7 * s, 5 * s, 1.5 * s, '#fff07a', alpha=alpha, ring=max(1, int(0.8 * s)))
        for side in (-1, 1):
            wy = y - 3 * s - wing * 2 * s
            pk.poly(img, [(x + side * 4 * s, y - 2 * s), (x + side * 11 * s, wy - 4 * s), (x + side * 9 * s, wy + 1 * s)], '#ffffff', alpha=alpha)
    pk.ellipse(img, x, y + 0.5 * s, 9 * s, 3.2 * s, '#e8b46a', alpha=alpha)
    pk.ellipse(img, x, y - 0.6 * s, 11 * s, 1.9 * s, '#c0503a', alpha=alpha)
    pk.rect(img, x - 8 * s, y - 1.6 * s, x + 6 * s, y - 1.1 * s, '#e06a4a', alpha=alpha)
    for j in range(7):
        u = x - 7 * s + j * 2.2 * s
        pk.line(img, u, y - 1.2 * s + (j % 2) * 1.1 * s, u + 2.2 * s, y - 1.2 * s + ((j + 1) % 2) * 1.1 * s, '#ffe14d', 1, alpha)
    pk.ellipse(img, x, y + 2.2 * s, 8.5 * s, 1.4 * s, '#c98a46', alpha=alpha)


# =========================================================================== backgrounds
GROUND = 300
DS = 2.3   # dog scale on the street


@functools.lru_cache(None)
def street_bg(seed, golden=False):
    w = LW + 120
    img = pk.canvas(w=w)
    rng = np.random.RandomState(seed)
    if golden:
        pk.vgrad(img, 0, 255, ['#ff9a3c', '#ffc46b', '#ffe3a3', '#fff4d6'])
    else:
        pk.vgrad(img, 0, 255, ['#3d8fe0', '#6fb3f2', '#a9d6fb', '#d8eefc'])
    pk.glow(img, 330, 40, 40, '#fff6c8', 0.8, 1.5)
    pk.disk(img, 330, 40, 12, '#fffbe6')
    for j in range(4):
        pk.blit(img, pk.cloud_sprite(40 + rng.randint(0, 40), 12 + rng.randint(0, 8), 40 + seed * 5 + j), rng.randint(-20, w - 40), 30 + rng.randint(0, 90))
    x = -rng.randint(0, 30)
    walls = ['#f2c4a0', '#a8d0e6', '#c8e6a0', '#f5e1a4', '#e6b8d8', '#f0f0e6']
    roofs = ['#c0504d', '#5b6ea8', '#8a5a44', '#4a7a5a', '#7a4a6a']
    while x < w:
        hw = rng.randint(70, 96)
        hh = rng.randint(58, 84)
        base = 240
        wc = walls[rng.randint(len(walls))]
        rc = roofs[rng.randint(len(roofs))]
        pk.rect(img, x + hw * 0.7, base - hh - 30, x + hw * 0.7 + 8, base - hh, '#8a5a44')
        pk.poly(img, [(x - 4, base - hh), (x + hw / 2, base - hh - 30), (x + hw + 4, base - hh)], rc)
        pk.line(img, x - 4, base - hh, x + hw / 2, base - hh - 30, mixhex(rc, '#ffffff', 0.3))
        pk.rect(img, x, base - hh, x + hw, base, wc)
        pk.rect(img, x + hw - 5, base - hh, x + hw, base, mixhex(wc, '#000000', 0.12))
        for wx in (x + 10, x + hw - 30):
            pk.rect(img, wx - 1, base - hh + 12, wx + 19, base - hh + 34, '#ffffff')
            pk.rect(img, wx + 1, base - hh + 14, wx + 17, base - hh + 32, '#7fb8e6')
            pk.rect(img, wx + 2, base - hh + 15, wx + 6, base - hh + 31, '#bfe0fa')
            pk.line(img, wx + 9, base - hh + 14, wx + 9, base - hh + 32, '#ffffff')
        dx = x + hw // 2 - 7
        pk.rect(img, dx, base - 30, dx + 14, base, '#8a4a3a')
        pk.rect(img, dx + 10, base - 16, dx + 12, base - 14, GOLD)
        x += hw + rng.randint(4, 14)
        if rng.rand() < 0.7 and x < w:
            tx = x - 6
            pk.rect(img, tx - 2, base - 30, tx + 2, base, '#6a4a2a')
            for (u, v, r, cc) in [(0, -40, 14, '#3f8a3a'), (-8, -32, 10, '#3f8a3a'), (8, -32, 10, '#3f8a3a'), (-3, -44, 8, '#5aaa4a'), (5, -38, 6, '#5aaa4a')]:
                pk.disk(img, tx + u, base + v, r, cc)
    pk.rect(img, 0, 240, w, 262, '#5aa04a')
    for fx in range(0, w, 7):
        pk.rect(img, fx, 236, fx + 4, 262, '#ffffff')
        pk.poly(img, [(fx, 236), (fx + 2, 233), (fx + 4, 236)], '#ffffff')
        pk.rect(img, fx + 3, 237, fx + 4, 262, '#d8dce6')
    pk.rect(img, 0, 243, w, 245, '#e8ecf2')
    pk.rect(img, 0, 254, w, 256, '#e8ecf2')
    pk.rect(img, 0, 262, w, 320, '#cfcfd2')
    pk.rect(img, 0, 262, w, 264, '#e6e6ea')
    for sx_ in range(0, w, 34):
        pk.line(img, sx_, 264, sx_ - 12, 320, '#b5b5ba')
    pk.dfill(img, '#c4c4c8', (pk.fbm2d(LH, w, 4, seed) > 0.6) * 0.7 * ((YY_W(w) > 264) & (YY_W(w) < 320)))
    pk.rect(img, 0, 320, w, 327, '#a8a8ae')
    pk.rect(img, 0, 320, w, 321, '#d6d6da')
    pk.rect(img, 0, 327, w, LH, '#4b4f5a')
    pk.dfill(img, '#555a66', (pk.fbm2d(LH, w, 3, seed + 1) > 0.55) * 0.8 * (YY_W(w) > 327))
    for sx_ in range(0, w, 30):
        pk.rect(img, sx_, 420, sx_ + 16, 423, '#e8c43a')
    return img


@functools.lru_cache(None)
def YY_W(w):
    return np.mgrid[0:LH, 0:w][0].astype(np.float32)


def mixhex(a, b, t):
    return pk.mixc(a, b, t)


def street(t, seed, golden=False, slide=True):
    bg = street_bg(seed, golden)
    off = int(round(60 + 60 * (1 - ease_out(t / 0.35)))) if slide else 60
    return bg[:, off:off + LW].copy()


@functools.lru_cache(None)
def _sunburst(n=12):
    ang = np.arctan2(YY - 200, XX - 135)
    return ang


def sunburst(t, c1='#ffcf3f', c2='#ffb02e'):
    ang = _sunburst()
    img = pk.canvas(c1)
    m = (((ang + t * 0.35) * 12 / math.pi) % 2) < 1
    img[m] = C(c2)
    d = np.hypot(YY - 200, XX - 135)
    pk.dfill(img, '#ff8a2a', np.clip((d - 120) / 260, 0, 0.7))
    return img


# =========================================================================== shot renderers
def beat_bob(T, amp):
    ph = (T * BPM / 60.0) % 1.0
    return -amp * math.sin(math.pi * ph) ** 2


def shot_closeup(t, k):
    img = sunburst(k.T)
    S = 58
    dog_front(img, 135, 196, S, k.T, mouth=k.mouth('dog'), bob=beat_bob(k.T, 3))
    if k.mode == 'end':
        k.text('FOLLOW IF', 540, 1330, 48, '#ffffff', t0=0.35)
        k.text("YOU'RE MY DAWG", 540, 1410, 48, '#ffe14d', t0=0.5)
    return img


def shot_mail(t, k):
    img = street(t, 3)
    dx = 20 + 62 * ease_out(t / 0.7)
    walk = 1.0 if t < 0.7 else 0.0
    l0, l1 = k.shot.lines
    hop = 0.0
    if l0.start - k.shot.start <= t < l0.end - k.shot.start:
        hop = abs(math.sin((t - (l0.start - k.shot.start)) * 9)) * 4
    te = l1.end - k.shot.start
    mx = 210
    run = 0.0
    face = -1
    if t > te - 0.05:
        dt = t - te + 0.05
        face = 1
        run = 1.0
        mx = 210 + 240 * dt ** 1.25
        for j in range(5):
            px = 210 + 240 * max(0.0, dt - 0.08 * j) ** 1.25 - 12
            pk.disk(img, px, GROUND - 2, 4 + j * 0.9, '#e8e2d6', alpha=max(0.0, 0.8 - 0.15 * j))
    scared = 1.0 if t > l0.start - k.shot.start + 0.2 else 0.0
    mailman(img, mx, GROUND, 2.2, k.T, run=run, mouth=k.mouth('mail'), facing=face, scared=scared)
    dog_side(img, dx, GROUND - hop, DS, k.T, walk=walk, mouth=k.mouth('dog'), wag=1.0)
    k.text('MAILMAN', Hx(210), Hx(GROUND + 12), 24, '#7fc8ff', t0=0.3, t1=te)
    return img


def Hx(v):
    return v * 4


def shot_squirrel(t, k):
    img = street(t, 7)
    dx = 20 + 62 * ease_out(t / 0.7)
    walk = 1.0 if t < 0.7 else 0.0
    l0, l1, l2 = k.shot.lines
    te = l1.end - k.shot.start
    t_hit = te + 0.5
    arm = ramp(t, te - 0.05, te + 0.08) * (1 - ramp(t, te + 0.25, te + 0.4))
    squirrel(img, 214, 237, 2.4, k.T, mouth=k.mouth('sq'), arm=arm, acorn=t < te + 0.06)
    bonked = t >= t_hit
    head = (dx + DS * 18, GROUND - DS * 27)
    dog_side(img, dx, GROUND, DS, k.T, walk=walk, mouth=k.mouth('dog'), wag=1.0 if not bonked else 0.3,
             glasses='on')
    if te + 0.06 <= t < t_hit:
        u = (t - te - 0.06) / (t_hit - te - 0.06)
        ax = 222 + (head[0] - 222) * u
        ay = 196 + (head[1] - 10 - 196) * u - 50 * math.sin(math.pi * u)
        pk.ellipse(img, ax, ay, 3.4, 4.2, '#a0662a')
        pk.rect(img, ax - 3.4, ay - 5.4, ax + 3.4, ay - 2.6, '#5a3a1a')
    if bonked and t < t_hit + 1.6:
        for j in range(4):
            a = (t - t_hit) * 7 + j * TAU / 4
            sx_ = head[0] + 16 * math.cos(a)
            sy_ = head[1] - 24 + 5 * math.sin(a)
            pk.rect(img, sx_ - 1, sy_ - 3, sx_ + 2, sy_ + 4, '#ffe14d')
            pk.rect(img, sx_ - 3, sy_ - 1, sx_ + 4, sy_ + 2, '#ffe14d')
        k.stamp('BONK!', Hx(head[0]) + 40, Hx(head[1]) - 150, 56, '#ffb36a', t0=t_hit, t1=t_hit + 0.9)
        k.shake(10 * max(0.0, 1 - (t - t_hit) / 0.3))
    if l2.start - k.shot.start <= t:
        for j in range(3):
            dt = t - (l2.start - k.shot.start) - j * 0.25
            if dt > 0:
                hearts(img, head[0] + 10 + j * 8, head[1] - 10 - dt * 30, 1 if dt < 1.2 else 0)
    k.text('SQUIRREL', Hx(214), Hx(GROUND + 12), 24, '#ffb36a', t0=0.3, t1=te)
    return img


HEART = pk.sprite([".##.##.", "#######", "#######", ".#####.", "..###..", "...#..."], {'#': '#ff4d6d'})


def hearts(img, x, y, a):
    if a > 0:
        pk.blit(img, HEART, x, y)


def shot_cat(t, k):
    img = street(t, 11)
    dx = 20 + 62 * ease_out(t / 0.7)
    walk = 1.0 if t < 0.7 else 0.0
    lines = k.shot.lines
    turned = t > lines[3].end - k.shot.start - 0.1
    cat(img, 214, 237, 2.4, k.T, mouth=k.mouth('cat'), facing=1 if turned else -1, turned=turned)
    dog_side(img, dx, GROUND, DS, k.T, walk=walk, mouth=k.mouth('dog'), wag=1.0)
    k.text('CAT', Hx(214), Hx(GROUND + 12), 24, '#ff9ad5', t0=0.3, t1=lines[1].end - k.shot.start)
    return img


def shot_robot(t, k):
    l0, l1 = k.shot.lines
    t_sc = l0.end - k.shot.start
    t_leave = l1.end - k.shot.start + 0.05
    img = street(t, 17)
    dx = 20 + 62 * ease_out(t / 0.7)
    walk = 1.0 if t < 0.7 else 0.0
    rx = 320 - 112 * ease_out(t / 1.0)
    if t > t_leave:
        rx = 208 + 280 * (t - t_leave) ** 1.4
    face = 'eyes'
    if t > t_sc + 0.5:
        face = '?'
    if t > l1.start - k.shot.start + 0.6:
        face = '!'
    robot(img, rx, GROUND, 2.3, k.T, face=face)
    if face == '?' and t < t_leave:
        k.stamp('?', Hx(rx - 14), Hx(GROUND - 60), 64, '#ffe14d', t0=t_sc + 0.5)
    if face == '!' and t < t_leave:
        k.stamp('!', Hx(rx - 14), Hx(GROUND - 60), 64, '#ff4d4d', t0=l1.start - k.shot.start + 0.6)
    angry = t > t_sc
    dog_side(img, dx, GROUND, DS, k.T, walk=walk, mouth=k.mouth('dog'), wag=0.0 if angry else 1.0,
             glasses='down' if angry else 'on', mood='angry' if angry else 'chill')
    if angry:
        img = pk.saturate(img, 0.35)
        pk.tint(img, '#ff2a2a', 0.12)
        d = np.hypot(YY - 270, XX - 135)
        pk.dfill(img, '#3a0008', np.clip((d - 130) / 170, 0, 0.8))
        k.stamp('NOT MY DAWG', 540, 620, 56, '#ff4d4d', t0=t_sc + 0.05)
        if t < t_sc + 0.25:
            k.shake(12)
    k.text('DELIVERY ROBOT', Hx(200), Hx(GROUND + 12), 24, '#9fe0ff', t0=0.7, t1=t_sc)
    return img


def shot_puddle(t, k):
    img = pk.canvas('#a9a9ad')
    n = pk.fbm2d(LH, LW, 6, 21)
    pk.dfill(img, '#9a9aa0', (n > 0.55) * 0.9)
    pk.dfill(img, '#b8b8bc', (n < 0.4) * 0.6)
    for y in range(0, LH, 60):
        pk.line(img, 0, y, LW, y + 6, '#8e8e94')
    for x in range(-20, LW, 70):
        pk.line(img, x, 0, x + 14, LH, '#8e8e94')
    pk.polyline(img, [(30, 380), (52, 392), (60, 420), (82, 430)], '#7a7a80')
    pk.poly(img, [(210, 350), (222, 344), (230, 352), (220, 360)], '#c8902a')
    # puddle shape
    m = np.zeros((LH, LW), bool)
    for (cx, cy, rx, ry) in [(135, 200, 104, 86), (70, 240, 50, 40), (200, 150, 50, 44), (160, 270, 60, 30)]:
        m |= ((XX - cx) / rx) ** 2 + ((YY - cy) / ry) ** 2 <= 1
    rim = m & ~(np.roll(m, 3, 0) & np.roll(m, -3, 0) & np.roll(m, 3, 1) & np.roll(m, -3, 1))
    water = pk.canvas()
    pk.vgrad(water, 0, LH, ['#9fd0f6', '#6fb0ea', '#4f8fd0', '#3a74b8'])
    for j in range(3):
        pk.blit(water, pk.cloud_sprite(50, 14, 70 + j), 30 + j * 70 + 6 * math.sin(k.T * 0.3 + j), 80 + j * 60)
    mouth = k.mouth('dog')
    dog_front(water, 135, 196, 44, k.T, mouth=mouth, bob=beat_bob(k.T, 2))
    amp = 0.6 + 2.2 * mouth
    for y in range(LH):
        sh = int(round(amp * math.sin(y * 0.35 + k.T * 7)))
        if sh:
            water[y] = np.roll(water[y], sh, axis=0)
    water = water * 0.85 + C('#3a74b8') * 0.15
    img[m] = water[m]
    img[rim] = C('#7f8790')
    for j in range(3):
        r = ((k.T * 30 + j * 30) % 90)
        pk.ellipse(img, 135, 200, 20 + r, 14 + r * 0.8, '#d6ecff', alpha=max(0.0, 0.6 - r / 120) * (0.4 + mouth), ring=1)
    lines = k.shot.lines
    if len(lines) > 1 and t > lines[1].start - k.shot.start:
        dt = t - (lines[1].start - k.shot.start)
        for j in range(5):
            u = dt - j * 0.18
            if u > 0:
                hearts(img, 60 + j * 36, 300 - u * 50 - (j % 2) * 20, 1)
        k.stamp('HE GETS IT', 540, 1480, 48, '#ff7aa8', t0=lines[1].end - k.shot.start - 0.5)
    k.text('HIS REFLECTION', 540, 420, 32, '#9fe0ff', t0=0.25, t1=lines[0].end - k.shot.start + 0.4)
    return img


def shot_hotdog(t, k):
    l0, l1 = k.shot.lines
    t_chomp = l0.end - k.shot.start + 0.6
    img = street(t, 23, golden=True)
    hx, hy = 208, GROUND - 4
    eaten = t >= t_chomp
    if not eaten:
        ang = np.arctan2(YY - hy, XX - hx)
        rays = (((ang + k.T * 0.25) * 8 / math.pi) % 2 < 1) & (YY < hy)
        a = 0.35 * np.clip(1 - np.hypot(YY - hy, XX - hx) / 260, 0, 1)
        pk.dfill(img, '#fff6c0', a, rays)
        pk.glow(img, hx, hy, 34, '#fff6c0', 0.9, 1.4)
        hotdog(img, hx, hy, 2.6)
        for j in range(6):
            a_ = k.T * 2 + j * TAU / 6
            if math.sin(k.T * 6 + j * 2) > 0:
                sx_, sy_ = hx + 38 * math.cos(a_), hy - 14 + 20 * math.sin(a_)
                pk.rect(img, sx_, sy_ - 2, sx_ + 1, sy_ + 3, '#ffffff')
                pk.rect(img, sx_ - 2, sy_, sx_ + 3, sy_ + 1, '#ffffff')
        k.text('A HOT DOG', Hx(hx), Hx(GROUND + 14), 24, '#ffe14d', t0=0.3)
    # dog creeps in reverently, then CHOMP
    dx = 20 + 86 * ease_out(t / max(0.5, t_chomp - 0.3))
    lunge = 0.0
    if t_chomp - 0.12 <= t < t_chomp + 0.35:
        lunge = 1.0
        dx += 12
    elif eaten:
        dx += 6
    walk = 0.5 if t < t_chomp - 0.3 else 0.0
    if eaten:
        for j in range(7):
            pk.rect(img, hx - 16 + j * 5, GROUND - 1 + (j % 2), hx - 13 + j * 5, GROUND + 1 + (j % 2), '#e8b46a')
        if t > l1.start - k.shot.start - 0.3:
            dt = t - (l1.start - k.shot.start - 0.3)
            hotdog(img, hx, hy - 30 - dt * 34, 2.4, alpha=max(0.0, 0.9 - dt * 0.15), halo=True,
                   wing=0.5 + 0.5 * math.sin(k.T * 14))
        k.stamp('CHOMP', 540, 600, 96, '#ff6a3c', t0=t_chomp, t1=l1.start - k.shot.start)
        k.stamp('R.I.P. MY DAWG', 540, 560, 48, '#ffffff', t0=l1.start - k.shot.start)
        if t < t_chomp + 0.08:
            pk.tint(img, '#ffffff', 0.8)
        k.shake(14 * max(0.0, 1 - (t - t_chomp) / 0.35))
        img = pk.saturate(img, 1 - 0.5 * ramp(t, t_chomp + 0.3, t_chomp + 1.2))
        tb = l1.end - k.shot.start + 0.3
        k.stamp('*BURP*', Hx(dx + 70), Hx(GROUND - 90), 40, '#c9f07a', t0=tb, t1=tb + 1.0)
    dog_side(img, dx, GROUND, DS, k.T, walk=walk, mouth=k.mouth('dog'), wag=0.4, glasses='up', lunge=lunge,
             look_down=0.3 if not eaten else 0.0)
    return img


SHOTS = {'closeup': shot_closeup, 'mail': shot_mail, 'squirrel': shot_squirrel, 'cat': shot_cat,
         'robot': shot_robot, 'puddle': shot_puddle, 'hotdog': shot_hotdog}


# =========================================================================== context / render
class Ctx(mv.Ctx):
    def __init__(self, shot, T, fi, R):
        self.shot = shot
        self.T = T
        self.t = T - shot.start
        self.fi = fi
        self.mode = shot.mode
        self.dur = shot.dur
        self.nlines = len(shot.lines)
        self.ui = []
        self.shake_amt = 0.0
        self.R = R

    def mouth(self, spk):
        return self.R.mouth(spk, self.T)


class Renderer:
    def __init__(self):
        self.lines, self.shots, self.total = build()
        self.starts = [s.start for s in self.shots]
        self.caps = self.build_caps()
        self.cap_starts = [c['start'] for c in self.caps]
        self.vig = pk.vignette_mask(0.4)
        self.greets = [ln.start for ln in self.lines if ln.greet]
        self.t_enemy = self.lines[11].start
        self.t_eaten = self.lines[14].end + 0.6

    def mouth(self, spk, T):
        v = 0.0
        for ln in self.lines:
            if ln.spk == spk and ln.start <= T < ln.end:
                i = int((T - ln.start) * 100)
                v = max(v, float(ln.env[min(i, len(ln.env) - 1)]))
        return min(1.0, v * 1.1)

    def build_caps(self):
        caps = []
        for ln in self.lines:
            groups, cur = [], []
            for tk in ln.toks:
                d = re.sub(r'[.,;:]+$', '', tk['disp'].upper()).replace('...', '')
                tk['cap'] = d
                n = sum(len(x['cap']) + 1 for x in cur) + len(d)
                if cur and (len(cur) >= 3 or n > 15):
                    groups.append(cur)
                    cur = []
                cur.append(tk)
                if tk['punct']:
                    groups.append(cur)
                    cur = []
            if cur:
                groups.append(cur)
            for gi, g in enumerate(groups):
                st = g[0]['t'] - 0.04
                en = groups[gi + 1][0]['t'] - 0.04 if gi + 1 < len(groups) else ln.end + min(0.4, ln.gap)
                caps.append(dict(start=st, end=en, toks=g, spk=ln.spk))
        for i in range(len(caps) - 1):
            caps[i]['end'] = min(caps[i]['end'], caps[i + 1]['start'])
        return caps

    def frame(self, fi):
        T = fi / FPS
        si = max(0, bisect.bisect_right(self.starts, T) - 1)
        sh = self.shots[si]
        k = Ctx(sh, T, fi, self)
        img = SHOTS[sh.kind](k.t, k)
        img = img * self.vig[..., None]
        u8 = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
        big = np.repeat(np.repeat(u8, SC, 0), SC, 1)
        if k.shake_amt > 0.5:
            rng = np.random.RandomState(fi)
            a = int(k.shake_amt)
            big = np.roll(big, ((rng.randint(-a, a + 1) // 2) * 2, (rng.randint(-a, a + 1) // 2) * 2), (0, 1))
        self.hud(big, T, sh)
        for (spr, x, y, scl) in k.ui:
            mv.composite(big, spr, x, y, scl)
        self.captions(big, T)
        return big

    def hud(self, big, T, sh):
        if sh.key == 'closeup:start':
            big[:560] = (big[:560] * (0.55 + 0.45 * (np.arange(560) / 560.0))[:, None, None]).astype(np.uint8)
            mv.composite(big, mv.text_sprite('MY DOG GREETS', 48, '#ffffff'), 540, 270, 1.0)
            mv.composite(big, mv.text_sprite('EVERYTHING', 88, '#ffe14d', 10), 540, 370, mv.pop_scale(T, 0.25, 0.4))
            mv.composite(big, mv.text_sprite('LIKE THIS', 48, '#ffffff'), 540, 470, 1.0)
            return
        n = sum(1 for g in self.greets if g <= T)
        last = max([g for g in self.greets if g <= T] or [0])
        rows = [((('DAWGS GREETED: ', '#ffffff'), (str(n), '#ffe14d')),)]
        if T >= self.t_enemy:
            rows.append(((('ENEMIES: ', '#ffffff'), ('1', '#ff4d4d')),))
        if T >= self.t_eaten:
            rows.append(((('DAWGS EATEN: ', '#ffffff'), ('1', '#ff9a3c')),))
        y = 236
        for i, r in enumerate(rows):
            scl = mv.pop_scale(T - last, 0.2, 1.4) if i == 0 else 1.0
            if i == 1:
                scl = mv.pop_scale(T - self.t_enemy, 0.2, 1.4)
            if i == 2:
                scl = mv.pop_scale(T - self.t_eaten, 0.2, 1.4)
            mv.composite(big, mv.rich_sprite(r, 32), 540, y + i * 56, scl)
        if 0 <= T - last < 0.6 and n > 1:
            mv.composite(big, mv.text_sprite('+1', 40, '#ffe14d'), 920, 236 - (T - last) * 80, 1.0, 1 - (T - last) / 0.6)

    def captions(self, big, T):
        i = bisect.bisect_right(self.cap_starts, T) - 1
        if i < 0:
            return
        c = self.caps[i]
        if not (c['start'] <= T < c['end']):
            return
        act = 0
        for j, tk in enumerate(c['toks']):
            if tk['t'] - 0.02 <= T:
                act = j
        hl = SPK[c['spk']]['color']
        words = []
        for j, tk in enumerate(c['toks']):
            col = hl if (j == act or tk['emph']) else '#ffffff'
            if tk['emph'] and j != act:
                col = '#ff7a4a' if c['spk'] == 'dog' else hl
            words.append((tk['cap'], col))
        rows, row, n = [], [], 0
        for wd, col in words:
            if row and n + 1 + len(wd) > 15:
                rows.append(row)
                row, n = [], 0
            if row:
                row.append((' ', '#ffffff'))
                n += 1
            row.append((wd, col))
            n += len(wd)
        rows.append(row)
        spr = mv.rich_sprite(tuple(tuple(r) for r in rows), 56)
        mv.composite(big, spr, 540, CAP_Y, mv.pop_scale(T - c['start'], 0.1, 0.8))


# =========================================================================== audio
def lofi(D, gates):
    N = au.n_(D) + au.n_(2)
    beat = 60.0 / BPM
    bar = 4 * beat
    sw = 0.08 * beat
    chords = [([53, 57, 60, 64], 41), ([52, 55, 59, 62], 40), ([50, 53, 57, 60], 38), ([48, 52, 55, 59], 36)]
    keys = np.zeros(N)
    bass = np.zeros(N)
    drums = np.zeros(N)
    for b in range(int(D / bar) + 2):
        t0 = b * bar
        notes, root = chords[b % 4]
        for hit, dur, vel in ((0.0, 1.4, 1.0), (1.5 * beat + sw, 2.0, 0.7)):
            k = au.n_(dur + 0.4)
            tt = au.tt(k)
            x = np.zeros(k)
            for m in notes:
                f = au.hz(m) * (1 + 0.002 * np.sin(2 * np.pi * 0.7 * tt))
                ph = np.cumsum(f) / SR
                x += (np.sin(2 * np.pi * ph) + 0.3 * np.sin(4 * np.pi * ph) * np.exp(-tt / 0.3)) * np.exp(-tt / 1.6)
            x *= 1 + 0.12 * np.sin(2 * np.pi * 4.2 * tt)
            au.place(keys, x * 0.12 * vel, t0 + hit)
        for hit, m in ((0.0, root), (2.5 * beat + sw, root), (3.5 * beat + sw, root + 7)):
            k = au.n_(0.5)
            x = np.tanh(2 * au.sine(au.hz(m), k)) * au.env_exp(k, 0.3)
            au.place(bass, x * 0.5, t0 + hit)
        for hit in (0.0, 1.5 * beat + sw, 2.0 * beat):
            k = au.n_(0.4)
            au.place(drums, au.sweep_sine(120, 45, k) * au.env_exp(k, 0.16) * 0.9, t0 + hit)
        for hit in (beat, 3 * beat):
            k = au.n_(0.3)
            sn = au.bp(au.noise(k, b * 7 + int(hit * 10)), 1000, 5000) * au.env_exp(k, 0.09) + au.sine(190, k) * au.env_exp(k, 0.04) * 0.4
            au.place(drums, sn * 0.5, t0 + hit + 0.01)
        for s in range(8):
            k = au.n_(0.06)
            off = s * beat / 2 + (sw if s % 2 else 0)
            au.place(drums, au.hp(au.noise(k, b * 13 + s), 7000) * au.env_exp(k, 0.015) * (0.25 if s % 2 else 0.4), t0 + off)
    mix = au.lp(keys, 3500) + au.lp(bass, 400) + au.lp(drums, 6000)
    crackle = (np.random.RandomState(5).rand(N) > 0.9995) * np.random.RandomState(6).randn(N) * 0.5
    hiss = au.hp(au.noise(N, 9), 4000) * 0.01
    mix += au.lp(crackle, 5000) + hiss
    tl = au.tt(N)
    g = np.ones(N)
    for a, b in gates:
        g *= 1 - ((tl >= a) & (tl < b))
    g = au.lp(g, 30)
    return mix * g


def sfx_scratch():
    n = au.n_(0.42)
    t = au.tt(n)
    f = 900 + 700 * np.sin(2 * np.pi * 7 * t) * np.exp(-t * 3)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.4
    nz = au.bp(au.noise(n, 3), 600, 3500) * (0.6 + 0.4 * np.sin(2 * np.pi * 7 * t))
    return au.norm((tone + nz) * au.env_adsr(n, 0.005, 0.12), 0.9)


def sfx_slide():
    n = au.n_(0.42)
    t = au.tt(n)
    f = 500 * 3.2 ** (t / 0.42) * (1 + 0.02 * np.sin(2 * np.pi * 9 * t))
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * au.env_adsr(n, 0.02, 0.08) * 0.6


def sfx_bonk():
    n = au.n_(0.5)
    x = au.sine(640, n) * au.env_exp(n, 0.07) + 0.5 * au.sine(1280, n) * au.env_exp(n, 0.04)
    x += au.hp(au.noise(n, 4), 2000) * au.env_exp(n, 0.006)
    boing = au.sine(1, n) * 0
    f = 260 * (1 + 0.25 * np.sin(2 * np.pi * 14 * au.tt(n)) * np.exp(-au.tt(n) * 4))
    boing = np.sin(2 * np.pi * np.cumsum(f) / SR) * au.env_exp(n, 0.25) * 0.35
    return au.norm(x + boing, 0.9)


def sfx_whoosh():
    n = au.n_(0.55)
    t = au.tt(n)
    x = np.zeros(n)
    w = au.noise(n, 12)
    for i in range(0, n, 512):
        fc = 3000 * (0.15 ** (i / n)) + 200
        x[i:i + 512] = au.bp(w[max(0, i - 2048):i + 512], fc * 0.7, fc * 1.4)[-len(x[i:i + 512]):]
    return au.norm(x * np.sin(np.pi * t / t[-1]) ** 2, 0.8)


def sfx_growl():
    n = au.n_(1.6)
    t = au.tt(n)
    am = 0.6 + 0.4 * np.sin(2 * np.pi * 23 * t)
    x = au.lp(au.saw(78, n) + 0.6 * au.noise(n, 7), 500) * am
    return au.norm(x * au.env_adsr(n, 0.15, 0.3), 0.8)


def sfx_beeps(seq):
    out = []
    for m in seq:
        k = au.n_(0.09)
        out.append(au.square(au.hz(m), k, 0.5) * au.env_adsr(k, 0.003, 0.02) * 0.4)
        out.append(np.zeros(au.n_(0.03)))
    return np.concatenate(out)


def sfx_choir():
    n = au.n_(3.2)
    t = au.tt(n)
    x = np.zeros(n)
    for m in (62, 66, 69, 74, 78):
        for d in (-0.06, 0.06):
            f = au.hz(m + d) * (1 + 0.006 * np.sin(2 * np.pi * 5 * t + m))
            x += 2 * ((np.cumsum(f) / SR) % 1) - 1
    x = au.bp(x, 600, 1300) + 0.5 * au.bp(x, 2200, 3200)
    x *= np.clip(t / 0.6, 0, 1) * np.clip((t[-1] - t) / 0.8, 0, 1)
    return au.norm(au.reverb(x, 1.8, 2.5, 0.35, 3, stereo=False), 0.8)


def sfx_chomp():
    n = au.n_(0.6)
    x = np.zeros(n)
    for o in (0.0, 0.13):
        k = au.n_(0.12)
        y = au.bp(au.noise(k, int(o * 100) + 3), 700, 5000) * au.env_exp(k, 0.03) + au.sweep_sine(160, 60, k) * au.env_exp(k, 0.05)
        au.place(x, y, o)
    return au.norm(x, 1.0)


def sfx_violin():
    seq = [(69, 0.55), (67, 0.55), (65, 0.55), (64, 1.3)]
    out = []
    for m, d in seq:
        k = au.n_(d)
        t = au.tt(k)
        f = au.hz(m) * (1 + 0.012 * np.sin(2 * np.pi * 5.5 * t) * np.clip(t / 0.2, 0, 1))
        s = 2 * ((np.cumsum(f) / SR) % 1) - 1
        out.append(s * au.env_adsr(k, 0.12, 0.12))
    x = au.lp(np.concatenate(out), 3200)
    return au.norm(au.reverb(x, 1.4, 3, 0.3, 4, stereo=False), 0.7)


def sfx_burp():
    n = au.n_(0.55)
    t = au.tt(n)
    f = 95 - 25 * t / t[-1]
    src = 2 * ((np.cumsum(f) / SR) % 1) - 1
    src *= 0.7 + 0.3 * np.sign(np.sin(2 * np.pi * 31 * t))
    x = au.bp(src, 250, 900) + 0.6 * au.bp(src, 900, 1600)
    return au.norm(x * au.env_adsr(n, 0.03, 0.15), 0.9)


SFXD = {
    'scratch': sfx_scratch, 'slide': sfx_slide, 'bonk': sfx_bonk, 'whoosh': sfx_whoosh, 'growl': sfx_growl,
    'beep_q': lambda: sfx_beeps([79, 86]), 'beep_x': lambda: sfx_beeps([91, 91, 79]), 'choir': sfx_choir,
    'chomp': sfx_chomp, 'violin': sfx_violin, 'burp': sfx_burp, 'up': au.SFX['up'], 'count': lambda: au.sfx_blip((84, 91), 0.05),
}


def build_audio(R, path):
    N = au.n_(R.total) + au.n_(2)
    voice = np.zeros(N)
    for ln in R.lines:
        y = ln.audio * (0.12 / (np.sqrt(np.mean(ln.audio ** 2)) + 1e-9))
        au.place(voice, y, ln.start)
        if ln.echo:
            e = au.lp(y, 3000)
            for j, (d, g) in enumerate([(0.05, 0.55), (0.22, 0.25), (0.4, 0.12)]):
                au.place(voice, e * g, ln.start + d)
    voice = np.tanh(voice * 1.3) / 1.3
    sfx = np.zeros(N)
    for ln in R.lines:
        for name, w, g in ln.sfx:
            au.place(sfx, SFXD[name](), when(ln, w), g)
        if ln.greet:
            au.place(sfx, SFXD['count'](), ln.start, 0.35)
    gates = [(R.lines[10].end - 0.02, R.lines[12].start - 0.25), (R.lines[14].start - 0.8, R.shots[-1].start + 0.1)]
    mus = lofi(R.total + 1, gates)[:N]
    mus = np.pad(mus, (0, max(0, N - len(mus))))
    env = au.lp(np.abs(voice), 8)
    env = np.clip(env / (np.percentile(env[env > 1e-4], 90) + 1e-9), 0, 1)
    mus *= 1 - 0.45 * env
    tl = au.tt(N)
    mus *= np.clip((R.total + 0.3 - tl) / 1.0, 0, 1)
    mono = voice + sfx * 0.55 + mus * 0.32
    mix = np.stack([mono, mono], 1)
    mix[:, 0] += mus * 0.04
    mix = mix[:au.n_(R.total)]
    mix = np.tanh(mix / np.max(np.abs(mix)) * 1.1) * 0.9
    raw = os.path.join(BUILD, 'mix_raw.wav')
    sf.write(raw, mix.astype(np.float32), SR, subtype='FLOAT')
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', raw, '-af',
                        'loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'], capture_output=True, text=True)
    js = json.loads(r.stderr[r.stderr.rindex('{'):r.stderr.rindex('}') + 1])
    af = ('loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={input_i}:measured_TP={input_tp}:measured_LRA={input_lra}:'
          'measured_thresh={input_thresh}:offset={target_offset}:linear=true').format(**js)
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', raw, '-af', af, '-ar', str(SR), path], check=True)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preview', type=float, default=0)
    ap.add_argument('--frames', type=float, nargs='*')
    ap.add_argument('--out', default=os.path.join(OUT, 'whaddup_my_dawg.mp4'))
    a = ap.parse_args()
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    R = Renderer()
    print('duration %.1fs, %d lines, %d shots' % (R.total, len(R.lines), len(R.shots)))
    with open(os.path.join(BUILD, 'timeline.json'), 'w') as f:
        json.dump([dict(i=ln.i, spk=ln.spk, start=round(ln.start, 2), end=round(ln.end, 2), text=ln.text) for ln in R.lines], f, indent=1)
    if a.frames:
        for t in a.frames:
            Image.fromarray(R.frame(int(t * FPS))).save(os.path.join(BUILD, 'frame_%05.2f.png' % t))
        return
    if a.preview:
        ts = np.arange(0.3, R.total, a.preview)
        thumbs = [Image.fromarray(R.frame(int(t * FPS))).resize((270, 480), Image.BILINEAR) for t in ts]
        cols = 8
        sheet = Image.new('RGB', (cols * 274, int(math.ceil(len(thumbs) / cols)) * 500), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for i, th in enumerate(thumbs):
            x, y = (i % cols) * 274, (i // cols) * 500
            sheet.paste(th, (x, y))
            d.text((x + 4, y + 484), '%.1fs' % ts[i], fill=(255, 255, 0))
        sheet.save(os.path.join(BUILD, 'preview.png'))
        print(os.path.join(BUILD, 'preview.png'))
        return
    wav = build_audio(R, os.path.join(BUILD, 'mix.wav'))
    vid = os.path.join(BUILD, 'video.mp4')
    p = subprocess.Popen(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                          '-s', '%dx%d' % (W, H), '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'medium',
                          '-crf', '18', '-tune', 'animation', '-pix_fmt', 'yuv420p', vid], stdin=subprocess.PIPE)
    for fi in range(int(math.ceil(R.total * FPS))):
        p.stdin.write(R.frame(fi).tobytes())
    p.stdin.close()
    p.wait()
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', vid, '-i', wav, '-c:v', 'copy', '-c:a', 'aac',
                    '-b:a', '192k', '-shortest', '-movflags', '+faststart', a.out], check=True)
    print('wrote', a.out)


if __name__ == '__main__':
    main()
