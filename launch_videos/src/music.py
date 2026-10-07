"""Synthesised soundtrack for the AcademicAR launch video (74 s, 44.1 kHz stereo)."""
import wave
import numpy as np

SR, DUR = 44100, 74.0
N = int(SR * (DUR + 3))
L = np.zeros(N); R = np.zeros(N)
rng = np.random.default_rng(3)
BEAT = 0.6  # 100 BPM


def hz(m): return 440.0 * 2 ** ((m - 69) / 12)


def add(t0, sig, gain=1.0, pan=0.0):
    i = int(t0 * SR)
    if i < 0: sig, i = sig[-i:], 0
    j = min(N, i + len(sig)); sig = sig[: j - i]
    L[i:j] += sig * gain * np.sqrt(0.5 * (1 - pan)) * 1.414
    R[i:j] += sig * gain * np.sqrt(0.5 * (1 + pan)) * 1.414


def tt(d): return np.arange(int(d * SR)) / SR


def bandnoise(d, lo, hi):
    n = rng.standard_normal(int(d * SR)); F = np.fft.rfft(n)
    f = np.fft.rfftfreq(len(n), 1 / SR); F[(f < lo) | (f > hi)] = 0
    out = np.fft.irfft(F, len(n)); return out / (np.abs(out).max() + 1e-9)


# ---------------------------------------------------------------- harmony
CH = {'Am': [57, 60, 64, 71], 'F': [53, 57, 60, 67], 'C': [48, 55, 64, 67], 'G': [55, 59, 62, 69], 'Cadd9': [48, 55, 62, 64, 67]}
ROOT = {'Am': 45, 'F': 41, 'C': 48, 'G': 43, 'Cadd9': 36}
PROG = ['Am', 'F', 'C', 'G']
CLEN, OFF = 4.8, -0.2
chords = []
k = 0
while OFF + k * CLEN < DUR:
    name = PROG[k % 4] if OFF + k * CLEN < 71.5 else 'Cadd9'
    chords.append((OFF + k * CLEN, name)); k += 1
chords[-1] = (chords[-1][0], 'Cadd9')


def pad_note(m, d, amp, det):
    t = tt(d + 2.0); f = hz(m); s = np.zeros_like(t)
    for h in range(1, 7):
        s += (1 / h ** 1.7) * (np.sin(2 * np.pi * f * h * (1 + det) * t) + np.sin(2 * np.pi * f * h * (1 - det) * t + 1.3))
    env = np.minimum(1, t / 1.4) * np.where(t > d, np.exp(-(t - d) * 2.2), 1)
    return s * env * amp


for i, (t0, name) in enumerate(chords):
    d = CLEN if i < len(chords) - 1 else DUR - t0 + 1
    lvl = 0.030 if t0 < 5 else 0.026
    for j, m in enumerate(CH[name]):
        add(t0, pad_note(m, d, lvl, 0.0016 + 0.0004 * j), pan=-0.35 + 0.23 * j)

# ---------------------------------------------------------------- arp
def pluck(m, amp=0.05, d=0.7):
    t = tt(d); f = hz(m)
    return (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)) * np.exp(-t * 7) * np.minimum(1, t / 0.004) * amp


def chord_at(t):
    name = chords[0][1]
    for c0, n in chords:
        if c0 <= t: name = n
    return name


pat = [0, 2, 1, 3, 2, 1, 3, 2]
t = 5.4
while t < 72.0:
    step = int(round((t - 5.4) / (BEAT / 2)))
    notes = CH[chord_at(t)]
    m = notes[pat[step % 8] % len(notes)] + 12
    g = 0.8 if 27.2 < t < 40.6 else 1.0
    add(t, pluck(m, 0.045 * g), pan=0.4 if step % 2 else -0.4)
    t += BEAT / 2

# ---------------------------------------------------------------- drums & bass
def kick(amp=0.55):
    t = tt(0.45); ph = 2 * np.pi * (46 * t + 90 / 28 * (1 - np.exp(-28 * t)))
    return np.sin(ph) * np.exp(-t * 7) * amp


def hat(amp=0.035):
    t = tt(0.08); n = np.diff(rng.standard_normal(len(t) + 1))
    return n * np.exp(-t * 70) * amp


def bass(m, d, amp=0.16):
    t = tt(d); f = hz(m)
    s = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
    return s * np.minimum(1, t / 0.01) * np.exp(-t * 1.8) * amp


b = 10.6
while b < 66.6:
    beat_i = int(round((b - 10.6) / BEAT))
    if beat_i % 2 == 0 or b > 19: add(b, kick(0.42 if b < 19 else 0.5))
    if b > 19.0: add(b + BEAT / 2, hat(), pan=0.25)
    if beat_i % 2 == 0: add(b, bass(ROOT[chord_at(b)], BEAT * 2 - 0.05))
    b += BEAT

# ---------------------------------------------------------------- fx
def whoosh(d=1.1, amp=0.10):
    n = bandnoise(d, 400, 7000); t = tt(d)
    return n * (t / d) ** 2.5 * np.where(t > d - 0.06, np.exp(-(t - d + 0.06) * 60), 1) * amp


def click(amp=0.10, f=2400):
    t = tt(0.05); return (np.sin(2 * np.pi * f * t) * 0.6 + rng.standard_normal(len(t)) * 0.4) * np.exp(-t * 120) * amp


def chime(ms, amp=0.07, d=2.2):
    t = tt(d); s = sum(np.sin(2 * np.pi * hz(m) * t) * np.exp(-t * (2.2 + 0.4 * i)) for i, m in enumerate(ms))
    return s * np.minimum(1, t / 0.003) * amp


def boom(amp=0.5, d=2.4):
    t = tt(d); s = np.sin(2 * np.pi * (38 * t + 50 / 6 * (1 - np.exp(-6 * t)))) * np.exp(-t * 2.2)
    return (s + 0.5 * bandnoise(d, 30, 400) * np.exp(-t * 6)) * amp


for tr in [5.4, 10.6, 19.0, 27.2, 40.4, 48.4, 55.4, 61.0, 67.0]:
    add(tr - 1.05, whoosh(), pan=0)
add(5.45, boom(0.38)); add(5.5, chime([81, 88, 93], 0.05))           # brand reveal
for i in range(6): add(10.6 + 0.9 + i * 0.16 + 0.35, click(0.05, 1800 + 120 * i), pan=0.5)
add(10.6 + 3.45, click(0.08, 1200))                                    # file drop
add(10.6 + 5.35, click(0.12)); add(10.6 + 6.1, click(0.12, 2000))      # checkbox, button
add(19.0 + 4.9, chime([88, 93], 0.07))                                 # Ready
add(27.2 + 10.6, click(0.08, 2600)); add(27.2 + 10.85, click(0.08, 3000))  # measure points
add(40.4 + 1.7, chime([84, 91], 0.05, 1.4))                            # QR complete
add(40.4 + 4.15, chime([96], 0.035, 0.6))                              # scan beep
add(40.4 + 5.2, chime([81, 88, 93, 100], 0.04, 2.5))                   # AR reveal shimmer
for i in range(8): add(48.4 + 0.6 + i * 0.09, click(0.035, 2200 + 90 * i), pan=-0.6 + 0.17 * i)
add(67.0, boom(0.55, 3.0)); add(67.05, chime([72, 79, 84, 88], 0.05, 3.5))

# ---------------------------------------------------------------- master
mix = np.stack([L, R], 1)[: int(SR * DUR)]
t = np.arange(len(mix)) / SR
mix *= np.minimum(1, t / 0.8)[:, None] * np.clip((DUR - t) / 2.2, 0, 1)[:, None]
mix = np.tanh(mix * 1.4) / np.tanh(1.4)
mix /= np.abs(mix).max() / 0.89
with wave.open('music.wav', 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype('<i2').tobytes())
print('ok', len(mix) / SR)
