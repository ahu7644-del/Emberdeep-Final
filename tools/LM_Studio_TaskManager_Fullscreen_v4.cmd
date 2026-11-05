@echo off
setlocal
set "LM_MONITOR_FILE=%~f0"
title LM Studio - Performance Dashboard v4
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$raw = [System.IO.File]::ReadAllText($env:LM_MONITOR_FILE); $tag = '# POWERSHELL MONITOR START'; $i = $raw.LastIndexOf($tag); if ($i -lt 0) { throw 'Embedded monitor code missing' }; Invoke-Expression ($raw.Substring($i + $tag.Length))"
set "MONITOR_EXIT=%errorlevel%"
endlocal & exit /b %MONITOR_EXIT%
# POWERSHELL MONITOR START
# =====================================================================
#  LM Studio / Windows Performance Dashboard  -  v4
#  Reads real Windows OS metrics (performance counters, NVIDIA driver,
#  optional LibreHardwareMonitor/OpenHardwareMonitor sensors, ICMP ping).
#
#  v4: metrics are sampled on background threads; the screen is drawn
#  ~30 times a second by a compiled renderer that only repaints changed
#  cells. Graphs scroll right-to-left continuously with 1/8-cell vertical
#  resolution, and every graph, meter, number and label shares ONE color
#  scale:  blue/green = normal  ->  yellow = watch  ->  orange = high  ->  red = critical
#
#  Keys:  Q quit   + / - scroll speed   P pause history
# =====================================================================

# ---------------- settings you can change ----------------
$PingTarget      = '131.241.51.94'   # host for the continuous ping panel
$ScrollPerSecond = 10                # graph columns per second (+ / - while running)
$TargetFps       = 30                # screen refresh rate
$EaseSeconds     = 0.12              # smoothing of live values (lower = snappier)
# ---------------------------------------------------------

$ErrorActionPreference = 'SilentlyContinue'
$Inv = [Globalization.CultureInfo]::InvariantCulture
try { [Console]::Title = 'LM STUDIO | SYSTEM PERFORMANCE v4' } catch {}
Write-Host 'Starting LM Studio performance dashboard...'

$rendererSource = @'
using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;

public class LmSeries
{
    private double[] buf;
    private int head;
    private int count;
    public double Live = double.NaN;
    public double Target = double.NaN;

    public LmSeries(int capacity) { buf = new double[Math.Max(16, capacity)]; }
    public int Count { get { return count; } }

    public void Push(double v)
    {
        buf[head] = v;
        head = (head + 1) % buf.Length;
        if (count < buf.Length) count++;
    }

    public double Ago(int k)
    {
        if (k < 0 || k >= count) return double.NaN;
        int i = head - 1 - k;
        if (i < 0) i += buf.Length;
        return buf[i];
    }

    public void Ease(double a)
    {
        if (double.IsNaN(Target)) { Live = double.NaN; return; }
        if (double.IsNaN(Live) || Target < 0 || Live < 0) { Live = Target; return; }
        Live += (Target - Live) * a;
    }

    public double MaxRecent(int n)
    {
        double m = (double.IsNaN(Live) || Live < 0) ? 0 : Live;
        for (int k = 0; k < n && k < count; k++)
        {
            double v = Ago(k);
            if (!double.IsNaN(v) && v > m) m = v;
        }
        return m;
    }

    public void Clear() { head = 0; count = 0; }
}

public static class LmDash4
{
    const string ESC = "\u001b";

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern IntPtr GetStdHandle(int nStdHandle);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetConsoleOutputCP(uint wCodePageID);

    public static int W = 1;
    public static int H = 1;
    static char[] ch = new char[1];
    static int[] fg = new int[1];
    static int[] bg = new int[1];
    static char[] pch = new char[1];
    static int[] pfg = new int[1];
    static int[] pbg = new int[1];
    static bool full = true;
    static Stream outS;

    // ---------- color helpers ----------
    static int Clamp255(int v) { return v < 0 ? 0 : (v > 255 ? 255 : v); }
    public static int Rgb(int r, int g, int b) { return (Clamp255(r) << 16) | (Clamp255(g) << 8) | Clamp255(b); }

    public static int Mix(int a, int b, double t)
    {
        if (t < 0) t = 0; if (t > 1) t = 1;
        int ar = (a >> 16) & 255, ag = (a >> 8) & 255, ab = a & 255;
        int br = (b >> 16) & 255, bgc = (b >> 8) & 255, bb = b & 255;
        return Rgb((int)Math.Round(ar + (br - ar) * t), (int)Math.Round(ag + (bgc - ag) * t), (int)Math.Round(ab + (bb - ab) * t));
    }

    // ONE shared color scale for every graph, meter, number and label.
    // severity 0.00-0.50 normal (blue -> teal -> green), 0.50 watch (yellow),
    // 0.75 high (orange), 0.90+ critical (red -> hot pink)
    static double[] rampStops = new double[] { 0.00, 0.20, 0.42, 0.50, 0.75, 0.90, 1.00 };
    static int[] rampColors = new int[] {
        Rgb(70, 150, 255), Rgb(45, 200, 215), Rgb(80, 222, 125), Rgb(242, 218, 80),
        Rgb(255, 150, 55), Rgb(255, 72, 88), Rgb(255, 60, 170) };
    public static int Muted = Rgb(92, 106, 124);

    public static int Ramp(double s)
    {
        if (double.IsNaN(s)) return Muted;
        if (s <= 0) return rampColors[0];
        if (s >= 1) return rampColors[rampColors.Length - 1];
        for (int i = 1; i < rampStops.Length; i++)
        {
            if (s <= rampStops[i])
            {
                double t = (s - rampStops[i - 1]) / (rampStops[i] - rampStops[i - 1]);
                return Mix(rampColors[i - 1], rampColors[i], t);
            }
        }
        return rampColors[rampColors.Length - 1];
    }

    // kinds: 0 load %, 1 RAM %, 2 VRAM %, 3 CPU temp C, 4 GPU temp C, 5 ping ms, 6 relative (0..1)
    // columns: floor, WATCH, HIGH, CRITICAL, top of scale
    static double[][] thresholds = new double[][] {
        new double[] { 0, 60, 80, 95, 100 },
        new double[] { 0, 70, 85, 95, 100 },
        new double[] { 0, 80, 92, 98, 100 },
        new double[] { 30, 75, 85, 95, 105 },
        new double[] { 30, 72, 83, 90, 100 },
        new double[] { 0, 60, 120, 250, 500 },
        new double[] { 0, 0.70, 0.85, 0.95, 1.0 } };
    static double[] sevStops = new double[] { 0, 0.5, 0.75, 0.9, 1.0 };

    public static double Sev(int kind, double v)
    {
        if (double.IsNaN(v)) return double.NaN;
        if (kind < 0 || kind >= thresholds.Length) kind = 0;
        if (kind == 5 && v < 0) return 1.0;  // ping timeout
        double[] t = thresholds[kind];
        if (v <= t[0]) return 0;
        for (int i = 1; i < 5; i++)
        {
            if (v <= t[i]) return sevStops[i - 1] + (sevStops[i] - sevStops[i - 1]) * (v - t[i - 1]) / (t[i] - t[i - 1]);
        }
        return 1.0;
    }

    public static int TierIndex(double s)
    {
        if (double.IsNaN(s)) return -1;
        if (s < 0.5) return 0;
        if (s < 0.75) return 1;
        if (s < 0.9) return 2;
        return 3;
    }

    static double[] tierSev = new double[] { 0.36, 0.60, 0.82, 0.95 };
    public static int TierColor(int t)
    {
        if (t < 0 || t > 3) return Muted;
        return Ramp(tierSev[t]);
    }

    // ---------- console ----------
    public static void EnableVT()
    {
        try
        {
            IntPtr h = GetStdHandle(-11);
            uint mode;
            if (GetConsoleMode(h, out mode)) SetConsoleMode(h, mode | 0x0001 | 0x0004);
            SetConsoleOutputCP(65001);
        }
        catch { }
    }

    static void WriteOut(string s)
    {
        if (outS == null) outS = Console.OpenStandardOutput();
        byte[] b = Encoding.UTF8.GetBytes(s);
        outS.Write(b, 0, b.Length);
        outS.Flush();
    }

    public static void Raw(string s) { try { WriteOut(s); } catch { } }

    // ---------- cell buffer ----------
    public static void Resize(int w, int h)
    {
        W = Math.Max(1, w); H = Math.Max(1, h);
        int n = W * H;
        ch = new char[n]; fg = new int[n]; bg = new int[n];
        pch = new char[n]; pfg = new int[n]; pbg = new int[n];
        full = true;
    }

    public static void ForceFull() { full = true; }

    public static void Clear(int b)
    {
        for (int i = 0; i < ch.Length; i++) { ch[i] = ' '; fg[i] = 0; bg[i] = b; }
    }

    public static void Put(int x, int y, char c, int f, int b)
    {
        if (x < 0 || y < 0 || x >= W || y >= H) return;
        int i = y * W + x;
        ch[i] = c;
        if (f >= 0) fg[i] = f;
        if (b >= 0) bg[i] = b;
    }

    public static int Text(int x, int y, string s, int f, int b)
    {
        if (s == null) return x;
        for (int k = 0; k < s.Length; k++) Put(x + k, y, s[k], f, b);
        return x + s.Length;
    }

    public static int TextMax(int x, int y, string s, int f, int b, int max)
    {
        if (s == null || max <= 0) return x;
        int n = Math.Min(s.Length, max);
        for (int k = 0; k < n; k++) Put(x + k, y, s[k], f, b);
        return x + n;
    }

    public static void Fill(int x, int y, int w, int h, int b)
    {
        for (int yy = y; yy < y + h; yy++)
            for (int xx = x; xx < x + w; xx++)
                Put(xx, yy, ' ', 0, b);
    }

    public static void Panel(int x, int y, int w, int h, int pbgc, int border, string title, int titleFg)
    {
        if (w < 4 || h < 2) return;
        Fill(x, y, w, h, pbgc);
        for (int xx = x + 1; xx < x + w - 1; xx++) { Put(xx, y, '\u2500', border, pbgc); Put(xx, y + h - 1, '\u2500', border, pbgc); }
        for (int yy = y + 1; yy < y + h - 1; yy++) { Put(x, yy, '\u2502', border, pbgc); Put(x + w - 1, yy, '\u2502', border, pbgc); }
        Put(x, y, '\u256D', border, pbgc);
        Put(x + w - 1, y, '\u256E', border, pbgc);
        Put(x, y + h - 1, '\u2570', border, pbgc);
        Put(x + w - 1, y + h - 1, '\u256F', border, pbgc);
        if (!string.IsNullOrEmpty(title)) TextMax(x + 2, y, " " + title + " ", titleFg, pbgc, w - 4);
    }

    // Scrolling area graph. Newest sample at the right edge (the live, eased value),
    // older samples move left. Each column is colored by the shared severity scale
    // of ITS OWN value; the fill fades toward the background for depth.
    public static void Area(int x, int y, int w, int h, LmSeries s, double scale, int kind, long tick, int gbg, int grid)
    {
        if (w < 1 || h < 1 || s == null) return;
        if (scale <= 0 || double.IsNaN(scale)) scale = 1;
        int g1 = h / 4, g2 = h / 2, g3 = (3 * h) / 4;
        int red = Ramp(1.0);
        for (int c = 0; c < w; c++)
        {
            int k = w - 1 - c;
            double v = (k == 0) ? s.Live : s.Ago(k - 1);
            long abs = tick - k;
            bool vgrid = (((abs % 10) + 10) % 10) == 0;
            bool has = !double.IsNaN(v);
            bool timeout = has && v < 0;
            double frac = (has && !timeout) ? Math.Max(0, Math.Min(1, v / scale)) : 0;
            double sev = has ? (kind == 6 ? Sev(6, frac * 0.8) : Sev(kind, v)) : double.NaN;
            int col = Ramp(sev);
            double total = frac * h * 8.0;
            if (has && !timeout && v > 0 && total < 1) total = 1;
            for (int r = 0; r < h; r++)
            {
                int yy = y + h - 1 - r;
                int rowFromTop = h - 1 - r;
                double lo = r * 8.0;
                if (timeout)
                {
                    if (r == 0) Put(x + c, yy, '\u00D7', red, Mix(gbg, red, 0.22));
                    else Put(x + c, yy, ' ', 0, Mix(gbg, red, 0.08));
                    continue;
                }
                if (has && total >= lo + 8)
                {
                    double f = 0.26 + 0.74 * Math.Pow((lo + 8) / total, 1.5);
                    Put(x + c, yy, '\u2588', Mix(gbg, col, f), gbg);
                }
                else if (has && total > lo)
                {
                    int p = (int)Math.Round(total - lo);
                    if (p < 1) p = 1;
                    if (p >= 8) Put(x + c, yy, '\u2588', col, gbg);
                    else Put(x + c, yy, (char)(0x2580 + p), col, gbg);
                }
                else
                {
                    bool hgrid = (rowFromTop == g1 || rowFromTop == g2 || rowFromTop == g3) && h >= 3;
                    if (vgrid && hgrid) Put(x + c, yy, '\u253C', grid, gbg);
                    else if (vgrid) Put(x + c, yy, '\u2502', grid, gbg);
                    else if (hgrid) Put(x + c, yy, '\u2500', grid, gbg);
                    else Put(x + c, yy, ' ', 0, gbg);
                }
            }
        }
    }

    // Horizontal gauge with 1/8-cell resolution; each cell takes the color of the
    // severity at its position, so the bar shows the same scale as the graphs.
    public static void Meter(int x, int y, int w, double v, double max, int kind, int track)
    {
        if (w < 1) return;
        double frac = (double.IsNaN(v) || max <= 0) ? 0 : Math.Max(0, Math.Min(1, v / max));
        double cells = frac * w;
        for (int c = 0; c < w; c++)
        {
            double pos = (c + 0.5) / w;
            int colr = Ramp(kind == 6 ? Sev(6, pos * 0.8) : Sev(kind, pos * max));
            if (cells >= c + 1) Put(x + c, y, '\u2588', colr, track);
            else if (cells > c)
            {
                int p = (int)Math.Round((cells - c) * 8);
                if (p <= 0) Put(x + c, y, ' ', 0, track);
                else if (p >= 8) Put(x + c, y, '\u2588', colr, track);
                else Put(x + c, y, (char)(0x2590 - p), colr, track);
            }
            else Put(x + c, y, ' ', 0, track);
        }
    }

    public static void Legend(int x, int y, int w)
    {
        for (int c = 0; c < w; c++) Put(x + c, y, '\u2584', Ramp(w <= 1 ? 0 : (double)c / (w - 1)), -1);
    }

    static void Sgr(StringBuilder sb, int code, int c)
    {
        sb.Append(ESC).Append('[').Append(code).Append(";2;")
          .Append((c >> 16) & 255).Append(';').Append((c >> 8) & 255).Append(';').Append(c & 255).Append('m');
    }

    // Writes only the cells that changed since the previous frame.
    public static void Flush()
    {
        StringBuilder sb = new StringBuilder(full ? W * H * 8 : 4096);
        if (full) sb.Append(ESC).Append("[0m").Append(ESC).Append("[2J");
        int cf = -1, cb = -1, cx = -1, cy = -1;
        for (int y = 0; y < H; y++)
        {
            for (int x = 0; x < W; x++)
            {
                int i = y * W + x;
                bool changed = full || ch[i] != pch[i] || bg[i] != pbg[i] || (ch[i] != ' ' && fg[i] != pfg[i]);
                if (!changed) continue;
                if (cy != y || cx != x) sb.Append(ESC).Append('[').Append(y + 1).Append(';').Append(x + 1).Append('H');
                if (bg[i] != cb) { Sgr(sb, 48, bg[i]); cb = bg[i]; }
                if (ch[i] != ' ' && fg[i] != cf) { Sgr(sb, 38, fg[i]); cf = fg[i]; }
                sb.Append(ch[i]);
                cx = x + 1; cy = y;
                pch[i] = ch[i]; pfg[i] = fg[i]; pbg[i] = bg[i];
            }
        }
        full = false;
        if (sb.Length > 0)
        {
            sb.Append(ESC).Append("[0m");
            try { WriteOut(sb.ToString()); } catch { full = true; }
        }
    }
}
'@

try {
    $ErrorActionPreference = 'Stop'
    Add-Type -TypeDefinition $rendererSource -Language CSharp
    $ErrorActionPreference = 'SilentlyContinue'
} catch {
    Write-Host ''
    Write-Host ('Could not compile the dashboard renderer: ' + $_.Exception.Message) -ForegroundColor Red
    Read-Host 'Press Enter to exit'
    return
}
$D = [LmDash4]

# ============================ background workers ============================
$S = [hashtable]::Synchronized(@{
    Run = $true
    Target = $PingTarget
    PingQ = (New-Object 'System.Collections.Concurrent.ConcurrentQueue[object]')
    LmPids = [int[]]@()
    GpuSrc = 'starting'; VramSrc = 'starting'; TempSrc = 'starting'
})

# ---- fast sampler: CPU, disk, RAM, network, GPU engines, NVIDIA (4 Hz) ----
$fastCode = {
    param($S)
    $ErrorActionPreference = 'SilentlyContinue'
    $inv = [Globalization.CultureInfo]::InvariantCulture
    function New-PC([string]$cat, [string]$ctr, [string]$inst) {
        try {
            $c = New-Object System.Diagnostics.PerformanceCounter($cat, $ctr, $inst, $true)
            [void]$c.NextValue()
            return $c
        } catch { return $null }
    }
    function ParseD([string]$s) {
        $v = [double]0
        if ([double]::TryParse(([string]$s).Trim(), [Globalization.NumberStyles]::Float, $inv, [ref]$v)) { return $v }
        return $null
    }
    function Pick-NIC {
        try {
            $adapters = @([System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | Where-Object {
                $_.OperationalStatus -eq [System.Net.NetworkInformation.OperationalStatus]::Up -and
                $_.NetworkInterfaceType -ne [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback -and
                $_.NetworkInterfaceType -ne [System.Net.NetworkInformation.NetworkInterfaceType]::Tunnel
            })
            $gw = @($adapters | Where-Object {
                @($_.GetIPProperties().GatewayAddresses | Where-Object { $_.Address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork }).Count -gt 0
            })
            if ($gw.Count -gt 0) { return ($gw | Sort-Object Speed -Descending | Select-Object -First 1) }
            return ($adapters | Sort-Object Speed -Descending | Select-Object -First 1)
        } catch { return $null }
    }
    function Parse-NvLine([string]$line) {
        $cells = @(([string]$line) -split ',')
        if ($cells.Count -lt 6) { return }
        $idx = ParseD $cells[0]
        if ($null -eq $idx) { return }
        $script:nvRows[[int]$idx] = @{ Name = $cells[1].Trim(); Load = (ParseD $cells[2]); Used = (ParseD $cells[3]); Total = (ParseD $cells[4]); Temp = (ParseD $cells[5]) }
    }

    $cpuPC = New-PC 'Processor Information' '% Processor Utility' '_Total'
    if (-not $cpuPC) { $cpuPC = New-PC 'Processor' '% Processor Time' '_Total' }
    $idlePC = New-PC 'PhysicalDisk' '% Idle Time' '_Total'
    $memPC = New-PC 'Memory' 'Available Bytes' ''
    $totalRam = $null
    try { $totalRam = [double](Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).TotalVisibleMemorySize * 1024 } catch {}

    $gpuCat = $null; $memCat = $null
    try { if ([System.Diagnostics.PerformanceCounterCategory]::Exists('GPU Engine')) { $gpuCat = New-Object System.Diagnostics.PerformanceCounterCategory('GPU Engine') } } catch {}
    try { if ([System.Diagnostics.PerformanceCounterCategory]::Exists('GPU Adapter Memory')) { $memCat = New-Object System.Diagnostics.PerformanceCounterCategory('GPU Adapter Memory') } } catch {}

    # Physical VRAM size for non-NVIDIA cards (driver registry value).
    $regVram = $null
    try {
        Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue | ForEach-Object {
            $q = (Get-ItemProperty -LiteralPath $_.PSPath -ErrorAction SilentlyContinue).'HardwareInformation.qwMemorySize'
            if ($q) { $v = [double]$q; if ($null -eq $regVram -or $v -gt $regVram) { $regVram = $v } }
        }
    } catch {}
    $wddmName = $null
    try { $wddmName = (Get-CimInstance Win32_VideoController -ErrorAction Stop | Sort-Object AdapterRAM -Descending | Select-Object -First 1).Name } catch {}

    # NVIDIA: one long-running nvidia-smi streaming a CSV row every 500 ms (no per-sample process spawn).
    $script:nvRows = @{}
    $nvCmd = Get-Command 'nvidia-smi.exe' -ErrorAction SilentlyContinue
    $nvExe = if ($nvCmd) { $nvCmd.Source } elseif (Test-Path "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe") { "$env:ProgramFiles\NVIDIA Corporation\NVSMI\nvidia-smi.exe" } else { $null }
    $nvQuery = '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits'
    $nv = $null; $nvTask = $null; $nvOneShot = $false
    if ($nvExe) {
        try {
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = $nvExe
            $psi.Arguments = $nvQuery + ' -lms 500'
            $psi.UseShellExecute = $false
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.CreateNoWindow = $true
            $nv = [System.Diagnostics.Process]::Start($psi)
            $nv.BeginErrorReadLine()
            $S.NvPid = $nv.Id
            $nvTask = $nv.StandardOutput.ReadLineAsync()
        } catch { $nv = $null; $nvOneShot = $true }
    }

    $prevGpu = @{}
    $prevNet = $null
    $nic = $null
    $n = 0
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($S.Run) {
        $t0 = $clock.ElapsedMilliseconds
        $n++

        # ---- CPU (Task Manager uses % Processor Utility) ----
        if ($cpuPC) { try { $S.Cpu = [Math]::Min(100, [Math]::Max(0, [double]$cpuPC.NextValue())) } catch {} }
        elseif ($n % 4 -eq 1) { try { $S.Cpu = [double](Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'").PercentProcessorTime } catch {} }

        # ---- Disk busy ----
        if ($idlePC) { try { $S.Disk = [Math]::Min(100, [Math]::Max(0, 100 - [double]$idlePC.NextValue())) } catch {} }

        # ---- RAM ----
        try {
            $avail = $null
            if ($memPC) { $avail = [double]$memPC.NextValue() }
            elseif ($n % 4 -eq 1) { $os = Get-CimInstance Win32_OperatingSystem; $totalRam = [double]$os.TotalVisibleMemorySize * 1024; $avail = [double]$os.FreePhysicalMemory * 1024 }
            if ($null -ne $avail -and $totalRam -gt 0) {
                $S.RamTotal = $totalRam / 1GB
                $S.RamUsed = ($totalRam - $avail) / 1GB
                $S.RamPct = 100 * ($totalRam - $avail) / $totalRam
            }
        } catch {}

        # ---- Network ----
        if ($n % 20 -eq 1 -or -not $nic) { $nic = Pick-NIC }
        if ($nic) {
            try {
                $st = $nic.GetIPStatistics()
                $now = $clock.Elapsed.TotalSeconds
                if ($prevNet -and $prevNet.Id -eq $nic.Id) {
                    $dt = $now - $prevNet.Time
                    if ($dt -gt 0) {
                        $S.Down = [Math]::Max(0, ($st.BytesReceived - $prevNet.Rx) / $dt / 1MB)
                        $S.Up = [Math]::Max(0, ($st.BytesSent - $prevNet.Tx) / $dt / 1MB)
                    }
                }
                $prevNet = @{ Id = $nic.Id; Time = $now; Rx = $st.BytesReceived; Tx = $st.BytesSent }
                $S.NicName = $nic.Name
                $S.NicSpeed = [double]$nic.Speed
            } catch { $prevNet = $null }
        } else { $S.Down = $null; $S.Up = $null }

        # ---- GPU engines (Task Manager method: sum processes per engine, busiest engine) ----
        $engineLoad = $null; $lmLoad = $null; $selAd = $null
        if ($gpuCat) {
            try {
                $coll = $gpuCat.ReadCategory()['Utilization Percentage']
                $lmSet = @{}
                foreach ($p in @($S.LmPids)) { $lmSet[[int]$p] = $true }
                $engTot = @{}; $engLm = @{}; $engAd = @{}; $newPrev = @{}
                foreach ($name in @($coll.Keys)) {
                    $smp = $coll[$name].Sample
                    $newPrev[$name] = $smp
                    if (-not $prevGpu.ContainsKey($name)) { continue }
                    if ($name -notmatch '^pid_(\d+)_luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)') { continue }
                    $pidNum = [int]$Matches[1]
                    $ad = $Matches[2] + '_' + $Matches[3]
                    $eng = $ad + '_' + $Matches[4]
                    $u = [double][System.Diagnostics.CounterSample]::Calculate($prevGpu[$name], $smp)
                    if ([double]::IsNaN($u) -or [double]::IsInfinity($u) -or $u -lt 0) { continue }
                    $engTot[$eng] = [double]$engTot[$eng] + $u
                    $engAd[$eng] = $ad
                    if ($lmSet.ContainsKey($pidNum)) { $engLm[$eng] = [double]$engLm[$eng] + $u }
                }
                $prevGpu = $newPrev
                if ($engTot.Count -gt 0) {
                    $hi = -1.0; $lmHi = 0.0
                    foreach ($e in $engTot.Keys) {
                        if ($engTot[$e] -gt $hi) { $hi = [double]$engTot[$e]; $selAd = $engAd[$e] }
                        if ([double]$engLm[$e] -gt $lmHi) { $lmHi = [double]$engLm[$e] }
                    }
                    $engineLoad = [Math]::Min(100, [Math]::Max(0, $hi))
                    $lmLoad = [Math]::Min(100, $lmHi)
                }
            } catch {}
        }

        # ---- Windows dedicated GPU memory (fallback VRAM) ----
        $wddmVram = $null
        if ($memCat) {
            try {
                $mc = $memCat.ReadCategory()['Dedicated Usage']
                foreach ($name in @($mc.Keys)) {
                    if ($name -notmatch 'luid_(0x[0-9a-f]+_0x[0-9a-f]+)_phys_(\d+)') { continue }
                    $ad = $Matches[1] + '_' + $Matches[2]
                    $b = [double]$mc[$name].RawValue
                    if ($ad -eq $selAd) { $wddmVram = $b; break }
                    if ($null -eq $wddmVram -or $b -gt $wddmVram) { $wddmVram = $b }
                }
            } catch {}
        }

        # ---- NVIDIA rows ----
        if ($nv) {
            try {
                while ($nvTask -and $nvTask.IsCompleted) {
                    $line = $null
                    try { $line = $nvTask.Result } catch {}
                    if ($null -eq $line) { $nvTask = $null; break }
                    Parse-NvLine $line
                    $nvTask = $nv.StandardOutput.ReadLineAsync()
                }
            } catch { $nvTask = $null }
            if ($null -eq $nvTask) { $nv = $null; $nvOneShot = $true }
        }
        if ($nvOneShot -and $nvExe -and ($n % 8 -eq 1)) {
            try { foreach ($line in @(& $nvExe ($nvQuery -split ' ') 2>$null)) { Parse-NvLine $line } } catch {}
        }
        $chosen = $null
        foreach ($r in @($script:nvRows.Values)) { if ($null -eq $chosen -or ([double]$r.Used -gt [double]$chosen.Used)) { $chosen = $r } }

        # ---- combine GPU sources ----
        if ($null -ne $engineLoad) { $S.GpuLoad = $engineLoad; $S.GpuSrc = 'Windows GPU engine (Task Manager method)' }
        elseif ($chosen -and $null -ne $chosen.Load) { $S.GpuLoad = $chosen.Load; $S.GpuSrc = 'NVIDIA driver (nvidia-smi)' }
        elseif ($null -ne $S.SensorGpuLoad) { $S.GpuLoad = $S.SensorGpuLoad; $S.GpuSrc = 'hardware sensor' }
        else { $S.GpuLoad = $null; $S.GpuSrc = 'unavailable' }
        $S.LmGpu = $lmLoad

        $vu = $null; $vt = $null
        if ($chosen -and $null -ne $chosen.Used) {
            $vu = $chosen.Used / 1024
            if ($null -ne $chosen.Total) { $vt = $chosen.Total / 1024 }
            $S.VramSrc = 'NVIDIA driver'
        } elseif ($null -ne $wddmVram) {
            $vu = $wddmVram / 1GB
            if ($regVram) { $vt = $regVram / 1GB }
            $S.VramSrc = 'Windows dedicated GPU memory'
        } else { $S.VramSrc = 'unavailable' }
        $S.VramUsed = $vu; $S.VramTotal = $vt
        $S.VramPct = if ($null -ne $vu -and $vt -gt 0) { [Math]::Min(100, 100 * $vu / $vt) } else { $null }

        if ($chosen -and $null -ne $chosen.Temp) { $S.GpuTemp = $chosen.Temp } else { $S.GpuTemp = $S.SensorGpuTemp }
        $S.GpuName = if ($chosen -and $chosen.Name) { $chosen.Name } else { $wddmName }

        $wait = 250 - ($clock.ElapsedMilliseconds - $t0)
        if ($wait -gt 1) { [System.Threading.Thread]::Sleep([int]$wait) }
    }
    if ($nv) { try { if (-not $nv.HasExited) { $nv.Kill() } } catch {} }
}

# ---- slow sampler: LM Studio processes + hardware temperature sensors (1 Hz) ----
$slowCode = {
    param($S)
    $ErrorActionPreference = 'SilentlyContinue'
    $cores = [Math]::Max(1, [Environment]::ProcessorCount)
    $S.Cores = $cores
    try { $S.CpuName = ([string](Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object -First 1).Name).Trim() } catch {}
    $namePattern = '^(LM Studio|LMStudio|lmstudio|lms|llmster|LM Studio Helper|llama-server|llama-box|llama-cpp|lm-studio)$'
    $extra = @()
    $prev = @{}
    $prevT = $null
    $n = 0
    $sensorFails = 0
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($S.Run) {
        $t0 = $clock.ElapsedMilliseconds
        $n++

        # ---- LM Studio processes ----
        if ($n % 5 -eq 1) {
            try {
                $extra = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
                    $_.Name -match '^(LM Studio|LMStudio|lmstudio|lms|llmster|LM Studio Helper|llama-server|llama-box|llama-cpp|lm-studio)(\.exe)?$' -or
                    $_.ExecutablePath -match '[\\/](\.lmstudio|LM Studio)[\\/]'
                } | ForEach-Object { [int]$_.ProcessId })
            } catch { $extra = @() }
        }
        $found = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match $namePattern -or $extra -contains $_.Id })
        $cur = @{}; $ramSum = 0.0; $delta = 0.0; $valid = $false
        foreach ($p in $found) {
            try {
                $sec = [double]$p.TotalProcessorTime.TotalSeconds
                $cur[$p.Id] = $sec
                if ($prev.ContainsKey($p.Id)) { $delta += [Math]::Max(0, $sec - $prev[$p.Id]); $valid = $true }
                $ramSum += [double]$p.WorkingSet64 / 1GB
            } catch {}
        }
        $now = $clock.Elapsed.TotalSeconds
        if ($valid -and $null -ne $prevT -and ($now - $prevT) -gt 0) {
            $S.LmCpu = [Math]::Min(100, 100 * $delta / (($now - $prevT) * $cores))
        } elseif ($found.Count -eq 0) { $S.LmCpu = $null }
        $prev = $cur; $prevT = $now
        $S.LmCount = $found.Count
        $S.LmRam = $ramSum
        $S.LmPids = [int[]]@($found | ForEach-Object { [int]$_.Id })

        # ---- temperature sensors (LibreHardwareMonitor / OpenHardwareMonitor WMI) ----
        if ($sensorFails -lt 3 -or ($n % 30 -eq 0)) {
            $ok = $false
            foreach ($ns in @('root\LibreHardwareMonitor', 'root\OpenHardwareMonitor')) {
                try {
                    $sensors = @(Get-CimInstance -Namespace $ns -ClassName Sensor -ErrorAction Stop)
                    if ($sensors.Count -eq 0) { continue }
                    $cpus = @($sensors | Where-Object {
                        $_.SensorType -eq 'Temperature' -and
                        ($_.Identifier -match '/(intelcpu|amdcpu|cpu)/' -or $_.Name -match '^CPU') -and
                        [double]$_.Value -gt 0
                    })
                    $pref = @($cpus | Where-Object { $_.Name -match 'CPU Package|CPU \(Tctl/Tdie\)|Core \(Tctl/Tdie\)|CPU Die|CPU Tdie' } | Select-Object -First 1)
                    $pick = if ($pref.Count -gt 0) { $pref[0] } else { $cpus | Select-Object -First 1 }
                    $S.CpuTemp = if ($pick) { [double]$pick.Value } else { $null }
                    $gt = $sensors | Where-Object {
                        $_.SensorType -eq 'Temperature' -and
                        ($_.Identifier -match '/(gpu|nvidiagpu|amdgpu|atigpu|intelgpu)/' -or $_.Name -match '^GPU') -and
                        [double]$_.Value -gt 0
                    } | Select-Object -First 1
                    $S.SensorGpuTemp = if ($gt) { [double]$gt.Value } else { $null }
                    $gl = $sensors | Where-Object { $_.SensorType -eq 'Load' -and $_.Name -match '^GPU Core$|^GPU Load$' } | Select-Object -First 1
                    $S.SensorGpuLoad = if ($gl) { [double]$gl.Value } else { $null }
                    $S.TempSrc = ($ns -replace '^root\\', '')
                    $ok = $true
                    break
                } catch {}
            }
            if ($ok) { $sensorFails = 0 } else {
                $sensorFails++
                $S.CpuTemp = $null; $S.SensorGpuTemp = $null; $S.SensorGpuLoad = $null
                $S.TempSrc = if ($S.GpuTemp -ne $null) { 'NVIDIA driver (CPU temp needs LibreHardwareMonitor)' } else { 'unavailable (run LibreHardwareMonitor)' }
            }
        }

        $wait = 1000 - ($clock.ElapsedMilliseconds - $t0)
        while ($wait -gt 0 -and $S.Run) { [System.Threading.Thread]::Sleep([int][Math]::Min(100, $wait)); $wait -= 100 }
    }
}

# ---- pinger: continuous ICMP, ~4 per second ----
$pingCode = {
    param($S)
    $ErrorActionPreference = 'SilentlyContinue'
    $p = New-Object System.Net.NetworkInformation.Ping
    $clock = [Diagnostics.Stopwatch]::StartNew()
    while ($S.Run) {
        $t0 = $clock.ElapsedMilliseconds
        $ms = -1.0
        try {
            $r = $p.Send([string]$S.Target, 1000)
            if ($r -and $r.Status -eq [System.Net.NetworkInformation.IPStatus]::Success) { $ms = [double]$r.RoundtripTime }
        } catch {}
        $S.PingQ.Enqueue($ms)
        $wait = 250 - ($clock.ElapsedMilliseconds - $t0)
        if ($wait -gt 1) { [System.Threading.Thread]::Sleep([int]$wait) }
    }
    $p.Dispose()
}

$workers = @()
foreach ($code in @($fastCode, $slowCode, $pingCode)) {
    $ps = [PowerShell]::Create()
    [void]$ps.AddScript($code.ToString()).AddArgument($S)
    $workers += @{ PS = $ps; Handle = $ps.BeginInvoke() }
}

# ============================ main thread: draw ============================
function RGB([int]$r, [int]$g, [int]$b) { return $D::Rgb($r, $g, $b) }
$C = @{
    Bg     = RGB 10 13 19
    Head   = RGB 18 24 34
    Panel  = RGB 16 21 29
    Graph  = RGB 11 15 21
    Border = RGB 42 52 66
    Grid   = RGB 27 35 46
    Track  = RGB 30 38 50
    Text   = RGB 226 233 241
    Muted  = RGB 122 136 152
    Dim    = RGB 78 90 106
    Accent = RGB 96 186 255
}

$sr = @{}
$seriesKeys = @('cpu', 'gpu', 'ram', 'vram', 'down', 'up', 'cpuTemp', 'gpuTemp', 'lmCpu', 'lmGpu', 'lmRam', 'disk', 'ping')
$easeKeys = @('cpu', 'gpu', 'ram', 'vram', 'down', 'up', 'cpuTemp', 'gpuTemp', 'lmCpu', 'lmGpu', 'lmRam', 'disk')
foreach ($k in $seriesKeys) { $sr[$k] = New-Object LmSeries 2400 }
$pingRaw = New-Object 'System.Collections.Generic.List[double]'
$script:tick = [long]0
$script:paused = $false
$script:netScale = 1.0
$script:pingScale = 50.0
$script:pingTimeoutFlag = $false
$script:lastErr = $null
$script:fps = 0.0
$speeds = @(4, 6, 8, 10, 15, 20, 30)

function Num($v) { if ($null -eq $v) { return [double]::NaN }; try { return [double]$v } catch { return [double]::NaN } }
function Fmt([double]$v, [string]$f) { if ([double]::IsNaN($v)) { return '--' }; return $v.ToString($f, $Inv) }
function Pct([double]$v) { if ([double]::IsNaN($v)) { return 'N/A' }; return ($v.ToString('0', $Inv) + '%') }
function Deg([double]$v) { if ([double]::IsNaN($v)) { return 'N/A' }; return ($v.ToString('0', $Inv) + [char]0xB0 + 'C') }
function TierName([int]$kind, [double]$sev) {
    $t = $D::TierIndex($sev)
    if ($t -lt 0) { return '' }
    if ($kind -eq 0) { return @('NORMAL', 'BUSY', 'HEAVY', 'MAX')[$t] }
    if ($kind -eq 5) { return @('GOOD', 'FAIR', 'SLOW', 'BAD')[$t] }
    return @('SAFE', 'WATCH', 'HIGH', 'CRITICAL')[$t]
}
function Split-Heights([int]$total, [int]$parts) {
    $base = [int][Math]::Floor($total / $parts); $rem = $total - $base * $parts
    $out = @(); for ($i = 0; $i -lt $parts; $i++) { $out += ($base + $(if ($i -lt $rem) { 1 } else { 0 })) }
    return $out
}
function Draw-Right([int]$x, [int]$y, [int]$pw, [string]$val, [double]$sev, [string]$tier) {
    $s1 = " $val "
    $s2 = if ($tier) { " $tier " } else { '' }
    $rx = $x + $pw - 2 - $s1.Length - $s2.Length
    if ($rx -lt $x + 8) { return }
    [void]$D::Text($rx, $y, $s1, $D::Ramp($sev), $C.Panel)
    if ($s2) { [void]$D::Text($rx + $s1.Length, $y, $s2, $C.Bg, $D::TierColor($D::TierIndex($sev))) }
}
function Draw-Axis([int]$gx, [int]$gy, [int]$gh, [string]$top, [string]$mid) {
    [void]$D::Text($gx - 5, $gy, ('{0,4}' -f $top), $C.Dim, $C.Panel)
    if ($gh -ge 4) { [void]$D::Text($gx - 5, $gy + [int][Math]::Floor($gh / 2), ('{0,4}' -f $mid), $C.Dim, $C.Panel) }
    if ($gh -ge 2) { [void]$D::Text($gx - 5, $gy + $gh - 1, '   0', $C.Dim, $C.Panel) }
}
function Detail([int]$x, [int]$y, [int]$pw, [int]$ph, [string]$msg) {
    [void]$D::TextMax($x + 2, $y + $ph - 2, $msg, $C.Muted, $C.Panel, $pw - 4)
}
function Meter-Row([int]$x, [int]$y, [int]$pw, [string]$label, [double]$v, [double]$max, [int]$kind, [string]$text, [double]$sevForText) {
    [void]$D::Text($x + 2, $y, $label, $C.Muted, $C.Panel)
    $mx = $x + 8
    $tw = 12
    $mw = $pw - 8 - $tw - 2
    if ($mw -ge 4) { $D::Meter($mx, $y, $mw, $v, $max, $kind, $C.Track) }
    [void]$D::Text($x + $pw - 2 - $tw, $y, ('{0,11}' -f $text), $D::Ramp($sevForText), $C.Panel)
}

function Panel-Load([int]$x, [int]$y, [int]$pw, [int]$ph, [string]$title, $series, [int]$kind) {
    $D::Panel($x, $y, $pw, $ph, $C.Panel, $C.Border, $title, $C.Text)
    $v = $series.Live
    $sev = $D::Sev($kind, $v)
    Draw-Right $x $y $pw (Pct $v) $sev (TierName $kind $sev)
    $gx = $x + 6; $gy = $y + 1; $gw = $pw - 8; $gh = $ph - 3
    if ($gh -ge 1 -and $gw -ge 4) {
        $D::Area($gx, $gy, $gw, $gh, $series, 100, $kind, $script:tick, $C.Graph, $C.Grid)
        Draw-Axis $gx $gy $gh '100' '50'
    }
    return [Math]::Max(4, $gw)
}

function Panel-CPU([int]$x, [int]$y, [int]$pw, [int]$ph) {
    $gw = Panel-Load $x $y $pw $ph 'CPU' $sr.cpu 0
    $peak = $sr.cpu.MaxRecent($gw)
    $name = if ($S.CpuName) { $S.CpuName } else { 'Processor' }
    Detail $x $y $pw $ph ("{0}   /   {1} logical cores   /   window peak {2}" -f $name, $S.Cores, (Pct $peak))
}

function Vram-Text {
    $u = Num $S.VramUsed; $t = Num $S.VramTotal
    if ([double]::IsNaN($u)) { return 'N/A' }
    if ([double]::IsNaN($t)) { return ("{0} GiB used" -f (Fmt $u '0.0')) }
    return ("{0} / {1} GiB  {2}" -f (Fmt $u '0.0'), (Fmt $t '0.0'), (Pct $sr.vram.Live))
}

function Panel-GPU([int]$x, [int]$y, [int]$pw, [int]$ph) {
    [void](Panel-Load $x $y $pw $ph 'GPU' $sr.gpu 0)
    $dy = $y + $ph - 2
    $end = $x + $pw - 2
    $xx = $D::Text($x + 2, $dy, 'VRAM ', $C.Muted, $C.Panel)
    $mw = [Math]::Min(20, [Math]::Max(6, [int][Math]::Floor($pw / 6)))
    $D::Meter($xx, $dy, $mw, $sr.vram.Live, 100, 2, $C.Track)
    $xx = $D::Text($xx + $mw + 1, $dy, (Vram-Text), $D::Ramp($D::Sev(2, $sr.vram.Live)), $C.Panel)
    $xx = $D::TextMax($xx, $dy, '   /   LM Studio share ', $C.Muted, $C.Panel, $end - $xx)
    $xx = $D::TextMax($xx, $dy, (Pct $sr.lmGpu.Live), $D::Ramp($D::Sev(0, $sr.lmGpu.Live)), $C.Panel, $end - $xx)
    if ($S.GpuName) { [void]$D::TextMax($xx, $dy, ('   /   ' + $S.GpuName), $C.Muted, $C.Panel, $end - $xx) }
}

function Panel-Mem([int]$x, [int]$y, [int]$pw, [int]$ph) {
    [void](Panel-Load $x $y $pw $ph 'MEMORY' $sr.ram 1)
    $u = Num $S.RamUsed; $t = Num $S.RamTotal
    $free = if ([double]::IsNaN($u)) { [double]::NaN } else { $t - $u }
    $dy = $y + $ph - 2; $end = $x + $pw - 2
    $xx = $D::Text($x + 2, $dy, ("{0} / {1} GiB in use   /   {2} GiB available   /   DISK " -f (Fmt $u '0.0'), (Fmt $t '0.0'), (Fmt $free '0.0')), $C.Muted, $C.Panel)
    $xx = $D::TextMax($xx, $dy, ((Pct $sr.disk.Live) + ' busy'), $D::Ramp($D::Sev(0, $sr.disk.Live)), $C.Panel, $end - $xx)
}

function Panel-Net([int]$x, [int]$y, [int]$pw, [int]$ph) {
    $D::Panel($x, $y, $pw, $ph, $C.Panel, $C.Border, 'NETWORK', $C.Text)
    $dn = $sr.down.Live; $upv = $sr.up.Live
    $right = " {0}{1}   {2}{3} MiB/s " -f [char]0x2193, (Fmt $dn '0.00'), [char]0x2191, (Fmt $upv '0.00')
    $rx = $x + $pw - 2 - $right.Length
    if ($rx -gt $x + 12) { [void]$D::Text($rx, $y, $right, $C.Text, $C.Panel) }
    $gx = $x + 6; $gy = $y + 1; $gw = $pw - 8; $gh = $ph - 3
    if ($gh -ge 1 -and $gw -ge 9) {
        $gl = [int][Math]::Floor(($gw - 1) / 2); $gr = $gw - 1 - $gl
        $D::Area($gx, $gy, $gl, $gh, $sr.down, $script:netScale, 6, $script:tick, $C.Graph, $C.Grid)
        $D::Area($gx + $gl + 1, $gy, $gr, $gh, $sr.up, $script:netScale, 6, $script:tick, $C.Graph, $C.Grid)
        [void]$D::Text($gx + 1, $gy, ([string][char]0x2193 + ' DOWN'), $C.Text, -1)
        [void]$D::Text($gx + $gl + 2, $gy, ([string][char]0x2191 + ' UP'), $C.Text, -1)
        $sc = $script:netScale
        $top = if ($sc -ge 10) { Fmt $sc '0' } else { Fmt $sc '0.0' }
        $mid = if ($sc -ge 10) { Fmt ($sc / 2) '0' } else { Fmt ($sc / 2) '0.0' }
        Draw-Axis $gx $gy $gh $top $mid
    }
    $spd = Num $S.NicSpeed
    $spdText = if ([double]::IsNaN($spd) -or $spd -le 0) { '' } elseif ($spd -ge 1e9) { '   /   ' + (Fmt ($spd / 1e9) '0.#') + ' Gbps link' } else { '   /   ' + (Fmt ($spd / 1e6) '0') + ' Mbps link' }
    $nicName = if ($S.NicName) { $S.NicName } else { 'no active adapter' }
    Detail $x $y $pw $ph ("{0}{1}   /   scale {2} MiB/s (auto)" -f $nicName, $spdText, (Fmt $script:netScale '0.0'))
}

function Temp-Row([int]$x, [int]$y, [int]$pw, [string]$label, [double]$v, [int]$kind, [double]$max) {
    [void]$D::Text($x + 2, $y, $label, $C.Muted, $C.Panel)
    if ([double]::IsNaN($v)) {
        [void]$D::TextMax($x + 8, $y, 'sensor unavailable', $C.Dim, $C.Panel, $pw - 10)
        return
    }
    $sev = $D::Sev($kind, $v)
    [void]$D::Text($x + 8, $y, ('{0,-6}' -f (Deg $v)), $D::Ramp($sev), $C.Panel)
    $tn = TierName $kind $sev
    [void]$D::Text($x + 15, $y, (' ' + $tn + ' '), $C.Bg, $D::TierColor($D::TierIndex($sev)))
    $mx = $x + 27; $mw = $pw - 27 - 2
    if ($mw -ge 4) { $D::Meter($mx, $y, $mw, $v, $max, $kind, $C.Track) }
}

function Panel-Thermal([int]$x, [int]$y, [int]$pw, [int]$ph) {
    $D::Panel($x, $y, $pw, $ph, $C.Panel, $C.Border, 'THERMALS', $C.Text)
    $ct = $sr.cpuTemp.Live; $gt = $sr.gpuTemp.Live
    Temp-Row $x ($y + 1) $pw 'CPU' $ct 3 105
    Temp-Row $x ($y + 2) $pw 'GPU' $gt 4 100
    $gx = $x + 6; $gy = $y + 3; $gw = $pw - 8; $gh = $ph - 4
    if ($gh -ge 2 -and $gw -ge 9) {
        $gl = [int][Math]::Floor(($gw - 1) / 2); $gr = $gw - 1 - $gl
        $D::Area($gx, $gy, $gl, $gh, $sr.cpuTemp, 100, 3, $script:tick, $C.Graph, $C.Grid)
        $D::Area($gx + $gl + 1, $gy, $gr, $gh, $sr.gpuTemp, 100, 4, $script:tick, $C.Graph, $C.Grid)
        [void]$D::Text($gx + 1, $gy, ('CPU ' + [char]0xB0 + 'C'), $C.Text, -1)
        [void]$D::Text($gx + $gl + 2, $gy, ('GPU ' + [char]0xB0 + 'C'), $C.Text, -1)
        Draw-Axis $gx $gy $gh '100' '50'
    }
}

function Panel-LM([int]$x, [int]$y, [int]$pw, [int]$ph) {
    $D::Panel($x, $y, $pw, $ph, $C.Panel, $C.Border, 'LM STUDIO', $C.Text)
    $cnt = [int]$S.LmCount
    if ($cnt -gt 0) {
        $xx = $D::Text($x + 2, $y + 1, ([string][char]0x25CF + ' RUNNING'), $D::Ramp(0.42), $C.Panel)
        [void]$D::TextMax($xx, $y + 1, ("   {0} process{1}" -f $cnt, $(if ($cnt -ne 1) { 'es' } else { '' })), $C.Muted, $C.Panel, $x + $pw - 2 - $xx)
    } else {
        [void]$D::Text($x + 2, $y + 1, ([string][char]0x25CB + ' NOT DETECTED'), $C.Dim, $C.Panel)
    }
    if ($ph -lt 7) { return }
    $lc = $sr.lmCpu.Live; $lg = $sr.lmGpu.Live; $lr = $sr.lmRam.Live
    Meter-Row $x ($y + 2) $pw 'CPU' $lc 100 0 (Pct $lc) ($D::Sev(0, $lc))
    Meter-Row $x ($y + 3) $pw 'GPU' $lg 100 0 (Pct $lg) ($D::Sev(0, $lg))
    $lmGiB = Num $S.LmRam
    Meter-Row $x ($y + 4) $pw 'RAM' $lr 100 1 ((Fmt $lmGiB '0.00') + ' GiB') ($D::Sev(1, $sr.ram.Live))
    Meter-Row $x ($y + 5) $pw 'VRAM' $sr.vram.Live 100 2 (Pct $sr.vram.Live) ($D::Sev(2, $sr.vram.Live))
}

function Panel-Ping([int]$x, [int]$y, [int]$pw, [int]$ph) {
    $D::Panel($x, $y, $pw, $ph, $C.Panel, $C.Border, "PING  $($S.Target)", $C.Text)
    $last = $sr.ping.Live
    $sev = $D::Sev(5, $last)
    $lt = if ([double]::IsNaN($last)) { 'sampling' } elseif ($last -lt 0) { 'TIMEOUT' } else { (Fmt $last '0') + ' ms' }
    Draw-Right $x $y $pw $lt $sev (TierName 5 $sev)

    # stats over the last 60 probes
    $okN = 0; $sum = 0.0; $mn = [double]::NaN; $mx = [double]::NaN; $jit = 0.0; $prevOk = [double]::NaN; $jn = 0
    foreach ($p in $pingRaw) {
        if ($p -ge 0) {
            $okN++; $sum += $p
            if ([double]::IsNaN($mn) -or $p -lt $mn) { $mn = $p }
            if ([double]::IsNaN($mx) -or $p -gt $mx) { $mx = $p }
            if (-not [double]::IsNaN($prevOk)) { $jit += [Math]::Abs($p - $prevOk); $jn++ }
            $prevOk = $p
        }
    }
    $avg = if ($okN) { $sum / $okN } else { [double]::NaN }
    $jitter = if ($jn) { $jit / $jn } else { [double]::NaN }
    $loss = if ($pingRaw.Count) { 100.0 * ($pingRaw.Count - $okN) / $pingRaw.Count } else { 0.0 }
    $end = $x + $pw - 2
    $xx = $D::Text($x + 2, $y + 1, 'avg ', $C.Muted, $C.Panel)
    $xx = $D::Text($xx, $y + 1, (Fmt $avg '0.0'), $D::Ramp($D::Sev(5, $avg)), $C.Panel)
    $xx = $D::Text($xx, $y + 1, '  min ', $C.Muted, $C.Panel)
    $xx = $D::Text($xx, $y + 1, (Fmt $mn '0'), $D::Ramp($D::Sev(5, $mn)), $C.Panel)
    $xx = $D::Text($xx, $y + 1, '  max ', $C.Muted, $C.Panel)
    $xx = $D::Text($xx, $y + 1, (Fmt $mx '0'), $D::Ramp($D::Sev(5, $mx)), $C.Panel)
    $xx = $D::TextMax($xx, $y + 1, ('  jitter ' + (Fmt $jitter '0.0') + ' ms   loss '), $C.Muted, $C.Panel, $end - $xx)
    $lossSev = if ($loss -le 0) { 0.3 } elseif ($loss -lt 2) { 0.6 } elseif ($loss -lt 10) { 0.8 } else { 0.95 }
    [void]$D::TextMax($xx, $y + 1, ((Fmt $loss '0.0') + '%'), $D::Ramp($lossSev), $C.Panel, $end - $xx)

    $gx = $x + 6; $gy = $y + 2; $gw = $pw - 8; $gh = $ph - 4
    if ($gh -ge 2 -and $gw -ge 4) {
        $D::Area($gx, $gy, $gw, $gh, $sr.ping, $script:pingScale, 5, $script:tick, $C.Graph, $C.Grid)
        Draw-Axis $gx $gy $gh (Fmt $script:pingScale '0') (Fmt ($script:pingScale / 2) '0')
    }
    # recent probes as colored tokens, newest on the right
    $ry = $y + $ph - 2
    $tokens = New-Object 'System.Collections.Generic.List[object]'
    $room = $pw - 12
    for ($i = $pingRaw.Count - 1; $i -ge 0 -and $room -gt 0; $i--) {
        $v = $pingRaw[$i]
        $t = if ($v -lt 0) { [string][char]0xD7 } else { $v.ToString('0', $Inv) }
        $room -= ($t.Length + 1)
        if ($room -lt 0) { break }
        $tokens.Insert(0, @($t, $v))
    }
    $xx = $D::Text($x + 2, $ry, 'recent ', $C.Dim, $C.Panel)
    foreach ($tk in $tokens) { $xx = $D::Text($xx, $ry, ($tk[0] + ' '), $D::Ramp($D::Sev(5, $tk[1])), $C.Panel) }
}

function Render {
    $scrW = $D::W; $scrH = $D::H
    $D::Clear($C.Bg)

    # ---- header ----
    $D::Fill(0, 0, $scrW, 1, $C.Head)
    $xx = $D::Text(1, 0, ([string][char]0x25A0), $C.Accent, $C.Head)
    $xx = $D::Text($xx + 1, 0, 'LM STUDIO', $C.Text, $C.Head)
    $xx = $D::Text($xx, 0, '  /  SYSTEM PERFORMANCE', $C.Muted, $C.Head)
    $right = "{0}   {1} col/s   [+/-] speed   [P] pause   [Q] quit " -f (Get-Date -Format 'HH:mm:ss'), $script:ScrollPerSecond
    $rx = $scrW - $right.Length
    if ($rx -gt $xx + 2) { [void]$D::Text($rx, 0, $right, $C.Muted, $C.Head) }
    if ($script:paused -and $rx -gt $xx + 12) { [void]$D::Text($rx - 10, 0, ' PAUSED ', $C.Bg, $D::Ramp(0.55)) }

    # ---- summary chips + shared color legend ----
    $chips = @(
        @('CPU', (Pct $sr.cpu.Live), ($D::Sev(0, $sr.cpu.Live))),
        @('GPU', (Pct $sr.gpu.Live), ($D::Sev(0, $sr.gpu.Live))),
        @('RAM', (Pct $sr.ram.Live), ($D::Sev(1, $sr.ram.Live))),
        @('VRAM', (Pct $sr.vram.Live), ($D::Sev(2, $sr.vram.Live))),
        @('CPU TEMP', (Deg $sr.cpuTemp.Live), ($D::Sev(3, $sr.cpuTemp.Live))),
        @('GPU TEMP', (Deg $sr.gpuTemp.Live), ($D::Sev(4, $sr.gpuTemp.Live))),
        @('PING', $(if ([double]::IsNaN($sr.ping.Live)) { '--' } elseif ($sr.ping.Live -lt 0) { 'TIMEOUT' } else { (Fmt $sr.ping.Live '0') + ' ms' }), ($D::Sev(5, $sr.ping.Live)))
    )
    $legendW = 20
    $legendX = $scrW - ($legendW + 17)
    $cx = 1
    foreach ($chip in $chips) {
        $need = $chip[0].Length + $chip[1].Length + 4
        if ($cx + $need -gt $scrW) { break }
        $cx = $D::Text($cx, 1, ($chip[0] + ' '), $C.Muted, -1)
        $cx = $D::Text($cx, 1, $chip[1], $D::Ramp($chip[2]), -1) + 3
    }
    if ($cx -lt $legendX) {
        $lx = $D::Text($legendX, 1, 'SAFE ', $D::Ramp(0.36), -1)
        $D::Legend($lx, 1, $legendW)
        [void]$D::Text($lx + $legendW, 1, ' CRITICAL', $D::Ramp(0.95), -1)
    }

    # ---- body ----
    $bodyY = 3
    $bodyH = $scrH - 4
    if ($scrW -ge 110 -and $bodyH -ge 26) {
        $lw = [int][Math]::Floor(($scrW - 3) * 0.62)
        $rxp = 1 + $lw + 1
        $rw = $scrW - $rxp - 1
        $hs = Split-Heights $bodyH 4
        $yy = $bodyY
        Panel-CPU 1 $yy $lw $hs[0]; $yy += $hs[0]
        Panel-GPU 1 $yy $lw $hs[1]; $yy += $hs[1]
        Panel-Mem 1 $yy $lw $hs[2]; $yy += $hs[2]
        Panel-Net 1 $yy $lw $hs[3]
        $lmH = 8
        $thH = [Math]::Max(9, [int][Math]::Floor($bodyH * 0.34))
        $pgH = $bodyH - $thH - $lmH
        if ($pgH -lt 8) { $thH = [Math]::Max(6, $bodyH - $lmH - 8); $pgH = $bodyH - $thH - $lmH }
        Panel-Thermal $rxp $bodyY $rw $thH
        Panel-LM $rxp ($bodyY + $thH) $rw $lmH
        Panel-Ping $rxp ($bodyY + $thH + $lmH) $rw $pgH
    } elseif ($bodyH -ge 10) {
        $pw = $scrW - 2
        $hs = Split-Heights $bodyH 5
        $yy = $bodyY
        Panel-CPU 1 $yy $pw $hs[0]; $yy += $hs[0]
        Panel-GPU 1 $yy $pw $hs[1]; $yy += $hs[1]
        Panel-Mem 1 $yy $pw $hs[2]; $yy += $hs[2]
        Panel-Net 1 $yy $pw $hs[3]; $yy += $hs[3]
        Panel-Ping 1 $yy $pw $hs[4]
    } else {
        [void]$D::Text(2, $bodyY, 'Window too small - enlarge it (or go full screen) to see the graphs.', $C.Muted, -1)
    }

    # ---- footer ----
    $D::Fill(0, $scrH - 1, $scrW, 1, $C.Head)
    if ($script:lastErr) {
        [void]$D::TextMax(1, $scrH - 1, ('render error: ' + $script:lastErr), $D::Ramp(0.95), $C.Head, $scrW - 2)
    } else {
        $f = "GPU: {0}   /   VRAM: {1}   /   Temps: {2}   /   {3} fps" -f $S.GpuSrc, $S.VramSrc, $S.TempSrc, (Fmt $script:fps '0')
        [void]$D::TextMax(1, $scrH - 1, $f, $C.Dim, $C.Head, $scrW - 2)
    }
}

# ============================ main loop ============================
$e = [char]27
$D::EnableVT()
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch {}
try { [Console]::TreatControlCAsInput = $true } catch {}
$D::Raw("$e[?1049h$e[?25l$e[2J")
$frameMs = [Math]::Max(10, [int](1000 / [Math]::Max(1, $TargetFps)))
$lastW = -1; $lastH = -1
$clock = [Diagnostics.Stopwatch]::StartNew()
$prevMs = 0.0
$nextScroll = 0.0
$quit = $false
try {
    while (-not $quit) {
        $nowMs = $clock.Elapsed.TotalMilliseconds
        $dt = [Math]::Max(0.001, ($nowMs - $prevMs) / 1000.0)
        $prevMs = $nowMs

        # ---- keys ----
        try {
            while ([Console]::KeyAvailable) {
                $k = [Console]::ReadKey($true)
                $kc = ([string]$k.KeyChar).ToLowerInvariant()
                if ($kc -eq 'q' -or $k.Key -eq [ConsoleKey]::Escape -or ($k.Key -eq [ConsoleKey]::C -and ($k.Modifiers -band [ConsoleModifiers]::Control))) { $quit = $true }
                elseif ($kc -eq '+' -or $kc -eq '=') { $i = [Array]::IndexOf($speeds, $script:ScrollPerSecond); if ($i -lt 0) { $i = 3 }; $script:ScrollPerSecond = $speeds[[Math]::Min($speeds.Count - 1, $i + 1)] }
                elseif ($kc -eq '-' -or $kc -eq '_') { $i = [Array]::IndexOf($speeds, $script:ScrollPerSecond); if ($i -lt 0) { $i = 3 }; $script:ScrollPerSecond = $speeds[[Math]::Max(0, $i - 1)] }
                elseif ($kc -eq 'p') { $script:paused = -not $script:paused }
            }
        } catch {}
        if ($quit) { break }

        # ---- terminal size ----
        $cw = 120; $chh = 40
        try { $cw = [Console]::WindowWidth; $chh = [Console]::WindowHeight } catch {}
        if ($cw -ne $lastW -or $chh -ne $lastH) {
            $D::Resize([Math]::Max(20, $cw - 1), [Math]::Max(8, $chh))
            $lastW = $cw; $lastH = $chh
        }

        # ---- latest samples from the background threads ----
        $sr.cpu.Target = Num $S.Cpu
        $sr.gpu.Target = Num $S.GpuLoad
        $sr.ram.Target = Num $S.RamPct
        $sr.vram.Target = Num $S.VramPct
        $sr.down.Target = Num $S.Down
        $sr.up.Target = Num $S.Up
        $sr.cpuTemp.Target = Num $S.CpuTemp
        $sr.gpuTemp.Target = Num $S.GpuTemp
        $sr.disk.Target = Num $S.Disk
        $lmRunning = ([int]$S.LmCount) -gt 0
        $sr.lmCpu.Target = if ($lmRunning) { Num $S.LmCpu } else { 0.0 }
        $sr.lmGpu.Target = if ($lmRunning) { Num $S.LmGpu } else { 0.0 }
        $rt = Num $S.RamTotal
        $sr.lmRam.Target = if ($lmRunning -and $rt -gt 0) { 100.0 * (Num $S.LmRam) / $rt } else { 0.0 }

        $item = $null
        while ($S.PingQ.TryDequeue([ref]$item)) {
            $pv = [double]$item
            $pingRaw.Add($pv)
            if ($pingRaw.Count -gt 60) { $pingRaw.RemoveAt(0) }
            if ($pv -lt 0) { $script:pingTimeoutFlag = $true }
            $sr.ping.Target = $pv
        }

        # ---- smoothing ----
        $a = 1.0 - [Math]::Exp(-$dt / [Math]::Max(0.01, $EaseSeconds))
        foreach ($key in $easeKeys) { $sr[$key].Ease($a) }
        $sr.ping.Live = $sr.ping.Target
        $netTarget = [Math]::Max(0.1, [Math]::Max($sr.down.MaxRecent(400), $sr.up.MaxRecent(400)) * 1.25)
        $script:netScale += ($netTarget - $script:netScale) * [Math]::Min(1.0, $a * 0.6)
        $pm = $sr.ping.MaxRecent(400)
        $pingTarget = [Math]::Max(25.0, [Math]::Min(1000.0, [Math]::Ceiling($pm * 1.25 / 25.0) * 25.0))
        $script:pingScale += ($pingTarget - $script:pingScale) * [Math]::Min(1.0, $a * 0.6)

        # ---- scroll history right-to-left ----
        $step = 1000.0 / $script:ScrollPerSecond
        if ($nowMs - $nextScroll -gt 1000) { $nextScroll = $nowMs }
        while ($nowMs -ge $nextScroll) {
            if (-not $script:paused) {
                foreach ($key in $easeKeys) { $sr[$key].Push($sr[$key].Live) }
                if ($script:pingTimeoutFlag) { $sr.ping.Push(-1.0) } else { $sr.ping.Push($sr.ping.Live) }
                $script:pingTimeoutFlag = $false
                $script:tick++
            }
            $nextScroll += $step
        }

        # ---- draw ----
        try { Render; $script:lastErr = $null } catch { $script:lastErr = $_.Exception.Message }
        $D::Flush()

        $spent = $clock.Elapsed.TotalMilliseconds - $nowMs
        $sleep = [int]($frameMs - $spent)
        if ($sleep -gt 1) { [System.Threading.Thread]::Sleep($sleep) }
        $frameTotal = $clock.Elapsed.TotalMilliseconds - $nowMs
        if ($frameTotal -gt 0) { $script:fps += ((1000.0 / $frameTotal) - $script:fps) * 0.1 }
    }
} finally {
    $S.Run = $false
    $D::Raw("$e[0m$e[?25h$e[?1049l")
    try { [Console]::TreatControlCAsInput = $false } catch {}
    foreach ($wk in $workers) { try { [void]$wk.Handle.AsyncWaitHandle.WaitOne(1500) } catch {} }
    if ($S.NvPid) { try { Stop-Process -Id $S.NvPid -Force -ErrorAction SilentlyContinue } catch {} }
    foreach ($wk in $workers) { try { $wk.PS.Stop() } catch {}; try { $wk.PS.Dispose() } catch {} }
}
