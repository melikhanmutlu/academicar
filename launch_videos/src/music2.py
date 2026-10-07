"""120 BPM soundtrack for the AcademicAR showreel (60 s), cut to the reel's beat grid."""
import wave
import numpy as np

SR, DUR, B = 44100, 60.0, 0.5
N = int(SR * (DUR + 3))
L = np.zeros(N); R = np.zeros(N)
rng = np.random.default_rng(9)


def hz(m): return 440.0 * 2 ** ((m - 69) / 12)
def tt(d): return np.arange(int(d * SR)) / SR


def add(t0, sig, gain=1.0, pan=0.0):
    i = int(round(t0 * SR))
    if i < 0: sig, i = sig[-i:], 0
    j = min(N, i + len(sig)); sig = sig[: j - i]
    L[i:j] += sig * gain * np.sqrt(1 - pan); R[i:j] += sig * gain * np.sqrt(1 + pan)


def band(d, lo, hi):
    n = rng.standard_normal(int(d * SR)); F = np.fft.rfft(n); f = np.fft.rfftfreq(len(n), 1 / SR)
    F[(f < lo) | (f > hi)] = 0; o = np.fft.irfft(F, len(n)); return o / (np.abs(o).max() + 1e-9)


def kick(a=0.6):
    t = tt(0.4); return np.sin(2 * np.pi * (48 * t + 110 / 30 * (1 - np.exp(-30 * t)))) * np.exp(-t * 8) * a
def clap(a=0.22):
    t = tt(0.25); n = band(0.25, 900, 6000); env = np.exp(-t * 18) * (1 + 0.6 * np.sin(2 * np.pi * 90 * t).clip(0)); return n * env * a
def hat(a=0.05, d=0.06):
    t = tt(d); return np.diff(rng.standard_normal(len(t) + 1)) * np.exp(-t * 80) * a
def saw(f, t, H=10):
    return sum(np.sin(2 * np.pi * f * h * t) / h for h in range(1, H))
def bass(m, d, a=0.16):
    t = tt(d); return saw(hz(m), t, 6) * np.minimum(1, t / 0.005) * np.exp(-t * 5) * a
def stab(ms, a=0.06, d=0.35):
    t = tt(d); return sum(saw(hz(m), t, 8) for m in ms) * np.exp(-t * 9) * np.minimum(1, t / 0.003) * a
def pluck(m, a=0.05, d=0.4):
    t = tt(d); f = hz(m); return (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(4 * np.pi * f * t)) * np.exp(-t * 10) * a
def chime(ms, a=0.05, d=1.5):
    t = tt(d); return sum(np.sin(2 * np.pi * hz(m) * t) * np.exp(-t * 3) for m in ms) * a
def boom(a=0.6, d=2.5):
    t = tt(d); s = np.sin(2 * np.pi * (36 * t + 60 / 5 * (1 - np.exp(-5 * t)))) * np.exp(-t * 2)
    return (s + 0.6 * band(d, 30, 500) * np.exp(-t * 7)) * a
def riser(d, a=0.12):
    t = tt(d); n = band(d, 500, 9000); return n * (t / d) ** 3 * a + np.sin(2 * np.pi * (200 * t + 900 * t * t / d)) * (t / d) ** 2 * a * 0.4
def zap(a=0.08):
    t = tt(0.12); return np.sin(2 * np.pi * (2400 * t - 8000 * t * t)) * np.exp(-t * 30) * a
def pad(ms, d, a=0.02):
    t = tt(d + 1); s = sum(saw(hz(m), t, 5) * (1 + 0.3 * np.sin(2 * np.pi * 0.5 * t)) for m in ms)
    return s * np.minimum(1, t / 0.4) * np.where(t > d, np.exp(-(t - d) * 3), 1) * a


PROG = [[57, 60, 64], [53, 57, 60], [48, 55, 64], [55, 59, 62]]
ROOT = [33, 29, 36, 31]
def chord(t): return int(t // 2) % 4

# ---- hook 0–2
add(0.0, boom(0.7)); add(0.0, stab([69, 72, 76], 0.08))
add(0.5, boom(0.5, 0.6)); add(0.5, stab([65, 69, 72], 0.08)); add(0.5, zap())
add(1.0, boom(0.8, 2.0)); add(1.0, chime([81, 88, 93], 0.05)); add(1.0, riser(1.0, 0.06), pan=0)
# ---- grooves
def groove(a, b, hats16=False, clapOn=True, kickAll=True, bassOn=True):
    t = a
    while t < b - 1e-6:
        bi = int(round((t - a) / B))
        if kickAll or bi % 2 == 0: add(t, kick())
        if clapOn and bi % 2 == 1: add(t, clap())
        for s in (range(4) if hats16 else range(2)):
            add(t + s * (B / (4 if hats16 else 2)), hat(0.05 if s % 2 else 0.03), pan=0.3)
        if bassOn:
            for s in range(2): add(t + B / 2 * s + (B / 2 if s == 0 else 0) * 0, bass(ROOT[chord(t)] + (12 if s else 0), B / 2 - 0.02))
        t += B
groove(2.0, 8.0)
for t0 in [2.0, 2.5, 3.0, 3.5, 4.0, 4.25, 4.5, 4.75, 5.0, 5.25, 5.5, 6.0, 6.5, 7.0, 7.5]:
    add(t0, stab([m + 12 for m in PROG[chord(t0)]], 0.05), pan=0.2 if int(t0 * 4) % 2 else -0.2)
add(7.0, riser(1.0, 0.1))
# ---- logo 8–11
add(8.0, boom(0.5, 1.2))
for i in range(16): add(8.0 + 0.04 * i + 0.4, pluck(69 + [0, 3, 7, 10, 12][i % 5], 0.035), pan=-0.7 + 0.09 * i)
add(9.0, boom(0.6, 1.5)); add(9.0, chime([76, 81, 88], 0.05))
for i in range(10): add(9.6 + i * 0.125, pluck([81, 84, 88, 91, 93, 96, 93, 91, 88, 93][i], 0.04))
groove(9.0, 11.0, clapOn=False, kickAll=False)
add(10.0, pad([57, 64, 69], 4.0, 0.015))
# ---- QR 11–14
groove(11.0, 13.0, hats16=True)
for i in range(16): add(13.0 + i * (1 / 16), clap(0.05 + 0.012 * i))
add(12.0, riser(2.0, 0.13))
for i in range(12): add(11.0 + i * 0.07, zap(0.03))
# ---- viewer 14–41 (drop)
for s in [14, 18, 22, 26, 30, 34, 38]: add(s, boom(0.55, 1.6)); add(s - 0.2, riser(0.2, 0.08))
groove(14.0, 41.0, hats16=True)
t = 14.0
while t < 41.0:
    add(t, pad(PROG[chord(t)], 2.0, 0.012)); t += 2.0
t = 14.0
while t < 41.0:
    for s, off in enumerate([0, 7, 12, 7]): add(t + s * B / 4 * 2, pluck(PROG[chord(t)][0] + 12 + off, 0.03), pan=0.4 if s % 2 else -0.4)
    t += B * 2
for i in range(8): add(22.0 + i * B, chime([84 + [0, 2, 4, 7, 9, 12, 14, 16][i]], 0.04, 0.6))   # finishes
for b in [27, 28, 29]: add(b, zap(0.1))                                                       # section axes
add(30.0, zap(0.06)); add(30.5, zap(0.06)); add(31.5, zap(0.06)); add(32.0, zap(0.06))
for i in range(4): add(32.0 + i * 0.5, chime([88 + i * 2], 0.035, 0.5))                       # labels
for i in range(8): add(34.0 + i * B, stab([m + 12 for m in PROG[chord(34 + i * B)]], 0.04))  # lighting
for i in range(6): add(38.0 + i * B, hat(0.12, 0.15))                                         # scene cuts
# ---- slice breakdown 41–45
add(41.0, boom(0.6, 2.5)); add(41.0, pad([45, 52, 57, 64], 4.0, 0.02))
t = 41.0
while t < 45.0:
    add(t, kick(0.45)) if int(round((t - 41) / B)) % 2 == 0 else None
    add(t + B / 2, hat(0.03), pan=0.4); t += B
for i in range(8): add(41.0 + i * B, pluck([69, 72, 76, 72, 69, 76, 79, 76][i], 0.035, 0.8))
add(43.0, riser(2.0, 0.14))
# ---- figure 45–47.5
add(45.0, boom(0.5, 1.5)); add(45.0, band(0.08, 2000, 10000) * np.exp(-tt(0.08) * 40) * 0.25)  # shutter
groove(45.0, 47.5, clapOn=True, kickAll=True)
add(46.5, riser(1.0, 0.1))
# ---- AR 47.5–52
add(47.5, boom(0.7, 2.0)); add(47.5, stab([69, 72, 76, 81], 0.08))
groove(47.5, 52.0, hats16=True)
for t0 in [48.5, 49.0, 49.5]: add(t0, stab([m + 12 for m in PROG[chord(t0)]], 0.06))
add(49.9, chime([96], 0.04, 0.4)); add(50.8, chime([81, 88, 93, 100], 0.045, 2.0))
add(51.0, riser(1.0, 0.1))
# ---- montage 52–56
groove(52.0, 56.0, hats16=True)
for i in range(16): add(52.0 + i * 0.25, stab([m + 12 for m in PROG[chord(52 + i * 0.25)]], 0.045), pan=0.3 if i % 2 else -0.3)
add(54.0, riser(2.0, 0.16))
# ---- end 56–60
add(56.0, boom(0.85, 3.5)); add(56.0, pad([45, 57, 60, 64, 71], 3.0, 0.022)); add(56.0, chime([69, 76, 81, 88], 0.05, 3.5))
add(57.0, pluck(93, 0.04, 1.0)); add(58.0, pluck(88, 0.03, 1.0))

mix = np.stack([L, R], 1)[: int(SR * DUR)]
t = np.arange(len(mix)) / SR
mix *= np.clip((DUR - t) / 1.2, 0, 1)[:, None]
mix = np.tanh(mix * 1.6) / np.tanh(1.6)
mix /= np.abs(mix).max() / 0.9
with wave.open('music2.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((mix * 32767).astype('<i2').tobytes())
print('ok')
