#!/usr/bin/env python3
"""What If The World Had No Wind?  -  procedural pixel-art TikTok generator.

Pipeline:  script -> Kokoro TTS (cached) -> word timings -> shots/timeline
           -> pixel-art frames (270x480, upscaled 4x) + HUD + captions -> x264
           -> procedural score + SFX + ducked narration -> loudnorm -> mux.

Usage:
    python make_video.py                      # full render -> out/no_wind_tiktok.mp4
    python make_video.py --preview 12         # contact sheet of 12 frames per row
    python make_video.py --frames 3.0 41.5    # dump specific timestamps as PNGs
"""
import argparse
import bisect
import functools
import hashlib
import json
import math
import multiprocessing as mp
import os
import re
import subprocess
import sys

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont
from scipy import signal
from scipy.ndimage import maximum_filter

import audio as au
import pixelkit as pk
import scenes

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, 'build')
OUT = os.path.join(HERE, 'out')
FONT = os.path.join(HERE, 'assets', 'PressStart2P-Regular.ttf')
KOKORO_DIR = os.environ.get('KOKORO_DIR', os.path.join(HERE, 'assets', 'kokoro'))
VOICE = os.environ.get('VOICE', 'af_heart')
SPEED = float(os.environ.get('SPEED', '1.18'))
FPS = 30
SC = 4
W, H = pk.LW * SC, pk.LH * SC
SR = au.SR

# =========================================================================== the script
# *word* = emphasis colour in captions, [shown|spoken] = caption text vs. narration text.


def L(text, shot, gap=0.16, sfx=(), nocap=False):
    return dict(text=text, shot=shot, gap=gap, sfx=list(sfx), nocap=nocap)


SEGMENTS = [
    dict(key='hook', slam=None, label=None, node=None, accent='#ffe14d', lines=[
        L("What if, tomorrow morning... the wind just *stopped?*", 'meadow:hook', 0.3),
        L("Not slowed down. *Stopped.* Everywhere. *Forever.*", 'meadow:hook', 0.22,
          sfx=[('hit', 'w:stopped', 0.7), ('hit', 'w:forever', 0.8)]),
        L("Sounds peaceful, right? It's *not.*", 'meadow:hook', 0.14),
        L("Here's how fast it all falls apart.", 'meadow:hook', 0.2,
          sfx=[('tick', 0.15 + 0.17 * i, 0.6) for i in range(6)]),
    ]),
    dict(key='h1', slam='HOUR 1', label='THE BLACKOUT', node=0, accent='#ffe14d', lines=[
        L("Hour one.", 'windfarm', 0.12, nocap=True),
        L("Every wind turbine on Earth stops spinning.", 'windfarm', 0.12, sfx=[('turbine', 0.0, 0.9)]),
        L("That's over a *terawatt* of power, gone.", 'windfarm', 0.16, sfx=[('stamp', 0.12, 0.8)]),
        L("Close to a *tenth* of the world's electricity.", 'city:blackout', 0.16,
          sfx=[('stamp', 0.25, 0.7), ('powerdown', 0.55, 0.8)]),
        L("Denmark just lost more than *half* of its.", 'denmark', 0.2, sfx=[('drain', 0.5, 0.6)]),
    ]),
    dict(key='d1', slam='DAY 1', label='DEAD AIR', node=1, accent='#d9b45a', lines=[
        L("Day one.", 'smog:a', 0.12, nocap=True),
        L("The air over cities stops moving.", 'smog:a', 0.12),
        L("Exhaust and smoke have nowhere to go, so they just *pile up.*", 'smog:a', 0.16,
          sfx=[('counter:2.4', 0.15, 0.5), ('stamp', 2.55, 0.8)]),
        L("In [1952|nineteen fifty-two], five days of dead-still air over London killed *thousands.*", 'london', 0.16,
          sfx=[('typing:16', 0.25, 0.5), ('typing:16', 'w:five-0.0', 0.5)]),
        L("Now make that *permanent.* Everywhere.", 'smog:b', 0.22, sfx=[('stamp', 'w:permanent+0.25', 0.8)]),
    ]),
    dict(key='w1', slam='WEEK 1', label='FROZEN WEATHER', node=2, accent='#7fd3ff', lines=[
        L("Week one.", 'storm', 0.12, nocap=True),
        L("Weather stops moving. Storms don't roll in... or roll out.", 'storm', 0.16, sfx=[('blip', 0.05, 0.4)]),
        L("Where it's raining, it *keeps* raining. Where it's dry, it *stays* dry.", 'split', 0.16),
        L("Ocean moisture never makes it inland.", 'coast', 0.14, sfx=[('stamp', 0.9, 0.6)]),
        L("So the monsoon that waters over a *billion* people... never comes.", 'monsoon', 0.2,
          sfx=[('stamp', 'w:never', 0.8)]),
        L("The good news? No hurricanes. No tornadoes.", 'hurricane', 0.12,
          sfx=[('up', 0.08, 0.5), ('blip', 'w:hurricanes', 0.4), ('blip', 'w:tornadoes', 0.4)]),
        L("The bad news? We're just getting *started.*", 'hurricane', 0.2,
          sfx=[('glitch', 0.0, 0.8), ('stamp', 0.02, 0.9), ('glitch', 'w:started-0.05', 0.6)]),
    ]),
    dict(key='m1', slam='MONTH 1', label='DEAD CALM SEAS', node=3, accent='#3fc8ff', lines=[
        L("Month one.", 'sea', 0.12, nocap=True),
        L("The oceans go glass-flat. No wind, no *waves.*", 'sea', 0.16),
        L("Wind-driven currents, like the Gulf Stream, start to *fade.*", 'gulf', 0.12),
        L("And Europe loses the warm water that keeps its winters *mild.*", 'gulf', 0.16,
          sfx=[('stamp', 'w:mild', 0.7)]),
        L("Wind also drags cold, nutrient-rich water up from the deep.", 'upwell:on', 0.14),
        L("Those upwelling zones are under [1%|one percent] of the ocean, but supply up to a *fifth* of all wild-caught fish.",
          'fishstat', 0.14, sfx=[('blip', 0.95, 0.5), ('stamp', 'w:fifth', 0.7)]),
        L("Shut them off, and the food chain starves from the *bottom up.*", 'upwell:off', 0.2,
          sfx=[('down', 'w:bottom', 0.6)]),
    ]),
    dict(key='m3', slam='MONTH 3', label='LIFE UNRAVELS', node=4, accent='#8fe36a', lines=[
        L("Month three.", 'corn', 0.12, nocap=True),
        L("Corn, the most-grown grain on Earth, needs wind to carry its *pollen.*", 'corn', 0.14),
        L("So do pines, oaks, and most grasses.", 'pines', 0.12,
          sfx=[('blip', 'w:pines', 0.35), ('blip', 'w:oaks', 0.35), ('blip', 'w:grasses', 0.35)]),
        L("Dandelion seeds go *nowhere.*", 'dandelion', 0.14),
        L("Albatrosses, built to glide on the wind, sit *stranded* on the sea.", 'albatross', 0.14,
          sfx=[('stamp', 'w:stranded', 0.7)]),
        L("And the Amazon? Every year, wind carries millions of tons of Sahara dust across the Atlantic to *fertilize* it.",
          'dust', 0.12),
        L("That delivery? *Cancelled.*", 'dust', 0.22, sfx=[('stamp', 'w:cancelled', 1.0), ('down', 'w:cancelled+0.1', 0.5)]),
    ]),
    dict(key='y1', slam='YEAR 1', label='PLANET OF EXTREMES', node=5, accent='#ff6a3c', lines=[
        L("Year one.", 'heat:on', 0.12, nocap=True),
        L("Wind is Earth's air conditioner, hauling heat from the equator toward the poles.", 'heat:on', 0.14),
        L("Switch it off, and the tropics *bake,* while the poles freeze even *harder.*", 'heat:off', 0.14,
          sfx=[('stamp', 'w:bake', 0.7), ('stamp', 'w:freeze', 0.7)]),
        L("Far from the coasts, green land dries into *desert.*", 'desert', 0.22, sfx=[('stamp', 'w:desert', 0.7)]),
    ]),
    dict(key='en', slam='POWER CHECK', label='WHO KEEPS THE LIGHTS ON?', node=5, accent='#ffe14d', lines=[
        L("So... who keeps the lights *on?*", 'city:flicker', 0.16),
        L("Solar still works. But with no breeze to cool them, panels run hot and lose *power.*", 'solar', 0.14,
          sfx=[('stamp', 'w:power', 0.7)]),
        L("Hydropower fades, as rain stops reaching the mountains.", 'dam', 0.14, sfx=[('down', 'w:mountains', 0.5)]),
        L("The surprise winner? *Tidal power.*", 'tidal', 0.14, sfx=[('fanfare', 'w:tidal', 0.7)]),
        L("Because the Moon doesn't need *wind.*", 'tidal', 0.3, sfx=[('chime', 0.1, 0.5)]),
    ]),
    dict(key='out', slam=None, label=None, node=None, accent='#ffe14d', lines=[
        L("Wind is just the planet *breathing.*", 'meadow:outro', 0.25),
        L("We never notice it... until it's *gone.*", 'meadow:outro', 0.35),
        L("Follow for the next *what if.*", 'meadow:outro', 0.2, sfx=[('up', 0.1, 0.4)]),
    ]),
]
NODES = ['1H', '1D', '1W', '1M', '3M', '1Y']


# =========================================================================== text parsing / timing
def parse_line(text):
    """-> spoken text, tokens [{disp, spoken, emph, punct}]"""
    protected = {}

    def prot(m):
        key = '\u0001%d\u0001' % len(protected)
        protected[key] = (m.group(1), m.group(2))
        return key
    t = re.sub(r'\[([^|\]]+)\|([^\]]+)\]', prot, text)
    toks = []
    emph = False
    for raw in t.split():
        start_emph = raw.startswith('*')
        end_emph = raw.rstrip('.,?!;:').endswith('*') or raw.endswith('*')
        word = raw.replace('*', '')
        if start_emph:
            emph = True
        e = emph
        if end_emph:
            emph = False
        disp, spoken = word, word
        for key, (d, s) in protected.items():
            if key in word:
                disp = word.replace(key, d)
                spoken = word.replace(key, s)
        m = re.match(r'^(.*?)([.,?!;:…]*)$', spoken)
        punct = m.group(2) if m else ''
        toks.append(dict(disp=disp, spoken=spoken, emph=e, punct=punct))
    spoken_text = ' '.join(tk['spoken'] for tk in toks)
    return spoken_text, toks


def syllables(w):
    w = re.sub(r'[^a-z]', '', w.lower())
    if not w:
        return 1
    n = len(re.findall(r'[aeiouy]+', w))
    if w.endswith('e') and not w.endswith(('le', 'ee')) and n > 1:
        n -= 1
    return max(1, n)


def word_weight(spoken):
    return 0.35 + sum(syllables(p) for p in re.split(r'[\s-]+', spoken) if p)


PAUSE_W = {',': 0.9, '.': 1.4, '?': 1.4, '!': 1.4, ';': 1.0, ':': 1.0}


def norm_word(w):
    return re.sub(r'[^a-z0-9%]', '', w.lower())


def find_pauses(y, sr, min_gap=0.085):
    hop = int(sr * 0.01)
    n = len(y) // hop
    rms = np.sqrt(np.mean(y[:n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    thr = max(rms.max() * 0.06, 1e-4)
    quiet = rms < thr
    gaps = []
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if i > 3 and j < n - 3 and (j - i) * 0.01 >= min_gap:
                gaps.append((i * 0.01, j * 0.01))
            i = j
        else:
            i += 1
    return gaps


def time_words(toks, y, sr):
    dur = len(y) / sr
    breaks = [i for i, tk in enumerate(toks[:-1]) if tk['punct']]
    gaps = find_pauses(y, sr)
    wts = [word_weight(tk['spoken']) for tk in toks]
    starts = [0.0] * len(toks)
    if breaks and len(gaps) >= len(breaks):
        chosen = sorted(sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:len(breaks)])
        spans = []
        prev_t, prev_i = 0.0, 0
        for b, (g0, g1) in zip(breaks, chosen):
            spans.append((prev_i, b + 1, prev_t, g0))
            prev_i, prev_t = b + 1, g1
        spans.append((prev_i, len(toks), prev_t, dur))
        for (i0, i1, t0, t1) in spans:
            tot = sum(wts[i0:i1]) or 1
            acc = t0
            for i in range(i0, i1):
                starts[i] = acc
                acc += (t1 - t0) * wts[i] / tot
    else:
        ww = [wts[i] + (PAUSE_W.get(toks[i]['punct'][-1:], 0) if toks[i]['punct'] and i < len(toks) - 1 else 0) for i in range(len(toks))]
        tot = sum(ww)
        acc = 0.0
        for i in range(len(toks)):
            starts[i] = acc
            acc += dur * ww[i] / tot
    return starts


# =========================================================================== TTS
_kokoro = None


def kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(os.path.join(KOKORO_DIR, 'kokoro-v1.0.onnx'), os.path.join(KOKORO_DIR, 'voices-v1.0.bin'))
    return _kokoro


def tts(text):
    os.makedirs(os.path.join(BUILD, 'tts'), exist_ok=True)
    h = hashlib.sha1(('%s|%s|%.3f' % (VOICE, text, SPEED)).encode()).hexdigest()[:16]
    p = os.path.join(BUILD, 'tts', h + '.wav')
    if not os.path.exists(p):
        y, sr = kokoro().create(text, voice=VOICE, speed=SPEED, lang='en-us')
        sf.write(p, y, sr)
    y, sr = sf.read(p, dtype='float32')
    # trim silence
    a = np.abs(y)
    thr = a.max() * 0.02
    idx = np.where(a > thr)[0]
    i0 = max(0, idx[0] - int(sr * 0.03))
    i1 = min(len(y), idx[-1] + int(sr * 0.06))
    return y[i0:i1], sr


# =========================================================================== timeline
class Line:
    pass


class Shot:
    pass


def build_timeline():
    lines = []
    t = 0.2
    segs = []
    for si, seg in enumerate(SEGMENTS):
        if seg['slam'] and si > 0:
            t += 0.35
        seg_lines = []
        for li, spec in enumerate(seg['lines']):
            spoken, toks = parse_line(spec['text'])
            y, sr = tts(spoken)
            ln = Line()
            ln.__dict__.update(spec)
            ln.seg = si
            ln.idx_in_seg = li
            ln.spoken = spoken
            ln.toks = toks
            ln.audio = y
            ln.sr = sr
            ln.start = t
            ln.dur = len(y) / sr
            ln.end = t + ln.dur
            rel = time_words(toks, y, sr)
            for tk, r in zip(toks, rel):
                tk['t'] = t + r
            ln.i = len(lines)
            lines.append(ln)
            seg_lines.append(ln)
            t = ln.end + spec['gap']
        segs.append(dict(seg, start=seg_lines[0].start, end=t, lines=seg_lines))
        t += 0.1
    total = t + 1.8
    for i, s in enumerate(segs):
        s['end'] = segs[i + 1]['start'] if i + 1 < len(segs) else total
    # shots
    shots = []
    for ln in lines:
        key = ln.shot
        new_seg = ln.idx_in_seg == 0
        if not shots or shots[-1].key != key or new_seg:
            sh = Shot()
            sh.key = key
            sh.kind, _, sh.mode = key.partition(':')
            sh.lines = [ln]
            sh.seg = ln.seg
            sh.seg_start = new_seg
            if not shots:
                sh.start = 0.0
            elif new_seg:
                sh.start = ln.start - 0.05
            else:
                sh.start = ln.start - 0.12
            shots.append(sh)
        else:
            shots[-1].lines.append(ln)
    for i, sh in enumerate(shots):
        sh.end = shots[i + 1].start if i + 1 < len(shots) else total
        sh.dur = sh.end - sh.start
    return lines, segs, shots, total


def resolve_when(ln, when):
    if isinstance(when, (int, float)):
        return ln.start + when
    m = re.match(r'^w:([a-z0-9%]+)([+-][0-9.]+)?$', when.lower())
    w, off = m.group(1), float(m.group(2) or 0)
    for tk in ln.toks:
        if norm_word(tk['disp']) == w or norm_word(tk['spoken']) == w:
            return tk['t'] + off
    raise KeyError('word %r not in %r' % (w, ln.text))


# =========================================================================== captions
MAXC = 15


def build_captions(lines):
    chunks = []
    for ln in lines:
        if ln.nocap:
            continue
        cur = []
        groups = []
        for i, tk in enumerate(ln.toks):
            d = tk['disp'].upper().replace('…', '')
            d = re.sub(r'[.,;:]+$', '', d)
            d = d.replace('...', '')
            tk['cap'] = d
            n = sum(len(x['cap']) + 1 for x in cur) + len(d)
            if cur and (len(cur) >= 3 or n > MAXC):
                groups.append(cur)
                cur = []
            cur.append(tk)
            if tk['punct'] and tk['punct'] not in ("'",):
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)
        for gi, g in enumerate(groups):
            st = g[0]['t'] - 0.04
            en = groups[gi + 1][0]['t'] - 0.04 if gi + 1 < len(groups) else ln.end + min(0.35, ln.gap + 0.1)
            chunks.append(dict(start=st, end=en, toks=g, seg=ln.seg))
    for i in range(len(chunks) - 1):
        chunks[i]['end'] = min(chunks[i]['end'], chunks[i + 1]['start'])
    return chunks


# =========================================================================== text sprites
@functools.lru_cache(None)
def font(size):
    return ImageFont.truetype(FONT, size)


def outline_for(size):
    return 4 if size <= 24 else 5 if size <= 32 else 6 if size <= 48 else 7 if size <= 56 else 8


@functools.lru_cache(maxsize=6000)
def rich_sprite(lines, size, outline=None, shadow=True):
    """lines: tuple of tuple of (text, hexcolour). Returns uint8 RGBA."""
    f = font(size)
    o = outline_for(size) if outline is None else outline
    lh = int(size * 1.45)
    maxlen = max(sum(len(t) for t, _ in ln) for ln in lines)
    pad = o + 4 + (o if shadow else 0)
    Wd = maxlen * size + 2 * pad
    Hd = lh * (len(lines) - 1) + size + 2 * pad
    mask = Image.new('L', (Wd, Hd), 0)
    col = Image.new('RGB', (Wd, Hd), (0, 0, 0))
    dm, dc = ImageDraw.Draw(mask), ImageDraw.Draw(col)
    dm.fontmode = '1'
    dc.fontmode = '1'
    for li, ln in enumerate(lines):
        n = sum(len(t) for t, _ in ln)
        x = pad + ((maxlen - n) * size) // 2
        x -= x % 2
        y = pad + li * lh
        for t, c in ln:
            dm.text((x, y), t, font=f, fill=255)
            dc.text((x, y), t, font=f, fill=c)
            x += len(t) * size
    m = np.asarray(mask) > 127
    ol = maximum_filter(m, size=2 * o + 1)
    out = np.zeros((Hd, Wd, 4), np.uint8)
    if shadow:
        sh = np.zeros_like(ol)
        sh[o:, o // 2:] = ol[:-o, :Wd - o // 2]
        out[sh] = (0, 0, 0, 150)
    out[ol] = (8, 8, 16, 255)
    rgb = np.asarray(col)
    out[m, :3] = rgb[m]
    out[m, 3] = 255
    return out


def text_sprite(s, size, color, outline=None):
    return rich_sprite((((s, color),),), size, outline)


def composite(frame, spr, cx, cy, scale=1.0, alpha=1.0):
    if alpha <= 0.01:
        return
    if abs(scale - 1) > 1e-3:
        h, w = spr.shape[:2]
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        spr = np.asarray(Image.fromarray(spr).resize((nw, nh), Image.NEAREST))
    h, w = spr.shape[:2]
    x0 = int(round(cx - w / 2))
    y0 = int(round(cy - h / 2))
    x0 -= x0 % 2
    y0 -= y0 % 2
    a0, b0 = max(0, x0), max(0, y0)
    a1, b1 = min(W, x0 + w), min(H, y0 + h)
    if a1 <= a0 or b1 <= b0:
        return
    s = spr[b0 - y0:b1 - y0, a0 - x0:a1 - x0]
    a = s[..., 3:4].astype(np.float32) * (alpha / 255.0)
    reg = frame[b0:b1, a0:a1].astype(np.float32)
    frame[b0:b1, a0:a1] = (s[..., :3] * a + reg * (1 - a)).astype(np.uint8)


def box(frame, x0, y0, x1, y1, color, alpha):
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
    if x1 <= x0 or y1 <= y0:
        return
    reg = frame[y0:y1, x0:x1].astype(np.float32)
    frame[y0:y1, x0:x1] = (reg * (1 - alpha) + np.array(pk.C(color)) * 255 * alpha).astype(np.uint8)


def pop_scale(dt, dur=0.14, start=0.6):
    if dt >= dur:
        return 1.0
    x = max(0.0, dt / dur)
    return start + (1 - start) * (1 - (1 - x) ** 3) + 0.08 * math.sin(math.pi * x)


def slam_scale(dt):
    if dt >= 0.13:
        return 1.0
    x = max(0.0, dt / 0.13)
    return 2.0 - 1.0 * (1 - (1 - x) ** 2)


# =========================================================================== shot context
class Ctx:
    def __init__(self, shot, T, fi):
        self.shot = shot
        self.T = T
        self.t = T - shot.start
        self.fi = fi
        self.mode = shot.mode
        self.dur = shot.dur
        self.nlines = len(shot.lines)
        self.ui = []
        self.shake_amt = 0.0

    def cue(self, i):
        return self.shot.lines[i].start - self.shot.start

    def cue_end(self, i):
        return self.shot.lines[i].end - self.shot.start

    def _find(self, w, line=None):
        w = norm_word(w)
        lns = self.shot.lines if line is None else [self.shot.lines[line]]
        for ln in lns:
            for tk in ln.toks:
                if norm_word(tk['disp']) == w or norm_word(tk['spoken']) == w:
                    return tk['t'] - self.shot.start
        return None

    def has_word(self, w):
        return self._find(w) is not None

    def word(self, w, line=None):
        r = self._find(w, line)
        if r is None:
            raise KeyError(w)
        return r

    def text(self, s, x, y, size=32, color='#ffffff', t0=None, t1=None, pop=True):
        if t0 is not None and self.t < t0:
            return
        if t1 is not None and self.t >= t1:
            return
        sc = pop_scale(self.t - t0) if (pop and t0 is not None) else 1.0
        self.ui.append((text_sprite(s, size, color), x, y, sc))

    def stamp(self, s, x, y, size=48, color='#ffffff', t0=0.0, t1=None):
        if self.t < t0 or (t1 is not None and self.t >= t1):
            return
        self.ui.append((text_sprite(s, size, color, outline_for(size) + 2), x, y, slam_scale(self.t - t0)))

    def type(self, s, x, y, size, color, t0=0.0, cps=16):
        if self.t < t0:
            return
        n = int((self.t - t0) * cps)
        shown = s[:n]
        cursor = '_' if (n < len(s) or int(self.t * 3) % 2 == 0) else ' '
        full = (shown + cursor).ljust(len(s) + 1)
        self.ui.append((text_sprite(full, size, color), x, y, 1.0))

    def shake(self, px):
        self.shake_amt = max(self.shake_amt, abs(px))


# =========================================================================== frame renderer
class Renderer:
    def __init__(self):
        self.lines, self.segs, self.shots, self.total = build_timeline()
        self.caps = build_captions(self.lines)
        self.cap_starts = [c['start'] for c in self.caps]
        self.shot_starts = [s.start for s in self.shots]
        self.vig = pk.vignette_mask(0.5)
        g = np.ones(H, np.float32)
        g[:460] = 0.5 + 0.5 * (np.arange(460) / 460.0) ** 0.8
        self.topgrad = g
        hook = self.segs[0]['lines']
        self.ts = resolve_when(hook[0], 'w:stopped')
        self.timeline_t0 = hook[3].start

    # ---------------------------------------------------------------- shot image
    def shot_image(self, si, T, fi):
        sh = self.shots[si]
        k = Ctx(sh, T, fi)
        img = scenes.SHOTS[sh.kind](k.t, k)
        return img, k

    def frame(self, fi):
        T = fi / FPS
        si = max(0, bisect.bisect_right(self.shot_starts, T) - 1)
        sh = self.shots[si]
        img, k = self.shot_image(si, T, fi)
        TR = 0.2
        if si > 0 and not sh.seg_start and T - sh.start < TR:
            pimg, _ = self.shot_image(si - 1, T, fi)
            a = (T - sh.start) / TR
            m = a > pk.BAYER[:pk.LH, :pk.LW]
            img = np.where(m[..., None], img, pimg)
        img = img * self.vig[..., None]
        u8 = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)
        big = np.repeat(np.repeat(u8, SC, 0), SC, 1)
        seg_i = sh.seg
        seg = self.segs[seg_i]
        # segment-start flash + shake
        shake = k.shake_amt
        if seg['slam'] and seg_i > 0:
            dt = T - seg['start']
            if 0 <= dt < 0.1:
                big = (big + (255 - big.astype(np.int16)) * (1 - dt / 0.1) * 0.85).astype(np.uint8)
            if 0 <= dt < 0.4:
                shake = max(shake, 22 * (1 - dt / 0.4))
        if shake > 0.5:
            rng = np.random.RandomState(fi)
            dx, dy = (rng.randint(-int(shake), int(shake) + 1) // 2) * 2, (rng.randint(-int(shake), int(shake) + 1) // 2) * 2
            big = np.roll(big, (dy, dx), (0, 1))
        self.hud(big, T, seg_i, seg)
        for (spr, x, y, sc) in k.ui:
            composite(big, spr, x, y, sc)
        self.captions(big, T)
        return big

    # ---------------------------------------------------------------- HUD
    def hud(self, big, T, seg_i, seg):
        in_outro = seg['key'] == 'out'
        show_tl = T >= self.timeline_t0 and not in_outro
        if show_tl or T < self.segs[0]['lines'][2].end + 0.3:
            big[:460] = (big[:460] * self.topgrad[:460, None, None]).astype(np.uint8)
        # hook title
        hook_end = self.segs[0]['lines'][2].end + 0.2
        if T < hook_end:
            a = 1.0 if T < hook_end - 0.25 else (hook_end - T) / 0.25
            composite(big, text_sprite('WHAT IF THE', 64, '#ffffff'), 540, 300, 1.0, a)
            composite(big, rich_sprite(((('WORLD HAD ', '#ffffff'), ('NO', '#ffe14d')),), 64), 540, 392, 1.0, a)
            composite(big, text_sprite('WIND?', 112, '#ffe14d', 10), 540, 520, pop_scale(T, 0.25, 0.3), a)
        if show_tl:
            self.timeline(big, T, seg)
        if seg.get('label'):
            ln0 = seg['lines'][0]
            slam_end = ln0.end + 0.25
            if T >= slam_end:
                composite(big, text_sprite(seg['label'], 40 if len(seg['label']) <= 20 else 32, seg['accent']), 540, 322,
                          pop_scale(T - slam_end), 1.0)
            elif T >= ln0.start - 0.05:
                dt = T - (ln0.start - 0.05)
                box(big, 0, 760, W, 1060, '#000000', 0.45)
                sz = min(128, (960 // len(seg['slam'])) // 8 * 8)
                composite(big, text_sprite(seg['slam'], sz, '#ffffff', 10), 540, 860, slam_scale(dt))
                composite(big, text_sprite(seg['label'], 40 if len(seg['label']) <= 20 else 32, seg['accent']), 540, 985,
                          pop_scale(dt - 0.15))
        if T > hook_end and not in_outro:
            blink = int(T * 2) % 2 == 0
            composite(big, rich_sprite(((('WIND: ', '#ff8a8a'), ('0 KM/H', '#ff4d4d')),), 24),
                      540, 378, 1.0, 1.0 if blink else 0.75)

    def timeline(self, big, T, seg):
        cur = seg['node']
        x0, dx, y = 150, 156, 236
        appear = T - self.timeline_t0
        nshow = 6 if cur is not None else min(6, int(appear / 0.17) + 1)
        box(big, x0, y - 3, x0 + dx * (nshow - 1), y + 3, '#55607a', 1.0)
        if cur is not None and cur > 0:
            box(big, x0, y - 3, x0 + dx * cur, y + 3, '#ffe14d', 1.0)
        for i in range(nshow):
            x = x0 + i * dx
            if cur is None:
                fill, lab = '#2a3348', '#9aa3b8'
            elif i < cur:
                fill, lab = '#ffe14d', '#c9b46a'
            elif i == cur:
                fill, lab = '#ffe14d', '#ffe14d'
            else:
                fill, lab = '#2a3348', '#6a7390'
            if cur is not None and i == cur:
                p = 4 + 4 * (0.5 + 0.5 * math.sin(T * 8))
                box(big, x - 14 - p, y - 14 - p, x + 14 + p, y + 14 + p, '#ffe14d', 0.35)
            box(big, x - 14, y - 14, x + 14, y + 14, '#0b0e1a', 1.0)
            box(big, x - 10, y - 10, x + 10, y + 10, fill, 1.0)
            composite(big, text_sprite(NODES[i], 24, lab), x, y + 40)

    # ---------------------------------------------------------------- captions
    def captions(self, big, T):
        i = bisect.bisect_right(self.cap_starts, T) - 1
        if i < 0:
            return
        c = self.caps[i]
        if not (c['start'] <= T < c['end']):
            return
        toks = c['toks']
        act = 0
        for j, tk in enumerate(toks):
            if tk['t'] - 0.02 <= T:
                act = j
        words = []
        for j, tk in enumerate(toks):
            if tk['emph']:
                col = '#ff7a4a'
            elif j == act:
                col = '#ffe14d'
            else:
                col = '#ffffff'
            words.append((tk['cap'], col))
        # wrap
        rows, row, n = [], [], 0
        for wd, col in words:
            if row and n + 1 + len(wd) > MAXC:
                rows.append(row)
                row, n = [], 0
            if row:
                row.append((' ', '#ffffff'))
                n += 1
            row.append((wd, col))
            n += len(wd)
        rows.append(row)
        spr = rich_sprite(tuple(tuple(r) for r in rows), 56)
        composite(big, spr, 540, 1186, pop_scale(T - c['start'], 0.1, 0.8))


# =========================================================================== audio build
def build_audio(R, path):
    N = au.n_(R.total) + au.n_(3)
    voice = np.zeros(N)
    for ln in R.lines:
        y = signal.resample_poly(ln.audio.astype(np.float64), SR, ln.sr)
        rms = np.sqrt(np.mean(y ** 2)) + 1e-9
        y = y * (0.11 / rms)
        au.place(voice, y, ln.start)
    voice = np.tanh(voice * 1.2) / 1.2
    sfx = np.zeros((N, 2))
    cache = {}

    def S(name):
        if name not in cache:
            cache[name] = au.SFX[name]()
        return cache[name]

    def add(x, t0, g):
        if x.ndim == 1:
            au.place(sfx, np.stack([x, x], 1), t0, g)
        else:
            au.place(sfx, x, t0, g)

    ts = R.ts
    # wind ambience until the stop, and back at the very end
    wind = au.sfx_wind(ts + 0.1)
    wind[-au.n_(0.03):] *= np.linspace(1, 0, au.n_(0.03))
    add(wind, 0.0, 0.42)
    add(S('stop'), ts, 0.9)
    add(S('boom'), ts, 0.6)
    out_seg = R.segs[-1]
    tr = out_seg['lines'][2].start
    w2 = au.sfx_wind(R.total - tr + 1, seed=8) * np.clip(au.tt(au.n_(R.total - tr + 1)) / 1.6, 0, 1)
    add(w2, tr, 0.5)
    # chapter risers + booms
    for si, seg in enumerate(R.segs):
        if seg['slam'] and si > 0:
            s0 = seg['lines'][0].start
            add(S('riser'), s0 - 1.4, 0.45)
            add(S('boom'), s0 - 0.04, 0.9)
    # per-line sfx
    for ln in R.lines:
        for spec in ln.sfx:
            name, when, g = spec
            t0 = resolve_when(ln, when)
            if name.startswith('counter:'):
                add(au.sfx_counter(float(name.split(':')[1])), t0, g)
            elif name.startswith('typing:'):
                nchar = int(name.split(':')[1])
                for c in range(nchar):
                    add(au.sfx_type(), t0 + c / 16.0, g)
            else:
                add(S(name), t0, g)
    # scene ambiences
    for sh in R.shots:
        if sh.kind == 'storm':
            add(au.sfx_rain(sh.dur + 0.4), sh.start, 0.35)
            for tb in scenes.STORM_BOLTS:
                if tb < sh.dur:
                    add(S('thunder') if tb < 2 else S('thunder2'), sh.start + tb + 0.08, 0.75)
        if sh.kind == 'split':
            add(au.sfx_rain(sh.dur + 0.3, 9), sh.start, 0.2)
        if sh.kind == 'coast':
            add(au.sfx_rain(sh.dur + 0.3, 10), sh.start, 0.15)
        if sh.kind == 'sea':
            add(au.sfx_waves(sh.dur, sh.dur * 0.5), sh.start, 0.45)
        if sh.kind == 'city' and sh.mode == 'flicker':
            add(S('powerdown'), sh.start + 0.4, 0.35)
    # music
    S_ = {s['key']: s['start'] for s in R.segs}
    END = R.total
    plan = {
        'pad': [(ts + 0.9, S_['out'], 0.55), (S_['out'], END + 2, 0.4)],
        'bass': [(S_['h1'], S_['out'], 0.75)],
        'kick': [(S_['d1'], S_['out'], 0.75)],
        'arp': [(S_['w1'], S_['out'], 0.5)],
        'snare': [(S_['m1'], S_['out'], 0.45)],
        'hat': [(S_['m3'], S_['out'], 0.4)],
        'lead': [(S_['y1'], S_['out'], 0.42)],
    }
    mus, parts = au.music(END + 1, plan)
    airy = parts['airy'][:N] if len(parts['airy']) >= N else np.pad(parts['airy'], (0, N - len(parts['airy'])))
    airy = au.tape_stop(airy * 0.9, ts, 0.6)
    mus = mus[:N] if len(mus) >= N else np.pad(mus, ((0, N - len(mus)), (0, 0)))
    mus[:, 0] += airy
    mus[:, 1] += airy
    tl = au.tt(N)
    gate = np.ones(N)
    for si, seg in enumerate(R.segs):
        if seg['slam'] and si > 0:
            s0 = seg['lines'][0].start
            gate *= 1 - 0.85 * ((tl > s0 - 0.45) & (tl < s0 - 0.03))
    bad = [ln for ln in R.lines if ln.text.startswith('The bad news')][0]
    gate *= 1 - 1.0 * ((tl > bad.start) & (tl < bad.start + 0.45))
    gate = au.lp(gate, 40)
    fade_out = np.clip((END + 0.5 - tl) / 2.5, 0, 1)
    mus *= (gate * fade_out)[:, None]
    # ducking
    env = np.abs(voice)
    env = au.lp(env, 8)
    env = np.clip(env / (np.percentile(env[env > 1e-4], 90) + 1e-9), 0, 1)
    duck = 1 - 0.6 * env
    mus *= duck[:, None]
    mix = mus * 0.3 + sfx * 0.55 + voice[:, None] * 1.0
    if os.environ.get('STEMS'):
        for nm, x in (('voice', voice), ('music', mus[:, 0] * 0.42), ('sfx', sfx[:, 0] * 0.55)):
            sf.write(os.path.join(BUILD, 'stem_%s.wav' % nm), x[:au.n_(END)].astype(np.float32), SR, subtype='FLOAT')
    mix = mix[:au.n_(END)]
    peak = np.max(np.abs(mix))
    mix = np.tanh(mix / peak * 1.1) * 0.9
    raw = os.path.join(BUILD, 'mix_raw.wav')
    sf.write(raw, mix.astype(np.float32), SR, subtype='FLOAT')
    # two-pass loudness normalisation to TikTok-ish -14 LUFS
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', raw, '-af',
                        'loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json', '-f', 'null', '-'],
                       capture_output=True, text=True)
    js = json.loads(r.stderr[r.stderr.rindex('{'):r.stderr.rindex('}') + 1])
    af = ('loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={input_i}:measured_TP={input_tp}:measured_LRA={input_lra}:'
          'measured_thresh={input_thresh}:offset={target_offset}:linear=true').format(**js)
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', raw, '-af', af, '-ar', str(SR), path],
                   check=True)
    return path


# =========================================================================== video build
_R = None


def _worker_init():
    global _R
    _R = Renderer()


def _render_range(args):
    f0, f1, path = args
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', '%dx%d' % (W, H), '-r', str(FPS), '-i', '-', '-c:v', 'libx264', '-preset', 'medium',
           '-crf', '19', '-tune', 'animation', '-pix_fmt', 'yuv420p', '-threads', '2', path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fi in range(f0, f1):
        p.stdin.write(_R.frame(fi).tobytes())
    p.stdin.close()
    p.wait()
    return path


def build_video(R, out_path, workers=4):
    nfr = int(math.ceil(R.total * FPS))
    os.makedirs(os.path.join(BUILD, 'parts'), exist_ok=True)
    nchunks = workers * 3
    bounds = np.linspace(0, nfr, nchunks + 1).astype(int)
    jobs = [(int(bounds[i]), int(bounds[i + 1]), os.path.join(BUILD, 'parts', 'p%03d.mp4' % i)) for i in range(nchunks)]
    with mp.Pool(workers, initializer=_worker_init) as pool:
        for k, pth in enumerate(pool.imap(_render_range, jobs)):
            print('  video chunk %d/%d' % (k + 1, nchunks), flush=True)
    lst = os.path.join(BUILD, 'parts', 'list.txt')
    with open(lst, 'w') as f:
        for _, _, pth in jobs:
            f.write("file '%s'\n" % pth)
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', lst,
                    '-c', 'copy', out_path], check=True)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preview', type=float, default=0, help='contact sheet every N seconds')
    ap.add_argument('--frames', type=float, nargs='*', help='dump frames at these timestamps')
    ap.add_argument('--audio-only', action='store_true')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--out', default=os.path.join(OUT, 'no_wind_tiktok.mp4'))
    a = ap.parse_args()
    os.makedirs(BUILD, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    R = Renderer()
    print('duration %.1fs, %d lines, %d shots, %d caption chunks' % (R.total, len(R.lines), len(R.shots), len(R.caps)))
    with open(os.path.join(BUILD, 'timeline.json'), 'w') as f:
        json.dump([dict(i=ln.i, start=round(ln.start, 2), end=round(ln.end, 2), shot=ln.shot, text=ln.text) for ln in R.lines], f, indent=1)
    if a.frames:
        for t in a.frames:
            Image.fromarray(R.frame(int(t * FPS))).save(os.path.join(BUILD, 'frame_%06.2f.png' % t))
        return
    if a.preview:
        ts = np.arange(0.5, R.total, a.preview)
        thumbs = [Image.fromarray(R.frame(int(t * FPS))).resize((270, 480), Image.BILINEAR) for t in ts]
        cols = 8
        rows = int(math.ceil(len(thumbs) / cols))
        sheet = Image.new('RGB', (cols * 274, rows * 500), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for i, th in enumerate(thumbs):
            x, y = (i % cols) * 274, (i // cols) * 500
            sheet.paste(th, (x, y))
            d.text((x + 4, y + 484), '%.1fs' % ts[i], fill=(255, 255, 0))
        p = os.path.join(BUILD, 'preview.png')
        sheet.save(p)
        print(p)
        return
    wav = build_audio(R, os.path.join(BUILD, 'mix.wav'))
    print('audio done')
    if a.audio_only:
        return
    vid = build_video(R, os.path.join(BUILD, 'video.mp4'), a.workers)
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', vid, '-i', wav, '-c:v', 'copy',
                    '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', a.out], check=True)
    print('wrote', a.out)


if __name__ == '__main__':
    main()
