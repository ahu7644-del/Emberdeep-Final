#!/usr/bin/env python3
"""
LM Studio / macOS Performance Dashboard
=======================================

A full-screen terminal dashboard for macOS (Apple Silicon and Intel), styled like the
Windows v4 dashboard: smooth right-to-left scrolling graphs with 1/8-cell resolution
and ONE shared color scale for every graph, meter, number and label:

    blue/green = normal  ->  yellow = watch  ->  orange = high  ->  red = critical

Run:
    python3 lmstudio_dashboard_mac.py
    sudo python3 lmstudio_dashboard_mac.py      # adds CPU/GPU power, thermal pressure,
                                                # and LM Studio's per-process GPU share
Options:
    --ping HOST     host for the continuous ping panel (default 131.241.51.94)
    --speed N       graph columns per second (default 10; + / - while running)
    --fps N         screen refresh rate (default 30)
    --truecolor / --256color   force the color mode (auto-detected otherwise)

Keys:  Q quit   + / - scroll speed   P pause history

Uses only the Python standard library and built-in macOS tools
(Mach host APIs, IOKit HID temperature sensors, vm_stat, netstat, iostat, ioreg, ps, ping,
and optionally powermetrics when run with sudo).
"""

import argparse
import collections
import ctypes
import itertools
import math
import os
import platform
import plistlib
import pty
import re
import select
import signal
import subprocess
import sys
import termios
import threading
import time
import tty

# ----------------------------------------------------------------------------- settings
DEFAULT_PING = '131.241.51.94'
SPEEDS = [4, 6, 8, 10, 15, 20, 30]
EASE_SECONDS = 0.12

# ----------------------------------------------------------------------------- color
def rgb(r, g, b):
    return (max(0, min(255, int(r))) << 16) | (max(0, min(255, int(g))) << 8) | max(0, min(255, int(b)))


def mix(a, b, t):
    if t < 0: t = 0.0
    if t > 1: t = 1.0
    ar, ag, ab = (a >> 16) & 255, (a >> 8) & 255, a & 255
    br, bg_, bb = (b >> 16) & 255, (b >> 8) & 255, b & 255
    return rgb(round(ar + (br - ar) * t), round(ag + (bg_ - ag) * t), round(ab + (bb - ab) * t))


RAMP_STOPS = [0.00, 0.20, 0.42, 0.50, 0.75, 0.90, 1.00]
RAMP_COLORS = [rgb(70, 150, 255), rgb(45, 200, 215), rgb(80, 222, 125), rgb(242, 218, 80),
               rgb(255, 150, 55), rgb(255, 72, 88), rgb(255, 60, 170)]
MUTED = rgb(92, 106, 124)
_ramp_memo = {}


def ramp(s):
    """Shared color scale. s = severity 0..1 (None -> muted)."""
    if s is None or s != s:
        return MUTED
    key = int(max(0.0, min(1.0, s)) * 400)
    c = _ramp_memo.get(key)
    if c is not None:
        return c
    s = key / 400.0
    c = RAMP_COLORS[-1]
    for i in range(1, len(RAMP_STOPS)):
        if s <= RAMP_STOPS[i]:
            t = (s - RAMP_STOPS[i - 1]) / (RAMP_STOPS[i] - RAMP_STOPS[i - 1])
            c = mix(RAMP_COLORS[i - 1], RAMP_COLORS[i], t)
            break
    _ramp_memo[key] = c
    return c


# floor, WATCH, HIGH, CRITICAL, top of scale
THRESH = {
    'load':    (0, 60, 80, 95, 100),
    'ram':     (0, 75, 88, 95, 100),
    'vram':    (0, 80, 92, 98, 100),
    'cpuTemp': (30, 80, 92, 102, 110),   # Apple Silicon runs hotter than desktop x86
    'gpuTemp': (30, 75, 88, 98, 105),
    'ping':    (0, 60, 120, 250, 500),
    'rel':     (0, 0.70, 0.85, 0.95, 1.0),
}
SEV_STOPS = (0.0, 0.5, 0.75, 0.9, 1.0)


def sev(kind, v):
    if v is None or v != v:
        return None
    if kind == 'ping' and v < 0:
        return 1.0
    t = THRESH[kind]
    if v <= t[0]:
        return 0.0
    for i in range(1, 5):
        if v <= t[i]:
            return SEV_STOPS[i - 1] + (SEV_STOPS[i] - SEV_STOPS[i - 1]) * (v - t[i - 1]) / (t[i] - t[i - 1])
    return 1.0


def tier_index(s):
    if s is None:
        return -1
    if s < 0.5: return 0
    if s < 0.75: return 1
    if s < 0.9: return 2
    return 3


TIER_SEV = (0.36, 0.60, 0.82, 0.95)


def tier_color(t):
    return MUTED if t < 0 else ramp(TIER_SEV[t])


def tier_name(kind, s):
    t = tier_index(s)
    if t < 0:
        return ''
    if kind == 'load':
        return ('NORMAL', 'BUSY', 'HEAVY', 'MAX')[t]
    if kind == 'ping':
        return ('GOOD', 'FAIR', 'SLOW', 'BAD')[t]
    return ('SAFE', 'WATCH', 'HIGH', 'CRITICAL')[t]


# theme
C_BG = rgb(10, 13, 19)
C_HEAD = rgb(18, 24, 34)
C_PANEL = rgb(16, 21, 29)
C_GRAPH = rgb(11, 15, 21)
C_BORDER = rgb(42, 52, 66)
C_GRID = rgb(27, 35, 46)
C_TRACK = rgb(30, 38, 50)
C_TEXT = rgb(226, 233, 241)
C_MUTED = rgb(122, 136, 152)
C_DIM = rgb(78, 90, 106)
C_ACCENT = rgb(96, 186, 255)


def to256(c):
    r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255

    def q(v):
        return 0 if v < 48 else (1 if v < 115 else (v - 35) // 40)
    levels = (0, 95, 135, 175, 215, 255)
    qr, qg, qb = q(r), q(g), q(b)
    cr, cg, cb = levels[qr], levels[qg], levels[qb]
    ci = 16 + 36 * qr + 6 * qg + qb
    avg = (r + g + b) // 3
    gi = 0 if avg < 8 else min(23, (avg - 8) // 10)
    gv = 8 + 10 * gi
    dc = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
    dg = (r - gv) ** 2 + (g - gv) ** 2 + (b - gv) ** 2
    return 232 + gi if dg < dc else ci


# ----------------------------------------------------------------------------- series
class Series(object):
    __slots__ = ('buf', 'live', 'target')

    def __init__(self, cap=2400):
        self.buf = collections.deque(maxlen=cap)
        self.live = None
        self.target = None

    def push(self, v):
        self.buf.append(v)

    def recent(self, n):
        return list(itertools.islice(reversed(self.buf), max(0, n)))

    def ease(self, a):
        t = self.target
        if t is None:
            self.live = None
            return
        cur = self.live
        if cur is None or t < 0 or cur < 0:
            self.live = t
            return
        self.live = cur + (t - cur) * a

    def max_recent(self, n):
        m = self.live if (self.live is not None and self.live > 0) else 0.0
        for v in itertools.islice(reversed(self.buf), n):
            if v is not None and v > m:
                m = v
        return m


# ----------------------------------------------------------------------------- screen
BLOCKS_V = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']
BLOCKS_H = [' ', '▏', '▎', '▍', '▌', '▋', '▊', '▉', '█']


class Screen(object):
    def __init__(self, truecolor):
        self.truecolor = truecolor
        self.W = self.H = 1
        self._fg_memo = {}
        self._bg_memo = {}
        self._shade_memo = {}
        self.resize(80, 24)

    def resize(self, w, h):
        self.W, self.H = max(1, w), max(1, h)
        n = self.W * self.H
        self.ch = [' '] * n
        self.fg = [0] * n
        self.bg = [0] * n
        self.pch = [None] * n
        self.pfg = [None] * n
        self.pbg = [None] * n
        self.full = True

    def clear(self, b):
        n = self.W * self.H
        self.ch = [' '] * n
        self.fg = [0] * n
        self.bg = [b] * n

    def put(self, x, y, c, f, b=-1):
        if 0 <= x < self.W and 0 <= y < self.H:
            i = y * self.W + x
            self.ch[i] = c
            if f >= 0: self.fg[i] = f
            if b >= 0: self.bg[i] = b

    def text(self, x, y, s, f, b=-1, maxw=None):
        if not s:
            return x
        if maxw is not None:
            if maxw <= 0:
                return x
            s = s[:maxw]
        if 0 <= y < self.H:
            W = self.W
            base = y * W
            ch, fg, bg = self.ch, self.fg, self.bg
            for k, c in enumerate(s):
                xx = x + k
                if 0 <= xx < W:
                    i = base + xx
                    ch[i] = c
                    if f >= 0: fg[i] = f
                    if b >= 0: bg[i] = b
        return x + len(s)

    def fill(self, x, y, w, h, b):
        for yy in range(max(0, y), min(self.H, y + h)):
            base = yy * self.W
            for xx in range(max(0, x), min(self.W, x + w)):
                i = base + xx
                self.ch[i] = ' '
                self.fg[i] = 0
                self.bg[i] = b

    def panel(self, x, y, w, h, title, title_fg=C_TEXT):
        if w < 4 or h < 2:
            return
        self.fill(x, y, w, h, C_PANEL)
        for xx in range(x + 1, x + w - 1):
            self.put(xx, y, '─', C_BORDER)
            self.put(xx, y + h - 1, '─', C_BORDER)
        for yy in range(y + 1, y + h - 1):
            self.put(x, yy, '│', C_BORDER)
            self.put(x + w - 1, yy, '│', C_BORDER)
        self.put(x, y, '╭', C_BORDER)
        self.put(x + w - 1, y, '╮', C_BORDER)
        self.put(x, y + h - 1, '╰', C_BORDER)
        self.put(x + w - 1, y + h - 1, '╯', C_BORDER)
        if title:
            self.text(x + 2, y, ' ' + title + ' ', title_fg, C_PANEL, w - 4)

    def _shade(self, col, f, gbg):
        key = (col, int(f * 24), gbg)
        v = self._shade_memo.get(key)
        if v is None:
            v = mix(gbg, col, int(f * 24) / 24.0)
            if len(self._shade_memo) > 20000:
                self._shade_memo.clear()
            self._shade_memo[key] = v
        return v

    def area(self, x, y, w, h, series, scale, kind, tick, gbg=C_GRAPH, grid=C_GRID):
        """Scrolling area graph: live value at the right edge, history moves left.
        Each column is colored by the shared scale for ITS OWN value."""
        if w < 1 or h < 1:
            return
        if not scale or scale <= 0 or scale != scale:
            scale = 1.0
        W, H = self.W, self.H
        ch, fg, bg = self.ch, self.fg, self.bg
        vals = [series.live] + series.recent(w - 1)
        nvals = len(vals)
        hg = set((h // 4, h // 2, (3 * h) // 4)) if h >= 3 else set()
        red = ramp(1.0)
        red_bot = mix(gbg, red, 0.22)
        red_col = mix(gbg, red, 0.08)
        for c in range(w):
            xx = x + c
            if xx < 0 or xx >= W:
                continue
            k = w - 1 - c
            v = vals[k] if k < nvals else None
            vgrid = ((tick - k) % 10) == 0
            has = v is not None and v == v
            timeout = has and v < 0
            if has and not timeout:
                frac = max(0.0, min(1.0, v / scale))
                s = sev('rel', frac * 0.8) if kind == 'rel' else sev(kind, v)
                col = ramp(s)
                total = frac * h * 8.0
                if v > 0 and total < 1:
                    total = 1.0
            else:
                total = 0.0
                col = MUTED
            for r in range(h):
                yy = y + h - 1 - r
                if yy < 0 or yy >= H:
                    continue
                i = yy * W + xx
                if timeout:
                    if r == 0:
                        ch[i] = '×'; fg[i] = red; bg[i] = red_bot
                    else:
                        ch[i] = ' '; fg[i] = 0; bg[i] = red_col
                    continue
                lo = r * 8.0
                bg[i] = gbg
                if has and total >= lo + 8:
                    f = 0.26 + 0.74 * ((lo + 8) / total) ** 1.5
                    ch[i] = '█'; fg[i] = self._shade(col, f, gbg)
                elif has and total > lo:
                    p = int(round(total - lo))
                    if p < 1: p = 1
                    if p > 8: p = 8
                    ch[i] = BLOCKS_V[p]; fg[i] = col
                else:
                    row_top = h - 1 - r
                    hgrid = row_top in hg
                    if vgrid and hgrid:
                        ch[i] = '┼'; fg[i] = grid
                    elif vgrid:
                        ch[i] = '│'; fg[i] = grid
                    elif hgrid:
                        ch[i] = '─'; fg[i] = grid
                    else:
                        ch[i] = ' '; fg[i] = 0

    def meter(self, x, y, w, v, vmax, kind, track=C_TRACK):
        """Gauge with 1/8-cell resolution; each cell has the color of the severity at its position."""
        if w < 1:
            return
        frac = 0.0 if (v is None or v != v or not vmax or vmax <= 0) else max(0.0, min(1.0, v / vmax))
        cells = frac * w
        for c in range(w):
            pos = (c + 0.5) / w
            colr = ramp(sev('rel', pos * 0.8) if kind == 'rel' else sev(kind, pos * vmax))
            if cells >= c + 1:
                self.put(x + c, y, '█', colr, track)
            elif cells > c:
                p = int(round((cells - c) * 8))
                self.put(x + c, y, BLOCKS_H[max(0, min(8, p))], colr, track)
            else:
                self.put(x + c, y, ' ', 0, track)

    def legend(self, x, y, w):
        for c in range(w):
            self.put(x + c, y, '▄', ramp(c / float(max(1, w - 1))))

    def _sgr_fg(self, c):
        s = self._fg_memo.get(c)
        if s is None:
            if self.truecolor:
                s = '\x1b[38;2;%d;%d;%dm' % ((c >> 16) & 255, (c >> 8) & 255, c & 255)
            else:
                s = '\x1b[38;5;%dm' % to256(c)
            self._fg_memo[c] = s
        return s

    def _sgr_bg(self, c):
        s = self._bg_memo.get(c)
        if s is None:
            if self.truecolor:
                s = '\x1b[48;2;%d;%d;%dm' % ((c >> 16) & 255, (c >> 8) & 255, c & 255)
            else:
                s = '\x1b[48;5;%dm' % to256(c)
            self._bg_memo[c] = s
        return s

    def flush(self, out):
        """Write only the cells that changed since the last frame."""
        parts = []
        W = self.W
        full = self.full
        if full:
            parts.append('\x1b[0m\x1b[2J')
        ch, fg, bg, pch, pfg, pbg = self.ch, self.fg, self.bg, self.pch, self.pfg, self.pbg
        cf = cb = None
        for y in range(self.H):
            a = y * W
            b = a + W
            if not full and ch[a:b] == pch[a:b] and bg[a:b] == pbg[a:b] and fg[a:b] == pfg[a:b]:
                continue
            cx = -1
            for i in range(a, b):
                c = ch[i]
                if not full and c == pch[i] and bg[i] == pbg[i] and (c == ' ' or fg[i] == pfg[i]):
                    continue
                if cx != i:
                    parts.append('\x1b[%d;%dH' % (y + 1, i - a + 1))
                bb = bg[i]
                if bb != cb:
                    parts.append(self._sgr_bg(bb)); cb = bb
                if c != ' ':
                    ff = fg[i]
                    if ff != cf:
                        parts.append(self._sgr_fg(ff)); cf = ff
                parts.append(c)
                cx = i + 1
            pch[a:b] = ch[a:b]
            pfg[a:b] = fg[a:b]
            pbg[a:b] = bg[a:b]
        self.full = False
        if parts:
            parts.append('\x1b[0m')
            try:
                out.write(''.join(parts).encode('utf-8'))
                out.flush()
            except Exception:
                self.full = True


# ----------------------------------------------------------------------------- helpers
def run(cmd, timeout=3.0):
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout, universal_newlines=True).stdout
    except Exception:
        return ''


def sysctl(name):
    return run(['sysctl', '-n', name]).strip()


def to_float(s):
    try:
        return float(s)
    except Exception:
        return None


CHILDREN = []


def spawn_pty(cmd):
    """Start a command on a pseudo-terminal so its output is line-buffered."""
    master, slave = pty.openpty()
    try:
        tty.setraw(slave)
    except Exception:
        pass
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.DEVNULL, close_fds=True)
    os.close(slave)
    CHILDREN.append(p)
    return p, master


def read_lines(master, proc, on_line, sep=b'\n'):
    buf = b''
    while S['run']:
        try:
            r, _, _ = select.select([master], [], [], 0.5)
        except Exception:
            break
        if not r:
            if proc.poll() is not None:
                break
            continue
        try:
            data = os.read(master, 65536)
        except OSError:
            break
        if not data:
            break
        buf += data
        while sep in buf:
            line, buf = buf.split(sep, 1)
            try:
                on_line(line)
            except Exception:
                pass
    try:
        os.close(master)
    except Exception:
        pass


S = {'run': True}          # shared state written by sampler threads (simple assignments only)
PING_Q = collections.deque()


# ----------------------------------------------------------------------------- samplers
def cpu_sampler():
    """Per-core CPU ticks via Mach host_processor_info (no subprocess, cheap)."""
    try:
        L = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        L.mach_host_self.restype = ctypes.c_uint
        L.host_processor_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                                          ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_uint)]
        L.host_processor_info.restype = ctypes.c_int
        L.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
        L.vm_deallocate.restype = ctypes.c_int
        task = ctypes.c_uint.in_dll(L, 'mach_task_self_').value
        host = L.mach_host_self()
    except Exception:
        S['cpu_src'] = 'unavailable'
        return

    def read():
        ncpu = ctypes.c_uint(0)
        info = ctypes.POINTER(ctypes.c_int)()
        icnt = ctypes.c_uint(0)
        if L.host_processor_info(host, 2, ctypes.byref(ncpu), ctypes.byref(info), ctypes.byref(icnt)) != 0:
            return None
        n = ncpu.value
        ticks = [[info[i * 4 + j] & 0xffffffff for j in range(4)] for i in range(n)]
        L.vm_deallocate(task, ctypes.cast(info, ctypes.c_void_p).value, icnt.value * 4)
        return ticks

    prev = read()
    S['cpu_src'] = 'Mach host_processor_info'
    while S['run']:
        time.sleep(0.25)
        cur = read()
        if not cur or not prev or len(cur) != len(prev):
            prev = cur
            continue
        cores = []
        tb = tt = 0
        for a, b in zip(prev, cur):
            d = [(b[j] - a[j]) & 0xffffffff for j in range(4)]
            busy = d[0] + d[1] + d[3]
            tot = busy + d[2]
            cores.append(100.0 * busy / tot if tot else 0.0)
            tb += busy
            tt += tot
        S['cpu'] = 100.0 * tb / tt if tt else 0.0
        S['cores'] = cores
        prev = cur


def mem_sampler():
    page = to_float(sysctl('hw.pagesize')) or 16384.0
    total = to_float(sysctl('hw.memsize')) or 0.0
    S['ram_total'] = total / 2 ** 30 if total else None
    while S['run']:
        out = run(['vm_stat'])
        vals = {}
        for line in out.splitlines():
            m = re.match(r'\s*(.+?):\s+(\d+)\.?\s*$', line)
            if m:
                vals[m.group(1)] = float(m.group(2))
        m = re.search(r'page size of (\d+) bytes', out)
        if m:
            page = float(m.group(1))
        if vals and total:
            wired = vals.get('Pages wired down', 0)
            comp = vals.get('Pages occupied by compressor', 0)
            if 'Anonymous pages' in vals:
                app = vals['Anonymous pages'] - vals.get('Pages purgeable', 0)
            else:
                app = vals.get('Pages active', 0) + vals.get('Pages inactive', 0) - vals.get('Pages purgeable', 0)
            used = (app + wired + comp) * page
            S['ram_used'] = used / 2 ** 30
            S['ram_pct'] = max(0.0, min(100.0, 100.0 * used / total))
            S['ram_cached'] = vals.get('File-backed pages', 0) * page / 2 ** 30
            S['ram_comp'] = comp * page / 2 ** 30
        lines = run(['sysctl', '-n', 'kern.memorystatus_vm_pressure_level', 'vm.swapusage']).splitlines()
        if lines:
            lv = to_float(lines[0].strip())
            S['pressure'] = {1: 'normal', 2: 'warning', 4: 'critical'}.get(int(lv) if lv else 0)
        if len(lines) > 1:
            m = re.search(r'used = ([\d.]+)M', lines[1])
            if m:
                S['swap_used'] = float(m.group(1)) / 1024.0
        time.sleep(1.0)


def net_sampler():
    iface = None
    prev = None
    n = 0
    while S['run']:
        if n % 20 == 0 or not iface:
            m = re.search(r'interface:\s*(\S+)', run(['route', '-n', 'get', 'default']))
            new = m.group(1) if m else None
            if new != iface:
                prev = None
            iface = new
            S['nic'] = iface
        n += 1
        if iface:
            out = run(['netstat', '-ibn', '-I', iface])
            rx = tx = None
            for line in out.splitlines():
                cols = line.split()
                if len(cols) >= 10 and cols[0].rstrip('*') == iface and cols[2].startswith('<Link'):
                    try:
                        rx, tx = float(cols[-5]), float(cols[-2])
                    except ValueError:
                        pass
                    break
            now = time.time()
            if rx is not None:
                if prev:
                    dt = now - prev[0]
                    if dt > 0:
                        S['down'] = max(0.0, (rx - prev[1]) / dt / 2 ** 20)
                        S['up'] = max(0.0, (tx - prev[2]) / dt / 2 ** 20)
                prev = (now, rx, tx)
        else:
            S['down'] = S['up'] = None
        time.sleep(0.5)


def disk_sampler():
    try:
        proc, master = spawn_pty(['iostat', '-d', '-w', '1'])
    except Exception:
        return
    state = {'first': True}

    def on_line(raw):
        nums = raw.decode('utf-8', 'replace').split()
        try:
            vals = [float(v) for v in nums]
        except ValueError:
            return
        if not vals or len(vals) % 3:
            return
        if state['first']:
            state['first'] = False   # first row is the since-boot average
            return
        S['disk_mbs'] = sum(vals[2::3])
    read_lines(master, proc, on_line)


def gpu_sampler():
    """GPU utilization + GPU memory from IOAccelerator (no sudo needed)."""
    arm = platform.machine() == 'arm64'
    ram = to_float(sysctl('hw.memsize')) or 0.0
    wired_mb = to_float(sysctl('iogpu.wired_limit_mb')) or 0.0
    if arm and ram:
        usable = wired_mb * 2 ** 20 if wired_mb > 0 else ram * (0.75 if ram > 36 * 2 ** 30 else 0.67)
        S['gpu_mem_total'] = usable / 2 ** 30
    S['gpu_mem_label'] = 'GPU MEM' if arm else 'VRAM'
    cores = re.search(r'"gpu-core-count"\s*=\s*(\d+)', run(['ioreg', '-r', '-d', '1', '-w', '0', '-c', 'IOAccelerator']))
    if cores:
        S['gpu_cores'] = int(cores.group(1))
    while S['run']:
        out = run(['ioreg', '-r', '-d', '1', '-w', '0', '-c', 'IOAccelerator'])
        utils = [float(v) for v in re.findall(r'"Device Utilization %"\s*=\s*(\d+)', out)]
        if not utils:
            utils = [float(v) for v in re.findall(r'"GPU Activity\(%\)"\s*=\s*(\d+)', out)]
        if utils:
            S['gpu'] = max(0.0, min(100.0, max(utils)))
            S['gpu_src'] = 'IOAccelerator'
        elif S.get('pm_gpu') is not None:
            S['gpu'] = S['pm_gpu']
            S['gpu_src'] = 'powermetrics'
        else:
            S['gpu'] = None
            S['gpu_src'] = 'unavailable'
        if arm:
            used = [float(v) for v in re.findall(r'"In use system memory"\s*=\s*(\d+)', out)]
            S['gpu_mem_used'] = max(used) / 2 ** 30 if used else None
        else:
            vu = re.findall(r'"vramUsedBytes"\s*=\s*(\d+)', out)
            vf = re.findall(r'"vramFreeBytes"\s*=\s*(\d+)', out)
            if vu:
                S['gpu_mem_used'] = float(vu[0]) / 2 ** 30
                if vf:
                    S['gpu_mem_total'] = (float(vu[0]) + float(vf[0])) / 2 ** 30
        u, t = S.get('gpu_mem_used'), S.get('gpu_mem_total')
        S['gpu_mem_pct'] = min(100.0, 100.0 * u / t) if (u is not None and t) else None
        time.sleep(0.5)


class HIDTemps(object):
    """Apple Silicon temperature sensors via IOKit HID (no sudo needed)."""

    def __init__(self):
        vp = ctypes.c_void_p
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        io = ctypes.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
        cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = vp
        cf.CFNumberCreate.argtypes = [vp, ctypes.c_long, vp]
        cf.CFNumberCreate.restype = vp
        cf.CFDictionaryCreate.argtypes = [vp, ctypes.POINTER(vp), ctypes.POINTER(vp), ctypes.c_long, vp, vp]
        cf.CFDictionaryCreate.restype = vp
        cf.CFArrayGetCount.argtypes = [vp]
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetValueAtIndex.argtypes = [vp, ctypes.c_long]
        cf.CFArrayGetValueAtIndex.restype = vp
        cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [vp]
        cf.CFRelease.restype = None
        io.IOHIDEventSystemClientCreate.argtypes = [vp]
        io.IOHIDEventSystemClientCreate.restype = vp
        io.IOHIDEventSystemClientSetMatching.argtypes = [vp, vp]
        io.IOHIDEventSystemClientSetMatching.restype = ctypes.c_int
        io.IOHIDEventSystemClientCopyServices.argtypes = [vp]
        io.IOHIDEventSystemClientCopyServices.restype = vp
        io.IOHIDServiceClientCopyProperty.argtypes = [vp, vp]
        io.IOHIDServiceClientCopyProperty.restype = vp
        io.IOHIDServiceClientCopyEvent.argtypes = [vp, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]
        io.IOHIDServiceClientCopyEvent.restype = vp
        io.IOHIDEventGetFloatValue.argtypes = [vp, ctypes.c_int32]
        io.IOHIDEventGetFloatValue.restype = ctypes.c_double
        self.cf, self.io = cf, io
        enc = 0x08000100

        def cfstr(s):
            return cf.CFStringCreateWithCString(None, s.encode('utf-8'), enc)

        page = ctypes.c_int32(0xff00)
        usage = ctypes.c_int32(5)
        keys = (vp * 2)(cfstr('PrimaryUsagePage'), cfstr('PrimaryUsage'))
        vals = (vp * 2)(cf.CFNumberCreate(None, 3, ctypes.addressof(page)),
                        cf.CFNumberCreate(None, 3, ctypes.addressof(usage)))
        kcb = ctypes.addressof(ctypes.c_char.in_dll(cf, 'kCFTypeDictionaryKeyCallBacks'))
        vcb = ctypes.addressof(ctypes.c_char.in_dll(cf, 'kCFTypeDictionaryValueCallBacks'))
        match = cf.CFDictionaryCreate(None, keys, vals, 2, kcb, vcb)
        self.client = io.IOHIDEventSystemClientCreate(None)
        if not self.client:
            raise RuntimeError('no HID client')
        io.IOHIDEventSystemClientSetMatching(self.client, match)
        services = io.IOHIDEventSystemClientCopyServices(self.client)
        if not services:
            raise RuntimeError('no HID services')
        product = cfstr('Product')
        self.services = []
        buf = ctypes.create_string_buffer(256)
        for i in range(cf.CFArrayGetCount(services)):
            svc = cf.CFArrayGetValueAtIndex(services, i)
            name = ''
            prop = io.IOHIDServiceClientCopyProperty(svc, product)
            if prop:
                if cf.CFStringGetCString(prop, buf, 256, enc):
                    name = buf.value.decode('utf-8', 'replace')
                cf.CFRelease(prop)
            self.services.append((name, svc))
        self._services_ref = services   # keep the array (and its services) alive
        if not self.services:
            raise RuntimeError('no temperature sensors')

    def read(self):
        out = {}
        for name, svc in self.services:
            ev = self.io.IOHIDServiceClientCopyEvent(svc, 15, 0, 0)
            if not ev:
                continue
            v = self.io.IOHIDEventGetFloatValue(ev, 15 << 16)
            self.cf.CFRelease(ev)
            if 0 < v < 150:
                out.setdefault(name, []).append(v)
        return out


def temp_sampler():
    hid = None
    try:
        hid = HIDTemps()
        S['temp_src'] = 'IOKit HID sensors'
    except Exception:
        S['temp_src'] = 'powermetrics (sudo)' if os.geteuid() == 0 else 'unavailable'
    while S['run'] and hid:
        try:
            vals = hid.read()
        except Exception:
            break

        def avg(names):
            xs = [v for n in names for v in vals[n]]
            return sum(xs) / len(xs) if xs else None
        cpu_names = [n for n in vals if ('pACC' in n or 'eACC' in n or 'PACC' in n or 'EACC' in n)]
        if not cpu_names:
            cpu_names = [n for n in vals if 'tdie' in n.lower() or n.upper().startswith('CPU')]
        gpu_names = [n for n in vals if 'GPU' in n.upper()]
        S['cpu_temp'] = avg(cpu_names)
        S['gpu_temp'] = avg(gpu_names)
        time.sleep(1.0)


LM_RE = re.compile(r'(LM Studio|LMStudio|lmstudio|/\.lmstudio/|llmster|llama-server|llama-box|lm-studio|(^|/)lms$)', re.I)


def parse_cputime(s):
    days = 0
    if '-' in s:
        d, s = s.split('-', 1)
        days = int(d)
    secs = 0.0
    for part in s.split(':'):
        secs = secs * 60 + float(part)
    return days * 86400 + secs


def lm_sampler():
    ncpu = os.cpu_count() or 1
    prev = {}
    prev_t = None
    while S['run']:
        out = run(['ps', '-axo', 'pid=,rss=,time=,comm='])
        now = time.time()
        cur = {}
        ram = 0.0
        pids = []
        for line in out.splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 4:
                continue
            if not LM_RE.search(parts[3]):
                continue
            try:
                pid = int(parts[0])
                rss = float(parts[1]) * 1024
                cpu_s = parse_cputime(parts[2])
            except ValueError:
                continue
            cur[pid] = cpu_s
            ram += rss
            pids.append(pid)
        delta = 0.0
        valid = False
        for pid, t in cur.items():
            if pid in prev:
                delta += max(0.0, t - prev[pid])
                valid = True
        if valid and prev_t and now > prev_t:
            S['lm_cpu'] = min(100.0, 100.0 * delta / ((now - prev_t) * ncpu))
        elif not cur:
            S['lm_cpu'] = None
        prev, prev_t = cur, now
        S['lm_count'] = len(cur)
        S['lm_ram'] = ram / 2 ** 30
        S['lm_pids'] = set(pids)
        time.sleep(1.0)


def ping_sampler(host):
    def start(interval):
        return spawn_pty(['ping', '-i', interval, '-W', '1000', host])

    def on_line(raw):
        line = raw.decode('utf-8', 'replace')
        m = re.search(r'time[=<]([\d.]+)\s*ms', line)
        if m:
            PING_Q.append(float(m.group(1)))
        elif 'timeout' in line.lower() or 'unreachable' in line.lower() or 'no route' in line.lower():
            PING_Q.append(-1.0)

    for interval in ('0.25', '1'):
        if not S['run']:
            return
        try:
            proc, master = start(interval)
        except Exception:
            return
        t0 = time.time()
        read_lines(master, proc, on_line)
        if not S['run']:
            return
        if time.time() - t0 > 5:      # ran fine then died: retry same interval
            time.sleep(1)
            return ping_sampler(host)
    S['ping_err'] = 'ping failed to start'


def power_sampler():
    """Only when run with sudo: CPU/GPU/ANE power, thermal pressure, per-process GPU time."""
    if os.geteuid() != 0:
        return
    cmd = ['powermetrics', '--samplers', 'cpu_power,gpu_power,thermal,tasks', '--show-process-gpu',
           '-i', '1000', '-f', 'plist']
    if platform.machine() != 'arm64':
        cmd = ['powermetrics', '--samplers', 'smc,cpu_power,gpu_power,thermal,tasks', '-i', '1000', '-f', 'plist']
    try:
        proc, master = spawn_pty(cmd)
    except Exception:
        return
    S['power_src'] = 'powermetrics'

    def on_sample(raw):
        raw = raw.strip()
        if not raw:
            return
        d = plistlib.loads(raw)
        p = d.get('processor', {}) or {}
        g = d.get('gpu', {}) or {}

        def mw(*vals):
            for v in vals:
                if isinstance(v, (int, float)):
                    return v / 1000.0
            return None
        S['cpu_w'] = mw(p.get('cpu_power'), p.get('package_watts') and p['package_watts'] * 1000)
        S['gpu_w'] = mw(p.get('gpu_power'), g.get('gpu_power'))
        S['ane_w'] = mw(p.get('ane_power'))
        S['sys_w'] = mw(p.get('combined_power'))
        if isinstance(g.get('idle_ratio'), (int, float)):
            S['pm_gpu'] = max(0.0, min(100.0, 100.0 * (1.0 - g['idle_ratio'])))
        if d.get('thermal_pressure'):
            S['thermal_pressure'] = str(d['thermal_pressure'])
        smc = d.get('smc', {}) or {}
        if S.get('cpu_temp') is None and isinstance(smc.get('cpu_die'), (int, float)):
            S['cpu_temp'] = float(smc['cpu_die'])
        if S.get('gpu_temp') is None and isinstance(smc.get('gpu_die'), (int, float)):
            S['gpu_temp'] = float(smc['gpu_die'])
        lm = S.get('lm_pids') or set()
        share = 0.0
        found = False
        for t in d.get('tasks', []) or []:
            if t.get('pid') in lm:
                gt = t.get('gputime_ms_per_s')
                if isinstance(gt, (int, float)):
                    share += gt / 10.0
                    found = True
        S['lm_gpu'] = min(100.0, share) if found else (0.0 if lm else None)
    read_lines(master, proc, on_sample, sep=b'\x00')


# ----------------------------------------------------------------------------- dashboard
def fmt(v, f='%.0f'):
    return '--' if v is None or v != v else f % v


def pct(v):
    return 'N/A' if v is None or v != v else '%.0f%%' % v


def deg(v):
    return 'N/A' if v is None or v != v else '%.0f°C' % v


def split_heights(total, parts):
    base = total // parts
    rem = total - base * parts
    return [base + (1 if i < rem else 0) for i in range(parts)]


class Dashboard(object):
    KEYS = ['cpu', 'gpu', 'ram', 'gmem', 'down', 'up', 'cpuTemp', 'gpuTemp', 'lmCpu', 'lmGpu', 'lmRam']

    def __init__(self, args, screen):
        self.args = args
        self.scr = screen
        self.sr = dict((k, Series()) for k in self.KEYS + ['ping'])
        self.ping_raw = collections.deque(maxlen=60)
        self.tick = 0
        self.paused = False
        self.speed = args.speed
        self.net_scale = 1.0
        self.ping_scale = 50.0
        self.ping_timeout_flag = False
        self.fps = 0.0
        self.err = None
        self.cpu_name = sysctl('machdep.cpu.brand_string') or platform.machine()
        p0 = sysctl('hw.perflevel0.logicalcpu')
        p1 = sysctl('hw.perflevel1.logicalcpu')
        self.core_desc = ('%s P + %s E cores' % (p0, p1)) if (p0 and p1) else ('%d cores' % (os.cpu_count() or 1))
        self.root = os.geteuid() == 0

    # ---- per-frame state update
    def update(self, dt, now_ms):
        sr = self.sr
        sr['cpu'].target = S.get('cpu')
        sr['gpu'].target = S.get('gpu')
        sr['ram'].target = S.get('ram_pct')
        sr['gmem'].target = S.get('gpu_mem_pct')
        sr['down'].target = S.get('down')
        sr['up'].target = S.get('up')
        sr['cpuTemp'].target = S.get('cpu_temp')
        sr['gpuTemp'].target = S.get('gpu_temp')
        running = (S.get('lm_count') or 0) > 0
        sr['lmCpu'].target = S.get('lm_cpu') if running else 0.0
        sr['lmGpu'].target = S.get('lm_gpu') if running else 0.0
        rt = S.get('ram_total')
        sr['lmRam'].target = (100.0 * (S.get('lm_ram') or 0) / rt) if (running and rt) else 0.0
        while PING_Q:
            v = PING_Q.popleft()
            self.ping_raw.append(v)
            if v < 0:
                self.ping_timeout_flag = True
            sr['ping'].target = v
        a = 1.0 - math.exp(-dt / max(0.01, EASE_SECONDS))
        for k in self.KEYS:
            sr[k].ease(a)
        sr['ping'].live = sr['ping'].target
        nt = max(0.1, max(sr['down'].max_recent(400), sr['up'].max_recent(400)) * 1.25)
        self.net_scale += (nt - self.net_scale) * min(1.0, a * 0.6)
        pt = max(25.0, min(1000.0, math.ceil(sr['ping'].max_recent(400) * 1.25 / 25.0) * 25.0))
        self.ping_scale += (pt - self.ping_scale) * min(1.0, a * 0.6)

    def scroll(self):
        if self.paused:
            return
        for k in self.KEYS:
            self.sr[k].push(self.sr[k].live)
        self.sr['ping'].push(-1.0 if self.ping_timeout_flag else self.sr['ping'].live)
        self.ping_timeout_flag = False
        self.tick += 1

    # ---- drawing helpers
    def right(self, x, y, pw, val, s, tier):
        s1 = ' %s ' % val
        s2 = (' %s ' % tier) if tier else ''
        rx = x + pw - 2 - len(s1) - len(s2)
        if rx < x + 8:
            return
        self.scr.text(rx, y, s1, ramp(s), C_PANEL)
        if s2:
            self.scr.text(rx + len(s1), y, s2, C_BG, tier_color(tier_index(s)))

    def axis(self, gx, gy, gh, top, mid):
        t = self.scr.text
        t(gx - 5, gy, '%4s' % top, C_DIM, C_PANEL)
        if gh >= 4:
            t(gx - 5, gy + gh // 2, '%4s' % mid, C_DIM, C_PANEL)
        if gh >= 2:
            t(gx - 5, gy + gh - 1, '   0', C_DIM, C_PANEL)

    def detail(self, x, y, pw, ph, s):
        self.scr.text(x + 2, y + ph - 2, s, C_MUTED, C_PANEL, pw - 4)

    def load_panel(self, x, y, pw, ph, title, key, kind, scale=100.0):
        scr = self.scr
        scr.panel(x, y, pw, ph, title)
        v = self.sr[key].live
        s = sev(kind, v)
        self.right(x, y, pw, pct(v), s, tier_name(kind, s))
        gx, gy, gw, gh = x + 6, y + 1, pw - 8, ph - 3
        if gh >= 1 and gw >= 4:
            scr.area(gx, gy, gw, gh, self.sr[key], scale, kind, self.tick)
            self.axis(gx, gy, gh, '100', '50')
        return max(4, gw)

    def meter_row(self, x, y, pw, label, v, vmax, kind, text, text_sev):
        scr = self.scr
        scr.text(x + 2, y, label, C_MUTED, C_PANEL)
        tw = 12
        mw = pw - 8 - tw - 2
        if mw >= 4:
            scr.meter(x + 8, y, mw, v, vmax, kind)
        scr.text(x + pw - 2 - tw, y, '%11s' % text, ramp(text_sev), C_PANEL)

    # ---- panels
    def p_cpu(self, x, y, pw, ph):
        gw = self.load_panel(x, y, pw, ph, 'CPU', 'cpu', 'load')
        scr = self.scr
        dy, end = y + ph - 2, x + pw - 2
        cores = S.get('cores') or []
        strip_w = len(cores) + 7 if cores else 0
        info = '%s   /   %s   /   window peak %s' % (self.cpu_name, self.core_desc, pct(self.sr['cpu'].max_recent(gw)))
        if self.root and S.get('cpu_w') is not None:
            info += '   /   %.1f W' % S['cpu_w']
        xx = scr.text(x + 2, dy, info, C_MUTED, C_PANEL, max(0, end - (x + 2) - strip_w - 2))
        if cores and end - strip_w > xx:
            cx = end - strip_w
            cx = scr.text(cx, dy, 'cores ', C_DIM, C_PANEL)
            for u in cores:
                scr.put(cx, dy, BLOCKS_V[max(1, min(8, int(round(u / 12.5))))], ramp(sev('load', u)), C_TRACK)
                cx += 1

    def p_gpu(self, x, y, pw, ph):
        self.load_panel(x, y, pw, ph, 'GPU', 'gpu', 'load')
        scr = self.scr
        dy, end = y + ph - 2, x + pw - 2
        label = S.get('gpu_mem_label', 'GPU MEM')
        xx = scr.text(x + 2, dy, label + ' ', C_MUTED, C_PANEL)
        mw = min(20, max(6, pw // 6))
        gm = self.sr['gmem'].live
        scr.meter(xx, dy, mw, gm, 100, 'vram')
        u, t = S.get('gpu_mem_used'), S.get('gpu_mem_total')
        if u is None:
            txt = 'N/A'
        elif t:
            txt = '%.1f / %.1f GiB  %s' % (u, t, pct(gm))
        else:
            txt = '%.1f GiB' % u
        xx = scr.text(xx + mw + 1, dy, txt, ramp(sev('vram', gm)), C_PANEL)
        if self.root and S.get('gpu_w') is not None:
            xx = scr.text(xx, dy, '   /   %.1f W' % S['gpu_w'], C_MUTED, C_PANEL, end - xx)
            if S.get('ane_w'):
                xx = scr.text(xx, dy, '  ANE %.1f W' % S['ane_w'], C_MUTED, C_PANEL, end - xx)
        name = self.cpu_name if platform.machine() == 'arm64' else ''
        if S.get('gpu_cores'):
            name = ('%s  %d-core GPU' % (name, S['gpu_cores'])).strip()
        if name:
            scr.text(xx, dy, '   /   ' + name, C_MUTED, C_PANEL, end - xx)

    def p_mem(self, x, y, pw, ph):
        self.load_panel(x, y, pw, ph, 'MEMORY', 'ram', 'ram')
        scr = self.scr
        dy, end = y + ph - 2, x + pw - 2
        u, t = S.get('ram_used'), S.get('ram_total')
        xx = scr.text(x + 2, dy, '%s / %s GiB used   /   cached %s   /   swap %s GiB   /   pressure ' % (
            fmt(u, '%.1f'), fmt(t, '%.0f'), fmt(S.get('ram_cached'), '%.1f'), fmt(S.get('swap_used'), '%.2f')),
            C_MUTED, C_PANEL, end - x - 2)
        pr = S.get('pressure')
        prs = {'normal': 0.36, 'warning': 0.7, 'critical': 0.95}.get(pr)
        xx = scr.text(xx, dy, (pr or '--'), ramp(prs), C_PANEL, end - xx)
        if S.get('disk_mbs') is not None:
            scr.text(xx, dy, '   /   disk %.1f MB/s' % S['disk_mbs'], C_MUTED, C_PANEL, end - xx)

    def p_net(self, x, y, pw, ph):
        scr = self.scr
        scr.panel(x, y, pw, ph, 'NETWORK')
        r = ' ↓%s   ↑%s MiB/s ' % (fmt(self.sr['down'].live, '%.2f'), fmt(self.sr['up'].live, '%.2f'))
        rx = x + pw - 2 - len(r)
        if rx > x + 12:
            scr.text(rx, y, r, C_TEXT, C_PANEL)
        gx, gy, gw, gh = x + 6, y + 1, pw - 8, ph - 3
        if gh >= 1 and gw >= 9:
            gl = (gw - 1) // 2
            gr = gw - 1 - gl
            scr.area(gx, gy, gl, gh, self.sr['down'], self.net_scale, 'rel', self.tick)
            scr.area(gx + gl + 1, gy, gr, gh, self.sr['up'], self.net_scale, 'rel', self.tick)
            scr.text(gx + 1, gy, '↓ DOWN', C_TEXT)
            scr.text(gx + gl + 2, gy, '↑ UP', C_TEXT)
            sc = self.net_scale
            f = '%.0f' if sc >= 10 else '%.1f'
            self.axis(gx, gy, gh, f % sc, f % (sc / 2))
        self.detail(x, y, pw, ph, '%s   /   scale %.1f MiB/s (auto)' % (S.get('nic') or 'no active interface', self.net_scale))

    def temp_row(self, x, y, pw, label, v, kind, vmax):
        scr = self.scr
        scr.text(x + 2, y, label, C_MUTED, C_PANEL)
        if v is None:
            scr.text(x + 8, y, 'sensor unavailable', C_DIM, C_PANEL, pw - 10)
            return
        s = sev(kind, v)
        scr.text(x + 8, y, '%-6s' % deg(v), ramp(s), C_PANEL)
        scr.text(x + 15, y, ' %s ' % tier_name(kind, s), C_BG, tier_color(tier_index(s)))
        mw = pw - 27 - 2
        if mw >= 4:
            scr.meter(x + 27, y, mw, v, vmax, kind)

    def p_thermal(self, x, y, pw, ph):
        scr = self.scr
        scr.panel(x, y, pw, ph, 'THERMALS')
        tp = S.get('thermal_pressure')
        if tp:
            tps = {'nominal': 0.36, 'moderate': 0.62, 'heavy': 0.82}.get(tp.lower(), 0.95)
            badge = ' %s ' % tp.upper()
            rx = x + pw - 2 - len(badge)
            if rx - 17 > x + 12:
                scr.text(rx - 17, y, ' thermal pressure', C_MUTED, C_PANEL)
                scr.text(rx, y, badge, C_BG, tier_color(tier_index(tps)))
        self.temp_row(x, y + 1, pw, 'CPU', self.sr['cpuTemp'].live, 'cpuTemp', 110)
        self.temp_row(x, y + 2, pw, 'GPU', self.sr['gpuTemp'].live, 'gpuTemp', 105)
        gx, gy, gw, gh = x + 6, y + 3, pw - 8, ph - 4
        if gh >= 2 and gw >= 9:
            gl = (gw - 1) // 2
            gr = gw - 1 - gl
            scr.area(gx, gy, gl, gh, self.sr['cpuTemp'], 110, 'cpuTemp', self.tick)
            scr.area(gx + gl + 1, gy, gr, gh, self.sr['gpuTemp'], 105, 'gpuTemp', self.tick)
            scr.text(gx + 1, gy, 'CPU °C', C_TEXT)
            scr.text(gx + gl + 2, gy, 'GPU °C', C_TEXT)
            self.axis(gx, gy, gh, '110', '55')

    def p_lm(self, x, y, pw, ph):
        scr = self.scr
        scr.panel(x, y, pw, ph, 'LM STUDIO')
        n = S.get('lm_count') or 0
        if n > 0:
            xx = scr.text(x + 2, y + 1, '● RUNNING', ramp(0.42), C_PANEL)
            scr.text(xx, y + 1, '   %d process%s' % (n, '' if n == 1 else 'es'), C_MUTED, C_PANEL, x + pw - 2 - xx)
        else:
            scr.text(x + 2, y + 1, '○ NOT DETECTED', C_DIM, C_PANEL)
        if ph < 7:
            return
        sr = self.sr
        lc, lg, lr = sr['lmCpu'].live, sr['lmGpu'].live, sr['lmRam'].live
        self.meter_row(x, y + 2, pw, 'CPU', lc, 100, 'load', pct(lc), sev('load', lc))
        if self.root:
            self.meter_row(x, y + 3, pw, 'GPU', lg, 100, 'load', pct(lg), sev('load', lg))
        else:
            scr.text(x + 2, y + 3, 'GPU', C_MUTED, C_PANEL)
            scr.text(x + 8, y + 3, 'per-app GPU share needs: sudo python3 ...', C_DIM, C_PANEL, pw - 10)
        self.meter_row(x, y + 4, pw, 'RAM', lr, 100, 'ram', '%s GiB' % fmt(S.get('lm_ram'), '%.2f'), sev('ram', sr['ram'].live))
        gm = sr['gmem'].live
        self.meter_row(x, y + 5, pw, 'GMEM', gm, 100, 'vram', pct(gm), sev('vram', gm))

    def p_ping(self, x, y, pw, ph):
        scr = self.scr
        scr.panel(x, y, pw, ph, 'PING  %s' % self.args.ping)
        last = self.sr['ping'].live
        s = sev('ping', last)
        lt = 'sampling' if last is None else ('TIMEOUT' if last < 0 else '%.0f ms' % last)
        self.right(x, y, pw, lt, s, tier_name('ping', s))
        ok = [v for v in self.ping_raw if v >= 0]
        avg = sum(ok) / len(ok) if ok else None
        mn = min(ok) if ok else None
        mx = max(ok) if ok else None
        jit = (sum(abs(a - b) for a, b in zip(ok[1:], ok[:-1])) / (len(ok) - 1)) if len(ok) > 1 else None
        loss = 100.0 * (len(self.ping_raw) - len(ok)) / len(self.ping_raw) if self.ping_raw else 0.0
        end = x + pw - 2
        t = scr.text
        xx = t(x + 2, y + 1, 'avg ', C_MUTED, C_PANEL)
        xx = t(xx, y + 1, fmt(avg, '%.1f'), ramp(sev('ping', avg)), C_PANEL)
        xx = t(xx, y + 1, '  min ', C_MUTED, C_PANEL)
        xx = t(xx, y + 1, fmt(mn), ramp(sev('ping', mn)), C_PANEL)
        xx = t(xx, y + 1, '  max ', C_MUTED, C_PANEL)
        xx = t(xx, y + 1, fmt(mx), ramp(sev('ping', mx)), C_PANEL)
        xx = t(xx, y + 1, '  jitter %s ms   loss ' % fmt(jit, '%.1f'), C_MUTED, C_PANEL, end - xx)
        ls = 0.3 if loss <= 0 else (0.6 if loss < 2 else (0.8 if loss < 10 else 0.95))
        t(xx, y + 1, '%.1f%%' % loss, ramp(ls), C_PANEL, end - xx)
        gx, gy, gw, gh = x + 6, y + 2, pw - 8, ph - 4
        if gh >= 2 and gw >= 4:
            scr.area(gx, gy, gw, gh, self.sr['ping'], self.ping_scale, 'ping', self.tick)
            self.axis(gx, gy, gh, '%.0f' % self.ping_scale, '%.0f' % (self.ping_scale / 2))
        ry = y + ph - 2
        room = pw - 12
        toks = []
        for v in reversed(self.ping_raw):
            tk = '×' if v < 0 else '%.0f' % v
            room -= len(tk) + 1
            if room < 0:
                break
            toks.append((tk, v))
        xx = t(x + 2, ry, 'recent ', C_DIM, C_PANEL)
        for tk, v in reversed(toks):
            xx = t(xx, ry, tk + ' ', ramp(sev('ping', v)), C_PANEL)

    # ---- full frame
    def render(self):
        scr = self.scr
        W, H = scr.W, scr.H
        scr.clear(C_BG)
        sr = self.sr
        # header
        scr.fill(0, 0, W, 1, C_HEAD)
        xx = scr.text(1, 0, '■', C_ACCENT, C_HEAD)
        xx = scr.text(xx + 1, 0, 'LM STUDIO', C_TEXT, C_HEAD)
        xx = scr.text(xx, 0, '  /  macOS PERFORMANCE', C_MUTED, C_HEAD)
        r = '%s   %d col/s   [+/-] speed   [P] pause   [Q] quit ' % (time.strftime('%H:%M:%S'), self.speed)
        rx = W - len(r)
        if rx > xx + 2:
            scr.text(rx, 0, r, C_MUTED, C_HEAD)
        if self.paused and rx > xx + 12:
            scr.text(rx - 10, 0, ' PAUSED ', C_BG, ramp(0.55))
        # chips + legend
        pv = sr['ping'].live
        pr = S.get('pressure')
        chips = [
            ('CPU', pct(sr['cpu'].live), sev('load', sr['cpu'].live)),
            ('GPU', pct(sr['gpu'].live), sev('load', sr['gpu'].live)),
            ('RAM', pct(sr['ram'].live), sev('ram', sr['ram'].live)),
            (S.get('gpu_mem_label', 'GPU MEM'), pct(sr['gmem'].live), sev('vram', sr['gmem'].live)),
            ('CPU TEMP', deg(sr['cpuTemp'].live), sev('cpuTemp', sr['cpuTemp'].live)),
            ('GPU TEMP', deg(sr['gpuTemp'].live), sev('gpuTemp', sr['gpuTemp'].live)),
            ('PING', '--' if pv is None else ('TIMEOUT' if pv < 0 else '%.0f ms' % pv), sev('ping', pv)),
            ('PRESSURE', pr or '--', {'normal': 0.36, 'warning': 0.7, 'critical': 0.95}.get(pr)),
        ]
        lw = 20
        lx0 = W - (lw + 17)
        cx = 1
        for label, val, s in chips:
            if cx + len(label) + len(val) + 4 > W:
                break
            cx = scr.text(cx, 1, label + ' ', C_MUTED)
            cx = scr.text(cx, 1, val, ramp(s)) + 3
        if cx < lx0:
            lx = scr.text(lx0, 1, 'SAFE ', ramp(0.36))
            scr.legend(lx, 1, lw)
            scr.text(lx + lw, 1, ' CRITICAL', ramp(0.95))
        # body
        by, bh = 3, H - 4
        if W >= 110 and bh >= 26:
            lw2 = int((W - 3) * 0.62)
            rxp = 1 + lw2 + 1
            rw = W - rxp - 1
            hs = split_heights(bh, 4)
            yy = by
            self.p_cpu(1, yy, lw2, hs[0]); yy += hs[0]
            self.p_gpu(1, yy, lw2, hs[1]); yy += hs[1]
            self.p_mem(1, yy, lw2, hs[2]); yy += hs[2]
            self.p_net(1, yy, lw2, hs[3])
            lmh = 8
            thh = max(9, int(bh * 0.34))
            pgh = bh - thh - lmh
            if pgh < 8:
                thh = max(6, bh - lmh - 8)
                pgh = bh - thh - lmh
            self.p_thermal(rxp, by, rw, thh)
            self.p_lm(rxp, by + thh, rw, lmh)
            self.p_ping(rxp, by + thh + lmh, rw, pgh)
        elif bh >= 10:
            pw = W - 2
            hs = split_heights(bh, 5)
            yy = by
            self.p_cpu(1, yy, pw, hs[0]); yy += hs[0]
            self.p_gpu(1, yy, pw, hs[1]); yy += hs[1]
            self.p_mem(1, yy, pw, hs[2]); yy += hs[2]
            self.p_net(1, yy, pw, hs[3]); yy += hs[3]
            self.p_ping(1, yy, pw, hs[4])
        else:
            scr.text(2, by, 'Window too small - enlarge it (or go full screen) to see the graphs.', C_MUTED)
        # footer
        scr.fill(0, H - 1, W, 1, C_HEAD)
        if self.err:
            scr.text(1, H - 1, 'render error: ' + self.err, ramp(0.95), C_HEAD, W - 2)
        else:
            extra = '' if self.root else '   /   sudo adds power + per-app GPU'
            f = 'GPU: %s   /   temps: %s   /   %s   /   %.0f fps%s' % (
                S.get('gpu_src', 'starting'), S.get('temp_src', 'starting'),
                'truecolor' if scr.truecolor else '256-color', self.fps, extra)
            scr.text(1, H - 1, f, C_DIM, C_HEAD, W - 2)


# ----------------------------------------------------------------------------- main
def detect_truecolor(args):
    if args.truecolor:
        return True
    if args.color256:
        return False
    if os.environ.get('COLORTERM', '').lower() in ('truecolor', '24bit'):
        return True
    prog = os.environ.get('TERM_PROGRAM', '')
    if prog in ('iTerm.app', 'WezTerm', 'vscode', 'ghostty', 'WarpTerminal', 'Hyper', 'Tabby'):
        return True
    if prog == 'Apple_Terminal':
        try:
            return int(platform.mac_ver()[0].split('.')[0]) >= 26   # Terminal.app gained 24-bit color in macOS 26
        except Exception:
            return False
    return False


def main():
    ap = argparse.ArgumentParser(description='LM Studio / macOS performance dashboard')
    ap.add_argument('--ping', default=DEFAULT_PING)
    ap.add_argument('--speed', type=int, default=10)
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--truecolor', action='store_true')
    ap.add_argument('--256color', dest='color256', action='store_true')
    args = ap.parse_args()

    if sys.platform != 'darwin':
        print('This dashboard is for macOS. Use the .cmd version on Windows.')
        return 1
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('Run this directly in a terminal window.')
        return 1

    for fn, a in ((cpu_sampler, ()), (mem_sampler, ()), (net_sampler, ()), (disk_sampler, ()),
                  (gpu_sampler, ()), (temp_sampler, ()), (lm_sampler, ()), (ping_sampler, (args.ping,)),
                  (power_sampler, ())):
        threading.Thread(target=fn, args=a, daemon=True).start()

    screen = Screen(detect_truecolor(args))
    dash = Dashboard(args, screen)
    out = sys.stdout.buffer
    fd = sys.stdin.fileno()
    old_attr = termios.tcgetattr(fd)
    quit_flag = {'q': False}

    def on_signal(signum, frame):
        quit_flag['q'] = True
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    frame_s = 1.0 / max(1, args.fps)
    try:
        tty.setcbreak(fd)
        out.write(b'\x1b[?1049h\x1b[?25l\x1b[2J')
        out.flush()
        last_size = None
        t_prev = time.monotonic()
        next_scroll = t_prev
        while not quit_flag['q']:
            t0 = time.monotonic()
            dt = max(0.001, t0 - t_prev)
            t_prev = t0
            # keys
            while True:
                r, _, _ = select.select([fd], [], [], 0)
                if not r:
                    break
                data = os.read(fd, 64).decode('utf-8', 'ignore')
                for k in data.lower():
                    if k in ('q', '\x1b', '\x03'):
                        quit_flag['q'] = True
                    elif k in ('+', '='):
                        i = SPEEDS.index(dash.speed) if dash.speed in SPEEDS else 3
                        dash.speed = SPEEDS[min(len(SPEEDS) - 1, i + 1)]
                    elif k in ('-', '_'):
                        i = SPEEDS.index(dash.speed) if dash.speed in SPEEDS else 3
                        dash.speed = SPEEDS[max(0, i - 1)]
                    elif k == 'p':
                        dash.paused = not dash.paused
            if quit_flag['q']:
                break
            # size
            try:
                cols, rows = os.get_terminal_size(fd)
            except OSError:
                cols, rows = 120, 40
            if (cols, rows) != last_size:
                screen.resize(max(20, cols - 1), max(8, rows))
                last_size = (cols, rows)
            # update / scroll / draw
            dash.update(dt, t0 * 1000)
            step = 1.0 / max(1, dash.speed)
            if t0 - next_scroll > 1.0:
                next_scroll = t0
            while t0 >= next_scroll:
                dash.scroll()
                next_scroll += step
            try:
                dash.render()
                dash.err = None
            except Exception as e:
                dash.err = '%s: %s' % (type(e).__name__, e)
            screen.flush(out)
            spent = time.monotonic() - t0
            if frame_s - spent > 0.001:
                time.sleep(frame_s - spent)
            total = time.monotonic() - t0
            if total > 0:
                dash.fps += (1.0 / total - dash.fps) * 0.1
    finally:
        S['run'] = False
        try:
            out.write(b'\x1b[0m\x1b[?25h\x1b[?1049l')
            out.flush()
        except Exception:
            pass
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attr)
        for p in CHILDREN:
            try:
                p.terminate()
            except Exception:
                pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
