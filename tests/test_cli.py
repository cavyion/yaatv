import hashlib
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from io import BytesIO, StringIO
from pathlib import Path

import pytest
from PIL import Image

from yaatv import __version__
from yaatv.cli import (
    MACOS_ARM64_FFMPEG_ARCHIVE_URL,
    MACOS_ARM64_FFPROBE_ARCHIVE_URL,
    AudioMetadata,
    OutputStats,
    YaatvError,
    _download_url,
    build_ffmpeg_command,
    choose_audio_plan,
    confirm_overwrite,
    default_output_path,
    find_external_tool,
    format_output_stats,
    input_format_warnings,
    install_linux_ffmpeg,
    install_macos_ffmpeg,
    install_windows_ffmpeg,
    is_high_quality_aac,
    normalize_output_path,
    pad_seconds,
    probe_output,
    quality_warnings,
    read_audio_metadata,
    resolve_ffmpeg_tools,
    run,
    run_ffmpeg,
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


def _ffmpeg_tar_bytes() -> bytes:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        for name, data in {
            "ffmpeg-build/bin/ffmpeg": b"ffmpeg",
            "ffmpeg-build/bin/ffprobe": b"ffprobe",
            "ffmpeg-build/bin/ffplay": b"ffplay",
            "ffmpeg-build/doc/readme.txt": b"extra",
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, BytesIO(data))
    return buffer.getvalue()


def _single_tool_zip_bytes(tool_name: str, data: bytes) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(tool_name, data)
        archive.writestr(f"__MACOSX/._{tool_name}", b"metadata")
        archive.writestr("readme.txt", b"extra")
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


def test_readme_release_tag_matches_project_metadata() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert f"git tag v{_pyproject_version()}" in readme


def test_release_workflow_checks_tag_version_before_building() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "Validate release tag version" in workflow
    assert "tag_version=\"${GITHUB_REF_NAME#v}\"" in workflow
    assert "pyproject.toml" in workflow


def test_release_workflow_does_not_bundle_ffmpeg_tools() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "Prepare FFmpeg release notes" in workflow
    assert "--add-binary" not in workflow
    assert "vendor/bin" not in workflow
    assert '"$executable" --install-ffmpeg' in workflow
    assert "XDG_DATA_HOME" in workflow
    assert "LOCALAPPDATA" in workflow


def test_audio_and_image_are_required_for_encoding() -> None:
    with pytest.raises(YaatvError, match="Audio file is required"):
        run([], stdin=StringIO(), stderr=StringIO())

    with pytest.raises(YaatvError, match="Cover image is required"):
        run(["--audio", "track.wav"], stdin=StringIO(), stderr=StringIO())


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
    assert command[command.index("-map") + 1] == "0:v:0"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "1:a:0"
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
        output_duration=None,
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


def test_small_cover_warning_recommends_target_size() -> None:
    warnings = quality_warnings(
        AudioMetadata(codec="mp3", bitrate=320_000, sample_rate=48_000, artist=None, title=None),
        image_size=(640, 640),
        target_size=(1920, 1080),
    )

    assert warnings == [
        "cover image is 640x640; FFmpeg will upscale it for 1920x1080. "
        "Consider using an image at least 1920x1080"
    ]


def test_unusual_input_extensions_warn_before_encoding() -> None:
    assert input_format_warnings(Path("track.audio"), Path("cover.picture")) == [
        "audio file extension is unusual: .audio",
        "cover image extension is unusual: .picture",
    ]

    assert input_format_warnings(Path("track.flac"), Path("cover.png")) == []


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


def test_sanitize_filename_prefixes_windows_reserved_device_names() -> None:
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("con") == "_con"
    assert sanitize_filename("NUL.txt") == "_NUL.txt"
    assert sanitize_filename("COM1") == "_COM1"
    assert sanitize_filename("LPT9") == "_LPT9"


def test_default_output_avoids_windows_reserved_audio_stem() -> None:
    metadata = AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None)

    assert default_output_path(Path("COM1.flac"), metadata) == Path("_COM1.mp4")


def test_find_ffmpeg_uses_app_cache_before_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_bin = tmp_path / "app" / "bin"
    app_bin.mkdir(parents=True)
    cached = app_bin / _executable_name("ffmpeg")
    cached.write_bytes(b"")
    path_tool = tmp_path / "path" / _executable_name("ffmpeg")
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
        (app_bin / _executable_name("ffmpeg")).write_bytes(b"")
        (app_bin / _executable_name("ffprobe")).write_bytes(b"")
        return app_bin

    monkeypatch.setattr("shutil.which", lambda _: None)

    assert resolve_ffmpeg_tools(
        stdin=_TtyInput("y\n"),
        stderr=StringIO(),
        app_bin_dir=app_bin,
        packaged_paths=(),
        install_supported=True,
        installer=install,
    ) == (str(app_bin / _executable_name("ffmpeg")), str(app_bin / _executable_name("ffprobe")))


def test_run_install_ffmpeg_uses_general_installer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def install(*, stderr: StringIO) -> Path:
        nonlocal called
        called = True
        return tmp_path

    monkeypatch.setattr("yaatv.cli.install_ffmpeg", install)

    assert run(["--install-ffmpeg"], stderr=StringIO()) == 0
    assert called


def test_run_dry_run_prints_command_without_encoding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "out.mp4"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")
    stderr = StringIO()

    monkeypatch.setattr("yaatv.cli.resolve_ffmpeg_tools", lambda **_kwargs: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(
        "yaatv.cli.read_audio_metadata",
        lambda _path: AudioMetadata(
            codec="flac",
            bitrate=900_000,
            sample_rate=44_100,
            artist=None,
            title=None,
            duration=12.1,
        ),
    )
    monkeypatch.setattr("yaatv.cli.validate_image", lambda _path: (1920, 1080))

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        raise AssertionError("dry run must not encode")

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)

    assert run(
        ["-a", str(audio_path), "-i", str(image_path), "-o", str(output_path), "--dry-run"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    assert "ffmpeg" in stderr.getvalue()
    assert str(output_path) in stderr.getvalue()
    assert not output_path.exists()


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


def test_download_url_uses_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def urlopen(url: str, *, timeout: int) -> BytesIO:
        captured["url"] = url
        captured["timeout"] = timeout
        return BytesIO(b"archive")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    destination = tmp_path / "ffmpeg.zip"

    _download_url("https://example.invalid/ffmpeg.zip", destination)

    assert captured == {
        "url": "https://example.invalid/ffmpeg.zip",
        "timeout": 60,
    }
    assert destination.read_bytes() == b"archive"


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


def test_install_linux_ffmpeg_extracts_only_ffmpeg_and_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_bytes = _ffmpeg_tar_bytes()
    expected_sha256 = hashlib.sha256(archive_bytes).hexdigest()

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(archive_bytes)

    monkeypatch.setattr("yaatv.cli._download_url", download)
    install_dir = tmp_path / "yaatv" / "bin"

    assert install_linux_ffmpeg(
        install_dir=install_dir,
        expected_sha256=expected_sha256,
        stderr=StringIO(),
    ) == install_dir

    assert (install_dir / "ffmpeg").read_bytes() == b"ffmpeg"
    assert (install_dir / "ffprobe").read_bytes() == b"ffprobe"
    assert not (install_dir / "ffplay").exists()
    if os.name != "nt":
        assert (install_dir / "ffmpeg").stat().st_mode & 0o111
        assert (install_dir / "ffprobe").stat().st_mode & 0o111


def test_install_macos_ffmpeg_extracts_ffmpeg_and_ffprobe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg_bytes = _single_tool_zip_bytes("ffmpeg", b"ffmpeg")
    ffprobe_bytes = _single_tool_zip_bytes("ffprobe", b"ffprobe")
    archive_by_url = {
        "https://example.invalid/ffmpeg.zip": ffmpeg_bytes,
        "https://example.invalid/ffprobe.zip": ffprobe_bytes,
    }

    def download(url: str, destination: Path) -> None:
        destination.write_bytes(archive_by_url[url])

    monkeypatch.setattr("yaatv.cli._download_url", download)
    install_dir = tmp_path / "yaatv" / "bin"

    assert install_macos_ffmpeg(
        install_dir=install_dir,
        ffmpeg_archive_url="https://example.invalid/ffmpeg.zip",
        ffmpeg_expected_sha256=hashlib.sha256(ffmpeg_bytes).hexdigest(),
        ffprobe_archive_url="https://example.invalid/ffprobe.zip",
        ffprobe_expected_sha256=hashlib.sha256(ffprobe_bytes).hexdigest(),
        stderr=StringIO(),
    ) == install_dir

    assert (install_dir / "ffmpeg").read_bytes() == b"ffmpeg"
    assert (install_dir / "ffprobe").read_bytes() == b"ffprobe"
    if os.name != "nt":
        assert (install_dir / "ffmpeg").stat().st_mode & 0o111
        assert (install_dir / "ffprobe").stat().st_mode & 0o111


def test_install_macos_ffmpeg_uses_arm64_downloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg_bytes = _single_tool_zip_bytes("ffmpeg", b"arm64 ffmpeg")
    ffprobe_bytes = _single_tool_zip_bytes("ffprobe", b"arm64 ffprobe")
    archive_by_url = {
        MACOS_ARM64_FFMPEG_ARCHIVE_URL: ffmpeg_bytes,
        MACOS_ARM64_FFPROBE_ARCHIVE_URL: ffprobe_bytes,
    }

    def download(url: str, destination: Path) -> None:
        destination.write_bytes(archive_by_url[url])

    monkeypatch.setattr("platform.machine", lambda: "arm64")
    monkeypatch.setattr("yaatv.cli._download_url", download)
    install_dir = tmp_path / "yaatv" / "bin"

    assert install_macos_ffmpeg(
        install_dir=install_dir,
        ffmpeg_expected_sha256=hashlib.sha256(ffmpeg_bytes).hexdigest(),
        ffprobe_expected_sha256=hashlib.sha256(ffprobe_bytes).hexdigest(),
        stderr=StringIO(),
    ) == install_dir

    assert (install_dir / "ffmpeg").read_bytes() == b"arm64 ffmpeg"
    assert (install_dir / "ffprobe").read_bytes() == b"arm64 ffprobe"


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


def test_normalize_output_path_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(YaatvError, match="Output directory does not exist"):
        normalize_output_path(tmp_path / "missing" / "out.mp4")


def test_run_ffmpeg_hides_progress_unless_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], *, check: bool, stderr: object, text: bool) -> object:
        captured.update({"command": command, "check": check, "stderr": stderr, "text": text})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("subprocess.run", fake_run)

    assert run_ffmpeg(["ffmpeg", "-version"]) == 0
    assert captured == {
        "command": ["ffmpeg", "-version"],
        "check": False,
        "stderr": subprocess.PIPE,
        "text": True,
    }

    assert run_ffmpeg(["ffmpeg", "-version"], verbose=True) == 0
    assert captured["stderr"] is None


def test_probe_output_reports_missing_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(YaatvError, match="FFprobe was not found"):
        probe_output("ffprobe", Path("out.mp4"))


def test_probe_output_reports_ffprobe_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="bad output")

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(YaatvError, match="bad output"):
        probe_output("ffprobe", Path("out.mp4"))


def test_probe_output_reports_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="{", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(YaatvError, match="Could not parse FFprobe output"):
        probe_output("ffprobe", Path("out.mp4"))


def test_prores_command_uses_correct_encoder_settings() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mov"),
        target_size=(1920, 1080),
        audio_plan=plan,
        overwrite=False,
        is_prores=True,
    )

    assert command[command.index("-c:v") + 1] == "prores_ks"
    assert command[command.index("-profile:v") + 1] == "2"
    assert command[command.index("-pix_fmt") + 1] == "yuv422p10le"
    assert command[command.index("-vendor") + 1] == "apl0"
    assert command[command.index("-f") + 1] == "mov"
    assert "-movflags" not in command
    assert command[command.index("-vf") + 1] == (
        "scale=1920:1080:force_original_aspect_ratio=decrease:out_range=tv,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,"
        "format=yuv422p10le,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )


def test_h264_command_unchanged_without_is_prores() -> None:
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

    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-movflags") + 1] == "+faststart"
    assert "-f" not in command or command[command.index("-f") + 1] != "mov"


def test_verify_prores_output_stats() -> None:
    stats = OutputStats(
        width=1920,
        height=1080,
        video_codec="prores",
        pixel_format="yuv422p10le",
        color_range="tv",
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        frame_rate=1.0,
        audio_codec="aac",
        audio_sample_rate=48_000,
    )

    verify_output_stats(stats, (1920, 1080), is_prores=True)

    assert format_output_stats(stats) == (
        "1920x1080, ProRes 422/yuv422p10le, bt709, 1fps video, AAC 48kHz"
    )


def test_verify_prores_output_accepts_unreported_color_range() -> None:
    stats = OutputStats(
        width=1920,
        height=1080,
        video_codec="prores",
        pixel_format="yuv422p10le",
        color_range=None,
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        frame_rate=1.0,
        audio_codec="aac",
        audio_sample_rate=48_000,
    )

    verify_output_stats(stats, (1920, 1080), is_prores=True)


def test_verify_prores_output_rejects_h264_in_prores_mode() -> None:
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

    with pytest.raises(YaatvError, match="expected ProRes video"):
        verify_output_stats(stats, (1920, 1080), is_prores=True)


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


def test_verify_output_stats_rejects_unreported_h264_color_range() -> None:
    stats = OutputStats(
        width=1920,
        height=1080,
        video_codec="h264",
        pixel_format="yuv420p",
        color_range=None,
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
        frame_rate=1.0,
        audio_codec="aac",
        audio_sample_rate=48_000,
    )

    with pytest.raises(YaatvError, match="expected limited color range, got unknown"):
        verify_output_stats(stats, (1920, 1080))


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
