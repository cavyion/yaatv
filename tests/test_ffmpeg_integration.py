from __future__ import annotations

import math
import shutil
import struct
import subprocess
import wave
from io import StringIO
from pathlib import Path

import pytest
from PIL import Image

from yaatv.cli import run


pytestmark = pytest.mark.integration


def test_cli_encodes_valid_mp4_with_ffmpeg(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/FFprobe is not installed")

    audio_path = tmp_path / "tone.wav"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "output.mp4"

    _write_sine_wave(audio_path)
    Image.new("RGB", (320, 240), (24, 84, 128)).save(image_path, "JPEG")

    stderr = StringIO()
    exit_code = run(
        [
            "--audio",
            str(audio_path),
            "--image",
            str(image_path),
            "--output",
            str(output_path),
            "--no-warn",
        ],
        stdin=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert f"Created {output_path}" in stderr.getvalue()
    assert "Verified: 1920x1080" in stderr.getvalue()
    assert "AAC 48kHz" in stderr.getvalue()

    duration = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert float(duration.stdout.strip()) <= 3

    verification = subprocess.run(
        [ffmpeg, "-v", "error", "-i", str(output_path), "-f", "null", "-"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verification.returncode == 0, verification.stderr


def _write_sine_wave(path: Path, duration: float = 1.25, sample_rate: int = 44_100) -> None:
    frame_count = int(duration * sample_rate)
    frames = bytearray()

    for index in range(frame_count):
        sample = int(32767 * 0.2 * math.sin(2 * math.pi * 440 * index / sample_rate))
        frames.extend(struct.pack("<h", sample))

    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(frames)
