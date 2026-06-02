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

from yaatv.cli import probe_output, run

pytestmark = pytest.mark.integration


def test_cli_encodes_valid_mp4_with_ffmpeg(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _require_ffmpeg_tools()

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
    assert "File:" in stderr.getvalue()

    _assert_valid_output(ffmpeg, ffprobe, output_path, expected_size=(1920, 1080), max_duration=3)


def test_cli_encodes_square_mp4_with_ffmpeg(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _require_ffmpeg_tools()

    audio_path = tmp_path / "tone.wav"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "square.mp4"

    _write_sine_wave(audio_path)
    Image.new("RGB", (320, 240), (24, 84, 128)).save(image_path, "JPEG")

    stderr = StringIO()
    exit_code = run(
        [
            "--audio",
            str(audio_path),
            "--image",
            str(image_path),
            "--aspect",
            "square",
            "--output",
            str(output_path),
            "--no-warn",
        ],
        stdin=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 0
    assert output_path.exists()
    assert "Verified: 1080x1080" in stderr.getvalue()
    _assert_valid_output(ffmpeg, ffprobe, output_path, expected_size=(1080, 1080), max_duration=3)


def test_cli_encodes_valid_mov_with_ffmpeg(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _require_ffmpeg_tools()

    audio_path = tmp_path / "tone.wav"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "output.mov"

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
    assert "note: .mov output uses ProRes 422; file sizes will be very large" in stderr.getvalue()
    assert "Verified: 1920x1080, ProRes 422/yuv422p10le" in stderr.getvalue()
    assert "AAC 48kHz" in stderr.getvalue()
    assert "File:" in stderr.getvalue()

    _assert_valid_output(ffmpeg, ffprobe, output_path, expected_size=(1920, 1080), max_duration=3, is_prores=True)


def test_cli_encodes_with_background_image(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _require_ffmpeg_tools()
    audio_path = tmp_path / "tone.wav"
    image_path = tmp_path / "cover.jpg"
    background_path = tmp_path / "background.jpg"
    output_path = tmp_path / "background-output.mp4"

    _write_sine_wave(audio_path)
    Image.new("RGB", (320, 240), (24, 84, 128)).save(image_path, "JPEG")
    Image.new("RGB", (640, 360), (80, 24, 128)).save(background_path, "JPEG")

    stderr = StringIO()
    exit_code = run(
        [
            "--audio",
            str(audio_path),
            "--image",
            str(image_path),
            "--bg-image",
            str(background_path),
            "--output",
            str(output_path),
            "--no-warn",
        ],
        stdin=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Verified: 1920x1080" in stderr.getvalue()
    _assert_valid_output(ffmpeg, ffprobe, output_path, expected_size=(1920, 1080), max_duration=3)


def test_cli_encodes_with_blurred_background(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _require_ffmpeg_tools()
    audio_path = tmp_path / "tone.wav"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "blur-output.mp4"

    _write_sine_wave(audio_path)
    Image.new("RGB", (320, 240), (24, 84, 128)).save(image_path, "JPEG")

    stderr = StringIO()
    exit_code = run(
        [
            "--audio",
            str(audio_path),
            "--image",
            str(image_path),
            "--bg-blur",
            "--output",
            str(output_path),
            "--no-warn",
        ],
        stdin=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Verified: 1920x1080" in stderr.getvalue()
    _assert_valid_output(ffmpeg, ffprobe, output_path, expected_size=(1920, 1080), max_duration=3)


def test_cli_encodes_color_only_output(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _require_ffmpeg_tools()
    audio_path = tmp_path / "tone.wav"
    output_path = tmp_path / "color-output.mp4"

    _write_sine_wave(audio_path)

    stderr = StringIO()
    exit_code = run(
        [
            "--audio",
            str(audio_path),
            "--bg-color",
            "white",
            "--output",
            str(output_path),
            "--no-warn",
        ],
        stdin=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Verified: 1920x1080" in stderr.getvalue()
    _assert_valid_output(ffmpeg, ffprobe, output_path, expected_size=(1920, 1080), max_duration=3)


def test_cli_encodes_with_padding(tmp_path: Path) -> None:
    ffmpeg, ffprobe = _require_ffmpeg_tools()
    audio_path = tmp_path / "tone.wav"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "pad-output.mp4"

    _write_sine_wave(audio_path)
    Image.new("RGB", (320, 240), (24, 84, 128)).save(image_path, "JPEG")

    stderr = StringIO()
    exit_code = run(
        [
            "--audio",
            str(audio_path),
            "--image",
            str(image_path),
            "--pad",
            "1",
            "--output",
            str(output_path),
            "--no-warn",
        ],
        stdin=StringIO(),
        stderr=stderr,
    )

    assert exit_code == 0
    assert "Verified: 1920x1080" in stderr.getvalue()
    duration = _output_duration(ffprobe, output_path)
    assert 2 <= duration <= 4
    _assert_valid_output(ffmpeg, ffprobe, output_path, expected_size=(1920, 1080), max_duration=4)


def _require_ffmpeg_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/FFprobe is not installed")
    return ffmpeg, ffprobe


def _output_duration(ffprobe: str, output_path: Path) -> float:
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
    return float(duration.stdout.strip())


def _assert_valid_output(
    ffmpeg: str,
    ffprobe: str,
    output_path: Path,
    *,
    expected_size: tuple[int, int],
    max_duration: float,
    is_prores: bool = False,
) -> None:
    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert _output_duration(ffprobe, output_path) <= max_duration
    stats = probe_output(ffprobe, output_path)
    assert (stats.width, stats.height) == expected_size
    if is_prores:
        assert stats.video_codec == "prores"
        assert stats.pixel_format == "yuv422p10le"
    else:
        assert stats.video_codec == "h264"
        assert stats.pixel_format == "yuv420p"
    assert stats.frame_rate is not None
    assert abs(stats.frame_rate - 1.0) <= 0.01
    assert stats.audio_codec == "aac"
    assert stats.audio_sample_rate == 48_000

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
