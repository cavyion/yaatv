import hashlib
import os
import re
import sys
import zipfile
from io import BytesIO
from io import StringIO
from pathlib import Path

import pytest
from PIL import Image

from yaatv import __version__
from yaatv.cli import (
    AudioMetadata,
    OutputStats,
    YaatvError,
    build_ffmpeg_command,
    choose_audio_plan,
    confirm_overwrite,
    default_output_path,
    find_external_tool,
    format_output_stats,
    install_windows_ffmpeg,
    is_high_quality_aac,
    pad_seconds,
    quality_warnings,
    read_audio_metadata,
    resolve_ffmpeg_tools,
    sanitize_filename,
    validate_image,
    verify_output_stats,
)


def _executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


class _TtyInput(StringIO):
    def isatty(self) -> bool:
        return True


def _ffmpeg_zip_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ffmpeg-build/bin/ffmpeg.exe", b"ffmpeg")
        archive.writestr("ffmpeg-build/bin/ffprobe.exe", b"ffprobe")
        archive.writestr("ffmpeg-build/bin/ffplay.exe", b"ffplay")
        archive.writestr("ffmpeg-build/doc/readme.txt", b"extra")
    return buffer.getvalue()


def _pyproject_version() -> str:
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    if sys.version_info >= (3, 11):
        import tomllib

        return str(tomllib.loads(text)["project"]["version"])

    match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"\s*$', text)
    assert match is not None
    return match.group(1)


def test_runtime_version_matches_project_metadata() -> None:
    assert __version__ == _pyproject_version()


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


def test_find_ffmpeg_uses_app_cache_before_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_bin = tmp_path / "app" / "bin"
    app_bin.mkdir(parents=True)
    cached = app_bin / "ffmpeg.exe"
    cached.write_bytes(b"")
    path_tool = tmp_path / "path" / "ffmpeg.exe"
    path_tool.parent.mkdir()
    path_tool.write_bytes(b"")
    monkeypatch.setattr("shutil.which", lambda _: str(path_tool))

    assert find_external_tool("ffmpeg", "FFmpeg", app_bin_dir=app_bin, packaged_paths=()) == str(cached)


def test_find_ffmpeg_missing_reports_install_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(YaatvError, match="yaatv --install-ffmpeg"):
        find_external_tool("ffmpeg", "FFmpeg", app_bin_dir=tmp_path / "empty", packaged_paths=())


def test_find_ffprobe_missing_reports_install_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(YaatvError, match="yaatv --install-ffmpeg"):
        find_external_tool("ffprobe", "FFprobe", app_bin_dir=tmp_path / "empty", packaged_paths=())


def test_resolve_ffmpeg_tools_noninteractive_does_not_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installed = False

    def install() -> Path:
        nonlocal installed
        installed = True
        return tmp_path

    monkeypatch.setattr("shutil.which", lambda _: None)

    with pytest.raises(YaatvError, match="yaatv --install-ffmpeg"):
        resolve_ffmpeg_tools(
            stdin=StringIO(),
            stderr=StringIO(),
            app_bin_dir=tmp_path / "empty",
            packaged_paths=(),
            install_supported=True,
            installer=install,
        )

    assert not installed


def test_resolve_ffmpeg_tools_interactive_installs_when_confirmed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_bin = tmp_path / "app" / "bin"

    def install() -> Path:
        app_bin.mkdir(parents=True)
        (app_bin / "ffmpeg.exe").write_bytes(b"")
        (app_bin / "ffprobe.exe").write_bytes(b"")
        return app_bin

    monkeypatch.setattr("shutil.which", lambda _: None)

    assert resolve_ffmpeg_tools(
        stdin=_TtyInput("y\n"),
        stderr=StringIO(),
        app_bin_dir=app_bin,
        packaged_paths=(),
        install_supported=True,
        installer=install,
    ) == (str(app_bin / "ffmpeg.exe"), str(app_bin / "ffprobe.exe"))


def test_install_ffmpeg_rejects_checksum_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(b"not the archive")

    monkeypatch.setattr("yaatv.cli._download_url", download)

    with pytest.raises(YaatvError, match="checksum mismatch"):
        install_windows_ffmpeg(
            install_dir=tmp_path / "yaatv" / "bin",
            expected_sha256="0" * 64,
            stderr=StringIO(),
        )

    assert not (tmp_path / "yaatv" / "bin").exists()


def test_install_ffmpeg_extracts_only_ffmpeg_and_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_bytes = _ffmpeg_zip_bytes()
    expected_sha256 = hashlib.sha256(archive_bytes).hexdigest()

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(archive_bytes)

    monkeypatch.setattr("yaatv.cli._download_url", download)
    install_dir = tmp_path / "yaatv" / "bin"

    assert install_windows_ffmpeg(
        install_dir=install_dir,
        expected_sha256=expected_sha256,
        stderr=StringIO(),
    ) == install_dir

    assert (install_dir / "ffmpeg.exe").read_bytes() == b"ffmpeg"
    assert (install_dir / "ffprobe.exe").read_bytes() == b"ffprobe"
    assert not (install_dir / "ffplay.exe").exists()


def test_find_ffmpeg_prefers_pyinstaller_bundled_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bin" / _executable_name("ffmpeg")
    bundled.parent.mkdir()
    bundled.write_bytes(b"")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    assert find_external_tool("ffmpeg", "FFmpeg", app_bin_dir=tmp_path / "empty") == str(bundled)


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

    assert find_external_tool("ffprobe", "FFprobe", app_bin_dir=tmp_path / "empty") == str(bundled)


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
