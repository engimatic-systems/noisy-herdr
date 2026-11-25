#!/usr/bin/env python3
"""Synthesize the five original sounds. No recordings or third-party samples.

Reproducible 44.1 kHz mono PCM; run only when changing the sound design.
The Pomodoro bell uses independently decaying, slightly inharmonic modes to
approximate a struck metal kitchen timer rather than an electronic sine beep.
"""
import array
import math
import sys
import wave
from pathlib import Path

RATE = 44100
TAU = 2 * math.pi
DESTINATION = Path(__file__).resolve().parent.parent / "sounds"


def bell(t):
    modes = ((1568, 1.00, 0.62), (2371, .43, .39), (3248, .23, .25),
             (4579, .11, .15), (6101, .045, .09))
    body = sum(level * math.exp(-t / decay) * math.sin(TAU * hz * t)
               for hz, level, decay in modes)
    # The very close pair gives a small, natural shimmer to the bell's tail.
    body += .13 * math.exp(-t / .46) * math.sin(TAU * 1572.3 * t)
    return body * min(1, t / .0015)


def glass(t):
    return min(1, t / .002) * sum(
        level * math.exp(-t / decay) * math.sin(TAU * hz * t)
        for hz, level, decay in ((2093, 1, .24), (5651, .19, .10), (8191, .06, .05)))


def note(t, hz, decay):
    if t < 0:
        return 0
    return min(1, t / .009) * math.exp(-t / decay) * (
        math.sin(TAU * hz * t) + .12 * math.sin(TAU * hz * 2 * t))


def chime(t):
    return .7 * note(t, 659.25, .30) + .65 * note(t - .16, 987.77, .35)


def wood(t):
    return min(1, t / .001) * (
        math.exp(-t / .035) * math.sin(TAU * 780 * t)
        + .42 * math.exp(-t / .018) * math.sin(TAU * 1795 * t))


def arcade(t):
    result = 0
    for start, hz in ((0, 523.25), (.12, 659.25), (.24, 1046.50)):
        age = t - start
        if 0 <= age < .18:
            envelope = min(1, age / .005, (.18 - age) / .035)
            result += envelope * sum(math.sin(TAU * hz * harmonic * age) / harmonic
                                     for harmonic in (1, 3, 5))
    return result


def write(name, duration, synth):
    count = round(duration * RATE)
    samples = [synth(i / RATE) for i in range(count)]
    peak = max(abs(sample) for sample in samples)
    gain = .72 * 32767 / peak
    # Fade the last 25 ms so even a long resonant tail ends without a click.
    pcm = array.array("h", (round(sample * gain * min(1, (count - i - 1) / (RATE * .025)))
                            for i, sample in enumerate(samples)))
    if sys.byteorder != "little":
        pcm.byteswap()
    path = DESTINATION / (name + ".wav")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(pcm.tobytes())
    print(f"{path.name}: {duration:.2f}s, {path.stat().st_size:,} bytes")


if __name__ == "__main__":
    DESTINATION.mkdir(exist_ok=True)
    for name, duration, synth in (("pomodoro", 1.8, bell), ("glass", .95, glass),
                                  ("chime", 1.5, chime), ("wood", .28, wood),
                                  ("arcade", .65, arcade)):
        write(name, duration, synth)
