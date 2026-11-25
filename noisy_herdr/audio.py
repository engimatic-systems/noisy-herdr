"""Local playback with a per-plugin gain; system volume is never changed."""
import array
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from .storage import SOUND_IDS

SOUND_DIR = Path(__file__).resolve().parent.parent / "sounds"


def scaled_wav(source, target, volume):
    with wave.open(str(source), "rb") as stream:
        params = stream.getparams()
        if params.sampwidth != 2 or params.comptype != "NONE":
            raise ValueError("Expected 16-bit PCM audio")
        samples = array.array("h", stream.readframes(params.nframes))
    if sys.byteorder != "little":
        samples.byteswap()
    gain = volume / 100
    samples = array.array("h", (round(sample * gain) for sample in samples))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(target), "wb") as stream:
        stream.setparams(params)
        stream.writeframes(samples.tobytes())


def play(sound, volume):
    if sound not in SOUND_IDS:
        raise ValueError("Unknown sound")
    if not 0 <= volume <= 100:
        raise ValueError("Volume must be from 0 to 100")
    if volume == 0:
        return
    source = SOUND_DIR / (sound + ".wav")
    if sys.platform == "darwin":
        command = ["/usr/bin/afplay", "-v", str(volume / 100), str(source)]
        subprocess.run(command, check=True, timeout=10, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return
    player = next((name for name in ("paplay", "pw-play", "aplay", "ffplay", "mpv")
                   if shutil.which(name)), None)
    if player is None:
        raise RuntimeError("Install paplay, pw-play, aplay, ffplay, or mpv for audio playback")
    fd, path = tempfile.mkstemp(prefix="noisy-herdr-", suffix=".wav")
    os.close(fd)
    try:
        scaled_wav(source, path, volume)
        flags = {"ffplay": ["-nodisp", "-autoexit", "-loglevel", "error"],
                 "mpv": ["--no-video", "--really-quiet"]}.get(player, [])
        subprocess.run([player, *flags, path], check=True, timeout=10,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE)
    finally:
        os.unlink(path)
