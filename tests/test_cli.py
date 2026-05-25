import os
import sys
from pathlib import Path
from io import StringIO

import pytest
from PIL import Image

from yaatv.cli import (
    AudioMetadata,
    OutputStats,
    YaatvError,
    build_ffmpeg_command,
    choose_audio_plan,
    confirm_overwrite,
    default_output_path,
    find_ffmpeg,
    find_ffprobe,
    format_output_stats,
    is_high_quality_aac,
    pad_seconds,
    quality_warnings,
    read_audio_metadata,
    sanitize_filename,
    validate_image,
    verify_output_stats,
)


def _executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def test_transcode_command_uses_required_youtube_settings() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=2,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=(2560, 1440),
        audio_plan=plan,
        overwrite=False,
    )

    assert command[:8] == ["ffmpeg", "-n", "-loop", "1", "-framerate", "1", "-i", "cover.jpg"]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-preset") + 1] == "slow"
    assert command[command.index("-crf") + 1] == "16"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-color_range") + 1] == "tv"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "384k"
    assert command[command.index("-ar") + 1] == "48000"
    assert command[command.index("-af") + 1] == "apad=pad_dur=2"
    assert "-shortest" in command
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert command[command.index("-vf") + 1] == (
        "scale=2560:1440:force_original_aspect_ratio=decrease:out_range=tv,"
        "pad=2560:1440:(ow-iw)/2:(oh-ih)/2:black,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )


def test_command_uses_shortest_without_duration_cap() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="mp3", bitrate=128_000, sample_rate=48_000, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.mp3"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=(1920, 1080),
        audio_plan=plan,
        overwrite=False,
    )

    assert "-shortest" in command
    assert "-t" not in command


def test_command_uses_duration_cap_when_audio_duration_is_known() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="mp3", bitrate=128_000, sample_rate=48_000, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.mp3"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=(1920, 1080),
        audio_plan=plan,
        overwrite=False,
        output_duration=145,
    )

    assert "-shortest" in command
    assert command[command.index("-t") + 1] == "145"
    assert command.index("-t") < len(command) - 1


def test_high_quality_aac_is_copied() -> None:
    metadata = AudioMetadata(
        codec="mp4a.40.2",
        bitrate=320_000,
        sample_rate=48_000,
        artist="Artist",
        title="Title",
    )

    assert is_high_quality_aac(metadata)
    assert choose_audio_plan(metadata, pad=0).codec_args == ("-c:a", "copy")


def test_pad_rejects_high_quality_aac_copy_mode() -> None:
    metadata = AudioMetadata(
        codec="aac",
        bitrate=384_000,
        sample_rate=48_000,
        artist=None,
        title=None,
    )

    with pytest.raises(Exception, match="--pad cannot be used"):
        choose_audio_plan(metadata, pad=1)


def test_low_bitrate_warning_is_reported() -> None:
    warnings = quality_warnings(
        AudioMetadata(codec="mp3", bitrate=192_000, sample_rate=44_100, artist=None, title=None),
        image_size=(1920, 1080),
        target_size=(1920, 1080),
    )

    assert warnings == ["source audio bitrate is 192kbps, below the 256kbps warning threshold"]


def test_default_output_prefers_artist_and_title() -> None:
    metadata = AudioMetadata(
        codec="flac",
        bitrate=900_000,
        sample_rate=44_100,
        artist='AC/DC: "Live"',
        title="Track / One",
    )

    assert default_output_path(Path("input.flac"), metadata) == Path('AC_DC_ _Live_ - Track _ One.mp4')


def test_default_output_falls_back_to_audio_stem() -> None:
    metadata = AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None)

    assert default_output_path(Path("input.flac"), metadata) == Path("input.mp4")


def test_pad_seconds_validates_range() -> None:
    assert pad_seconds("0") == 0
    assert pad_seconds("10") == 10

    with pytest.raises(Exception, match="between 0 and 10"):
        pad_seconds("11")


def test_sanitize_filename_has_fallback() -> None:
    assert sanitize_filename(' <>:"/\\|?* ') == "_________"


def test_find_ffmpeg_reports_download_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(YaatvError, match="https://ffmpeg.org/download.html"):
        find_ffmpeg()


def test_find_ffprobe_reports_download_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(YaatvError, match="https://ffmpeg.org/download.html"):
        find_ffprobe()


def test_find_ffmpeg_prefers_pyinstaller_bundled_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bin" / _executable_name("ffmpeg")
    bundled.parent.mkdir()
    bundled.write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    assert find_ffmpeg() == str(bundled)


def test_find_ffprobe_uses_adjacent_bin_for_frozen_onedir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bin" / _executable_name("ffprobe")
    bundled.parent.mkdir()
    bundled.write_bytes(b"")
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / _executable_name("yaatv")))
    monkeypatch.setattr("shutil.which", lambda _: None)

    assert find_ffprobe() == str(bundled)


def test_unreadable_audio_reports_user_facing_error(tmp_path: Path) -> None:
    audio_path = tmp_path / "not-audio.mp3"
    audio_path.write_text("not audio", encoding="utf-8")

    with pytest.raises(YaatvError, match="Could not read audio metadata"):
        read_audio_metadata(audio_path)


def test_animated_image_is_rejected(tmp_path: Path) -> None:
    image_path = tmp_path / "cover.gif"
    frames = [
        Image.new("RGB", (12, 12), (255, 0, 0)),
        Image.new("RGB", (12, 12), (0, 0, 255)),
    ]
    frames[0].save(image_path, save_all=True, append_images=frames[1:], duration=100, loop=0)

    with pytest.raises(YaatvError, match="static image"):
        validate_image(image_path)


def test_existing_output_refuses_noninteractive_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    output.write_bytes(b"existing")

    with pytest.raises(YaatvError, match="without confirmation"):
        confirm_overwrite(output, stdin=StringIO(), stderr=StringIO())


def test_verify_output_stats_accepts_expected_youtube_profile() -> None:
    stats = OutputStats(
        width=1920,
        height=1080,
        video_codec="h264",
        pixel_format="yuv420p",
        color_range="tv",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        frame_rate=1.0,
        audio_codec="aac",
        audio_sample_rate=48_000,
    )

    verify_output_stats(stats, (1920, 1080))

    assert format_output_stats(stats) == (
        "1920x1080, H.264/yuv420p, bt709, 1fps video, AAC 48kHz"
    )


def test_verify_output_stats_rejects_wrong_profile() -> None:
    stats = OutputStats(
        width=1280,
        height=720,
        video_codec="h264",
        pixel_format="yuv420p",
        color_range="tv",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        frame_rate=1.0,
        audio_codec="aac",
        audio_sample_rate=48_000,
    )

    with pytest.raises(YaatvError, match="expected 1920x1080"):
        verify_output_stats(stats, (1920, 1080))
