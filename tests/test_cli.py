import hashlib
import os
import re
import subprocess
import sys
import zipfile
from io import BytesIO, StringIO
from pathlib import Path
from urllib.request import Request

import pytest
from PIL import Image

from yaatv import __version__
from yaatv.cli import (
    FFMPEG_DOWNLOAD_USER_AGENT,
    LINUX_FFMPEG_ARCHIVE_SHA256,
    LINUX_FFMPEG_ARCHIVE_URL,
    LINUX_FFPROBE_ARCHIVE_SHA256,
    LINUX_FFPROBE_ARCHIVE_URL,
    MACOS_ARM64_FFMPEG_ARCHIVE_URL,
    MACOS_ARM64_FFPROBE_ARCHIVE_URL,
    MACOS_FFMPEG_ARCHIVE_SHA256,
    MACOS_FFMPEG_ARCHIVE_URL,
    MACOS_FFPROBE_ARCHIVE_SHA256,
    MACOS_FFPROBE_ARCHIVE_URL,
    OUTPUT_SIZES,
    TOOL_HEALTH_TIMEOUT_SECONDS,
    WINDOWS_FFMPEG_ARCHIVE_SHA256,
    WINDOWS_FFMPEG_ARCHIVE_URL,
    AudioMetadata,
    AudioPlan,
    OutputStats,
    ToolHealth,
    YaatvError,
    _download_url,
    _install_staged_tools,
    _should_pause_after_run,
    background_color,
    build_ffmpeg_command,
    check_tool_health,
    choose_audio_plan,
    classify_files,
    confirm_overwrite,
    default_output_path,
    extract_embedded_cover,
    find_external_tool,
    format_duration,
    format_file_details,
    format_output_stats,
    input_format_warnings,
    install_linux_ffmpeg,
    install_macos_ffmpeg,
    install_windows_ffmpeg,
    is_high_quality_aac,
    main,
    normalize_output_path,
    output_size,
    pad_seconds,
    parse_args,
    probe_output,
    quality_warnings,
    read_audio_metadata,
    resolve_ffmpeg_tools,
    resolve_output_path,
    run,
    run_ffmpeg,
    run_scry,
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


def _image_bytes(format_name: str = "JPEG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (16, 16), color=(32, 64, 96)).save(buffer, format=format_name)
    return buffer.getvalue()


def _transcode_plan(pad: float = 0) -> AudioPlan:
    return choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=pad,
    )


def _video_tail(pixel_format: str) -> str:
    return (
        f"format={pixel_format},"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )


def _pad_filter(width: int, height: int, *, color: str = "black", pixel_format: str = "yuv420p") -> str:
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{color},"
        f"{_video_tail(pixel_format)}"
    )


def _background_image_filter(width: int, height: int, *, pixel_format: str = "yuv420p") -> str:
    return (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase:out_range=tv,"
        f"crop={width}:{height}[bg];"
        f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{_video_tail(pixel_format)}[v]"
    )


def _background_blur_filter(width: int, height: int, *, pixel_format: str = "yuv420p") -> str:
    return (
        "[0:v]split[s1][s2];"
        f"[s1]scale={width}:{height}:force_original_aspect_ratio=increase:out_range=tv,"
        f"crop={width}:{height},boxblur=20:5[bg];"
        f"[s2]scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{_video_tail(pixel_format)}[v]"
    )


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


def test_release_workflow_builds_native_macos_arm64_asset() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert "package_name: yaatv-macos-x64" in workflow
    assert "package_name: yaatv-macos-arm64" in workflow
    assert "executable_name: yaatv-macos-arm64" in workflow
    assert "MACOS_ARM64_FFMPEG_ARCHIVE_URL" in workflow
    assert "platform.machine()" in workflow


def test_windows_installer_uses_pinned_versioned_release_archive() -> None:
    assert WINDOWS_FFMPEG_ARCHIVE_URL == (
        "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/ffmpeg-8.1.2-essentials_build.zip"
    )
    assert WINDOWS_FFMPEG_ARCHIVE_SHA256 == "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"


def test_linux_installer_uses_pinned_versioned_release_archives() -> None:
    assert LINUX_FFMPEG_ARCHIVE_URL == (
        "https://ffmpeg.martin-riedl.de/download/linux/amd64/1787074600_9.0.1/ffmpeg.zip"
    )
    assert LINUX_FFPROBE_ARCHIVE_URL == (
        "https://ffmpeg.martin-riedl.de/download/linux/amd64/1787074600_9.0.1/ffprobe.zip"
    )
    assert LINUX_FFMPEG_ARCHIVE_SHA256 == "18bec7d5c2ab3b24d277466b758394e109b0479133b98d155c5540ed3013fa74"
    assert LINUX_FFPROBE_ARCHIVE_SHA256 == "227c122cabb36444d7dee7f5c9c9db9e36e15ab7a9b43eb2196936fb177f9ad3"


def test_macos_x64_installer_uses_pinned_reachable_build_server() -> None:
    assert MACOS_FFMPEG_ARCHIVE_URL == (
        "https://ffmpeg.martin-riedl.de/download/macos/amd64/1778768838_8.1.1/ffmpeg.zip"
    )
    assert MACOS_FFPROBE_ARCHIVE_URL == (
        "https://ffmpeg.martin-riedl.de/download/macos/amd64/1778768838_8.1.1/ffprobe.zip"
    )
    assert MACOS_FFMPEG_ARCHIVE_SHA256 == "8cb711bfa6f66033112d708dc275220419d0fdb49c5b752f8db25f11a92d321f"
    assert MACOS_FFPROBE_ARCHIVE_SHA256 == "e9b9b83fef584c367b27c683a1172921b4f48fa8bd5df6712ef54e63b915ea50"


def test_audio_and_image_are_required_for_encoding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(YaatvError, match="Audio file is required"):
        run([], stdin=StringIO(), stderr=StringIO())

    audio_path = tmp_path / "track.wav"
    background_path = tmp_path / "background.jpg"
    audio_path.write_bytes(b"audio")
    background_path.write_bytes(b"background")

    monkeypatch.setattr(
        "yaatv.cli.read_audio_metadata",
        lambda _path: AudioMetadata(
            codec="wav",
            bitrate=900_000,
            sample_rate=44_100,
            artist=None,
            title=None,
            duration=12.1,
        ),
    )
    monkeypatch.setattr("yaatv.cli.extract_embedded_cover", lambda _audio_path, _directory: None)

    with pytest.raises(YaatvError, match="Cover image is required"):
        run(["--audio", str(audio_path), "--dry-run"], stdin=StringIO(), stderr=StringIO())

    with pytest.raises(YaatvError, match="Cover image is required"):
        run(["--audio", str(audio_path), "--bg-color", "black", "--dry-run"], stdin=StringIO(), stderr=StringIO())

    with pytest.raises(YaatvError, match="Cover image is required"):
        run(["--audio", str(audio_path), "--bg-blur", "--dry-run"], stdin=StringIO(), stderr=StringIO())

    with pytest.raises(SystemExit):
        run(
            ["--audio", str(audio_path), "--bg-blur", "--bg-color", "white", "--dry-run"],
            stdin=StringIO(),
            stderr=StringIO(),
        )

    with pytest.raises(YaatvError, match="Cover image is required"):
        run(
            ["--audio", str(audio_path), "--bg-image", str(background_path), "--dry-run"],
            stdin=StringIO(),
            stderr=StringIO(),
        )

    with pytest.raises(SystemExit):
        run(
            ["--audio", str(audio_path), "--bg-image", str(background_path), "--bg-color", "white", "--dry-run"],
            stdin=StringIO(),
            stderr=StringIO(),
        )


def test_run_scry_does_not_require_audio_or_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yaatv.cli.run_scry", lambda **_kwargs: 0)

    assert run(["--scry"], stdin=StringIO(), stderr=StringIO()) == 0


def test_parse_args_accepts_positional_files() -> None:
    args = parse_args(["cover.JPG", "track.FLAC", "--resolution", "4k", "--aspect", "square"])

    assert args.files == [Path("cover.JPG"), Path("track.FLAC")]
    assert args.audio is None
    assert args.image is None
    assert args.resolution == "4k"
    assert args.aspect == "square"


def test_output_size_maps_resolution_and_aspect() -> None:
    assert output_size("1080p", "16:9") == (1920, 1080)
    assert output_size("1440p", "square") == (1440, 1440)
    assert output_size("4k", "9:16") == (2160, 3840)


def test_parse_args_accepts_scry_without_files() -> None:
    args = parse_args(["--scry"])

    assert args.scry is True
    assert args.audio is None
    assert args.image is None


def test_parse_args_accepts_install_ffmpeg_without_files() -> None:
    args = parse_args(["--install-ffmpeg"])

    assert args.install_ffmpeg is True
    assert args.scry is False
    assert args.audio is None
    assert args.image is None


def test_parse_args_rejects_install_ffmpeg_with_scry(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--install-ffmpeg", "--scry"])

    err = capsys.readouterr().err
    assert "--install-ffmpeg and --scry are mutually exclusive; use one or the other." in err


def test_parse_args_rejects_scry_with_install_ffmpeg(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--scry", "--install-ffmpeg"])

    err = capsys.readouterr().err
    assert "--install-ffmpeg and --scry are mutually exclusive; use one or the other." in err


def test_parse_args_accepts_open_folder() -> None:
    args = parse_args(["-a", "audio.flac", "-i", "cover.jpg", "--open-folder"])

    assert args.open_folder is True


def test_should_pause_after_run_for_noninteractive_positional_files() -> None:
    assert _should_pause_after_run(["track.flac", "cover.jpg"], StringIO()) is True


def test_should_pause_after_run_for_windows_explorer_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yaatv.cli._windows_parent_process_name", lambda: "explorer.exe")

    assert _should_pause_after_run(["track.flac", "cover.jpg"], _TtyInput("\n")) is True


def test_should_not_pause_after_run_for_flag_invocation() -> None:
    assert _should_pause_after_run(["-a", "track.flac", "-i", "cover.jpg"], StringIO()) is False


def test_main_pauses_after_drag_drop_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = StringIO()

    def fake_run(argv: list[str], *, stdin: StringIO, stderr: StringIO) -> int:
        assert argv == ["track.flac", "cover.jpg"]
        return 0

    monkeypatch.setattr("yaatv.cli.run", fake_run)

    assert main(["track.flac", "cover.jpg"], stdin=StringIO("\n"), stderr=stderr) == 0
    assert "Press Enter to exit..." in stderr.getvalue()


def test_main_pauses_after_drag_drop_error(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = StringIO()

    def fake_run(argv: list[str], *, stdin: StringIO, stderr: StringIO) -> int:
        raise YaatvError("bad input")

    monkeypatch.setattr("yaatv.cli.run", fake_run)

    assert main(["track.flac", "cover.jpg"], stdin=StringIO("\n"), stderr=stderr) == 1
    output = stderr.getvalue()
    assert "error: bad input" in output
    assert "Press Enter to exit..." in output


def test_help_includes_examples(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "examples:" in help_text
    assert "yaatv audio.flac cover.jpg" in help_text
    assert "yaatv --scry" in help_text


def test_help_mentions_scry_for_audio_and_image_options(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out.replace("\n", " ")
    collapsed = " ".join(help_text.split()).replace("- ", "-")
    assert "Path to audio file (required unless using --install-ffmpeg, --scry, or positional files)" in collapsed
    assert (
        "Path to cover image (required unless using --install-ffmpeg, "
        "--scry, positional files, or color-only output)"
    ) in collapsed


def test_classify_files_detects_audio_and_image_in_any_order() -> None:
    assert classify_files([Path("track.flac"), Path("cover.jpg")]) == (Path("track.flac"), Path("cover.jpg"))
    assert classify_files([Path("cover.PNG"), Path("track.MP3")]) == (Path("track.MP3"), Path("cover.PNG"))


def test_classify_files_rejects_wrong_count() -> None:
    with pytest.raises(YaatvError, match="exactly 2 files .* but 1 were provided"):
        classify_files([Path("track.flac")])

    with pytest.raises(YaatvError, match="exactly 2 files .* but 3 were provided"):
        classify_files([Path("track.flac"), Path("cover.jpg"), Path("logo.png")])


def test_classify_files_rejects_same_type_inputs() -> None:
    with pytest.raises(YaatvError, match="Two audio files provided"):
        classify_files([Path("track.flac"), Path("song.mp3")])

    with pytest.raises(YaatvError, match="Two image files provided"):
        classify_files([Path("cover.jpg"), Path("art.png")])


def test_classify_files_rejects_unrecognized_extensions() -> None:
    with pytest.raises(YaatvError, match="Could not classify file.stuff as audio or image"):
        classify_files([Path("track.flac"), Path("file.stuff")])


def test_positional_files_cannot_be_mixed_with_audio_or_image_flags() -> None:
    with pytest.raises(YaatvError, match="Do not use positional file arguments together with -a or -i flags"):
        run(["-a", "track.flac", "cover.jpg"], stdin=StringIO(), stderr=StringIO())

    with pytest.raises(YaatvError, match="Do not use positional file arguments together with -a or -i flags"):
        run(["-i", "cover.jpg", "track.flac"], stdin=StringIO(), stderr=StringIO())


def test_cover_image_is_optional_for_explicit_nondefault_background_color() -> None:
    args = parse_args(["--audio", "track.wav", "--bg-color", "white"])

    assert args.image is None
    assert args.bg_color == "0xffffff"
    assert args.bg_color_explicit


def test_background_color_validates_values() -> None:
    assert background_color("white") == "0xffffff"
    assert background_color("black") == "black"
    assert background_color("#2a2a2a") == "0x2a2a2a"

    with pytest.raises(Exception, match="valid #RRGGBB hex color or named CSS color"):
        background_color("not-a-color")

    with pytest.raises(Exception, match="#RRGGBB"):
        background_color("#fff")


def test_parse_args_rejects_bg_image_with_bg_blur() -> None:
    with pytest.raises(SystemExit):
        parse_args(["-a", "track.wav", "-i", "cover.jpg", "--bg-image", "bg.jpg", "--bg-blur"])


def test_parse_args_rejects_bg_color_with_bg_image() -> None:
    with pytest.raises(SystemExit):
        parse_args(["-a", "track.wav", "-i", "cover.jpg", "--bg-image", "bg.jpg", "--bg-color", "red"])


def test_parse_args_rejects_bg_color_with_bg_blur() -> None:
    with pytest.raises(SystemExit):
        parse_args(["-a", "track.wav", "-i", "cover.jpg", "--bg-blur", "--bg-color", "red"])


def test_parse_args_accepts_cover_with_default_background() -> None:
    args = parse_args(["-a", "track.wav", "-i", "cover.jpg"])

    assert args.bg_image is None
    assert not args.bg_blur
    assert not args.bg_color_explicit


def test_parse_args_accepts_cover_with_custom_background_color() -> None:
    args = parse_args(["-a", "track.wav", "-i", "cover.jpg", "--bg-color", "white"])

    assert args.bg_color == "0xffffff"
    assert args.bg_color_explicit
    assert args.bg_image is None
    assert not args.bg_blur


def test_parse_args_accepts_cover_with_background_image() -> None:
    args = parse_args(["-a", "track.wav", "-i", "cover.jpg", "--bg-image", "bg.jpg"])

    assert args.bg_image == Path("bg.jpg")
    assert not args.bg_blur
    assert not args.bg_color_explicit


def test_parse_args_accepts_cover_with_blurred_background() -> None:
    args = parse_args(["-a", "track.wav", "-i", "cover.jpg", "--bg-blur"])

    assert args.bg_blur
    assert args.bg_image is None
    assert not args.bg_color_explicit


def test_parse_args_accepts_color_only_output() -> None:
    args = parse_args(["-a", "track.wav", "--bg-color", "white"])

    assert args.bg_color == "0xffffff"
    assert args.bg_color_explicit
    assert args.bg_image is None
    assert not args.bg_blur


@pytest.mark.parametrize(
    ("aspect", "resolution", "expected_size"),
    [
        (aspect, resolution, size)
        for aspect, sizes in OUTPUT_SIZES.items()
        for resolution, size in sizes.items()
    ],
)
def test_media_contract_defines_every_supported_output_size(
    aspect: str,
    resolution: str,
    expected_size: tuple[int, int],
) -> None:
    width, height = expected_size
    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=output_size(resolution, aspect),
        audio_plan=_transcode_plan(),
        overwrite=False,
    )

    assert output_size(resolution, aspect) == expected_size
    assert command[command.index("-vf") + 1] == _pad_filter(width, height)


@pytest.mark.parametrize(
    ("name", "kwargs", "expected_prefix", "video_option", "expected_video", "expected_maps"),
    [
        (
            "default mp4",
            {},
            ["ffmpeg", "-n", "-loop", "1", "-framerate", "1", "-i", "cover.jpg"],
            "-vf",
            _pad_filter(1920, 1080),
            ("0:v:0", "1:a:0"),
        ),
        (
            "prores mov",
            {"output_path": Path("out.mov"), "is_prores": True},
            ["ffmpeg", "-n", "-loop", "1", "-framerate", "1", "-i", "cover.jpg"],
            "-vf",
            _pad_filter(1920, 1080, pixel_format="yuv422p10le"),
            ("0:v:0", "1:a:0"),
        ),
        (
            "background image",
            {"bg_image_path": Path("background.jpg"), "bg_blur": True, "bg_color": "0xffffff"},
            [
                "ffmpeg",
                "-n",
                "-loop",
                "1",
                "-framerate",
                "1",
                "-i",
                "background.jpg",
                "-loop",
                "1",
                "-framerate",
                "1",
                "-i",
                "cover.jpg",
            ],
            "-filter_complex",
            _background_image_filter(1920, 1080),
            ("[v]", "2:a:0"),
        ),
        (
            "blurred background",
            {"bg_blur": True, "bg_color": "0xffffff"},
            ["ffmpeg", "-n", "-loop", "1", "-framerate", "1", "-i", "cover.jpg"],
            "-filter_complex",
            _background_blur_filter(1920, 1080),
            ("[v]", "1:a:0"),
        ),
        (
            "color only",
            {"image_path": None, "bg_color": "0xffffff", "output_duration": 30},
            ["ffmpeg", "-n", "-i", "track.flac", "-f", "lavfi", "-i", "color=c=0xffffff:s=1920x1080:d=30"],
            "-vf",
            f"fps=fps=1:start_time=0,{_video_tail('yuv420p')}",
            ("1:v:0", "0:a:0"),
        ),
    ],
)
def test_media_contract_command_profiles_preserve_branch_invariants(
    name: str,
    kwargs: dict[str, object],
    expected_prefix: list[str],
    video_option: str,
    expected_video: str,
    expected_maps: tuple[str, str],
) -> None:
    options = {
        "ffmpeg": "ffmpeg",
        "audio_path": Path("track.flac"),
        "image_path": Path("cover.jpg"),
        "output_path": Path("out.mp4"),
        "target_size": (1920, 1080),
        "audio_plan": _transcode_plan(),
        "overwrite": False,
    }
    options.update(kwargs)

    command = build_ffmpeg_command(**options)  # type: ignore[arg-type]

    assert command[: len(expected_prefix)] == expected_prefix, name
    assert command[command.index("-map") + 1] == expected_maps[0], name
    assert command[command.index("-map", command.index("-map") + 1) + 1] == expected_maps[1], name
    assert command[command.index(video_option) + 1] == expected_video, name

    if kwargs.get("is_prores"):
        assert "-movflags" not in command
        assert command[command.index("-f") + 1] == "mov"
        assert command[command.index("-pix_fmt") + 1] == "yuv422p10le"
    else:
        assert command[command.index("-pix_fmt") + 1] == "yuv420p"
        if options["output_path"] == Path("out.mp4"):
            assert command[command.index("-movflags") + 1] == "+faststart"


@pytest.mark.parametrize(
    ("output_duration", "expected_shortest", "expected_duration"),
    [
        (None, True, None),
        (145, True, "145"),
    ],
)
def test_media_contract_default_output_tail_order(
    output_duration: float | None,
    expected_shortest: bool,
    expected_duration: str | None,
) -> None:
    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.mp3"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=(1920, 1080),
        audio_plan=_transcode_plan(),
        overwrite=False,
        output_duration=output_duration,
    )

    assert ("-shortest" in command) is expected_shortest
    assert command.index("-shortest") < command.index("-movflags") < command.index("-vf")
    if expected_duration is None:
        assert "-t" not in command
    else:
        assert command[command.index("-t") + 1] == expected_duration
        assert command.index("-vf") < command.index("-t") < len(command) - 1


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


def test_background_color_changes_default_pad_color() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=(1920, 1080),
        audio_plan=plan,
        overwrite=False,
        bg_color="0xffffff",
    )

    assert command[command.index("-vf") + 1] == (
        "scale=1920:1080:force_original_aspect_ratio=decrease:out_range=tv,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:0xffffff,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )


def test_square_command_uses_square_canvas() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=output_size("1080p", "square"),
        audio_plan=plan,
        overwrite=False,
    )

    assert command[command.index("-vf") + 1] == (
        "scale=1080:1080:force_original_aspect_ratio=decrease:out_range=tv,"
        "pad=1080:1080:(ow-iw)/2:(oh-ih)/2:black,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )


def test_vertical_background_blur_command_uses_vertical_canvas() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=output_size("1080p", "9:16"),
        audio_plan=plan,
        overwrite=False,
        bg_blur=True,
    )

    assert command[command.index("-filter_complex") + 1] == (
        "[0:v]split[s1][s2];"
        "[s1]scale=1080:1920:force_original_aspect_ratio=increase:out_range=tv,"
        "crop=1080:1920,boxblur=20:5[bg];"
        "[s2]scale=1080:1920:force_original_aspect_ratio=decrease:out_range=tv[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]"
    )


def test_background_image_command_overlays_cover_on_background() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=(1920, 1080),
        audio_plan=plan,
        overwrite=False,
        bg_image_path=Path("background.jpg"),
        bg_blur=True,
        bg_color="0xffffff",
    )

    assert command[:14] == [
        "ffmpeg",
        "-n",
        "-loop",
        "1",
        "-framerate",
        "1",
        "-i",
        "background.jpg",
        "-loop",
        "1",
        "-framerate",
        "1",
        "-i",
        "cover.jpg",
    ]
    assert command[command.index("-map") + 1] == "[v]"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "2:a:0"
    assert command[command.index("-filter_complex") + 1] == (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase:out_range=tv,"
        "crop=1920:1080[bg];"
        "[1:v]scale=1920:1080:force_original_aspect_ratio=decrease:out_range=tv[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]"
    )


def test_background_blur_command_splits_cover_image() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=Path("cover.jpg"),
        output_path=Path("out.mp4"),
        target_size=(1920, 1080),
        audio_plan=plan,
        overwrite=False,
        bg_blur=True,
        bg_color="0xffffff",
    )

    assert command[command.index("-map") + 1] == "[v]"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "1:a:0"
    assert command[command.index("-filter_complex") + 1] == (
        "[0:v]split[s1][s2];"
        "[s1]scale=1920:1080:force_original_aspect_ratio=increase:out_range=tv,"
        "crop=1920:1080,boxblur=20:5[bg];"
        "[s2]scale=1920:1080:force_original_aspect_ratio=decrease:out_range=tv[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]"
    )


def test_color_only_command_uses_generated_video_stream() -> None:
    plan = choose_audio_plan(
        AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None),
        pad=0,
    )

    command = build_ffmpeg_command(
        ffmpeg="ffmpeg",
        audio_path=Path("track.flac"),
        image_path=None,
        output_path=Path("out.mp4"),
        target_size=(1920, 1080),
        audio_plan=plan,
        overwrite=False,
        output_duration=30,
        bg_color="0xffffff",
    )

    assert "-loop" not in command
    assert command[command.index("-i") + 1] == "track.flac"
    assert command[command.index("-f") + 1] == "lavfi"
    assert command[command.index("-i", command.index("-i") + 1) + 1] == "color=c=0xffffff:s=1920x1080:d=30"
    assert command[command.index("-vf") + 1] == (
        "fps=fps=1:start_time=0,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    assert command[command.index("-map") + 1] == "1:v:0"
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "0:a:0"
    assert command[command.index("-t") + 1] == "30"


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

    assert input_format_warnings(Path("track.flac"), Path("cover.png"), Path("background.picture")) == [
        "background image extension is unusual: .picture",
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


def test_output_dir_places_default_name_in_existing_directory(tmp_path: Path) -> None:
    output_dir = tmp_path / "uploads"
    output_dir.mkdir()
    metadata = AudioMetadata(
        codec="flac",
        bitrate=900_000,
        sample_rate=44_100,
        artist="Artist",
        title="Title",
    )

    assert resolve_output_path(Path("input.flac"), metadata, None, output_dir) == output_dir / "Artist - Title.mp4"


def test_output_dir_rejects_missing_directory(tmp_path: Path) -> None:
    metadata = AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None)

    with pytest.raises(YaatvError, match="Output directory does not exist"):
        resolve_output_path(Path("input.flac"), metadata, None, tmp_path / "missing")


def test_output_dir_rejects_file_path(tmp_path: Path) -> None:
    output_dir = tmp_path / "not-a-directory"
    output_dir.write_text("file", encoding="utf-8")
    metadata = AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None)

    with pytest.raises(YaatvError, match="Output directory is not a directory"):
        resolve_output_path(Path("input.flac"), metadata, None, output_dir)


def test_output_dir_cannot_be_combined_with_output(tmp_path: Path) -> None:
    metadata = AudioMetadata(codec="flac", bitrate=900_000, sample_rate=44_100, artist=None, title=None)

    with pytest.raises(YaatvError, match="Do not use --output-dir together with -o/--output"):
        resolve_output_path(Path("input.flac"), metadata, Path("out.mp4"), tmp_path)


def test_pad_seconds_validates_range() -> None:
    assert pad_seconds("0") == 0
    assert pad_seconds("10") == 10

    with pytest.raises(Exception, match="between 0 and 10"):
        pad_seconds("11")


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "+inf", "NaN", "Infinity"])
def test_pad_seconds_rejects_non_finite_values(value: str) -> None:
    with pytest.raises(Exception, match="between 0 and 10"):
        pad_seconds(value)


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


def test_run_scry_succeeds_with_app_managed_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_bin = tmp_path / "bin"
    app_bin.mkdir()
    (app_bin / _executable_name("ffmpeg")).write_bytes(b"")
    (app_bin / _executable_name("ffprobe")).write_bytes(b"")
    stderr = StringIO()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("yaatv.cli.app_managed_ffmpeg_bin_dir", lambda: app_bin)
    monkeypatch.setattr("yaatv.cli.supports_app_managed_ffmpeg_install", lambda: True)
    monkeypatch.setattr("shutil.which", lambda name: str(app_bin / _executable_name(name)))
    monkeypatch.setattr(
        "yaatv.cli.check_tool_health", lambda path: ToolHealth(path=path, state="ok", version="7.1.4")
    )

    assert run_scry(stderr=stderr) == 0
    output = stderr.getvalue()
    assert f"ok    ffmpeg: {app_bin / _executable_name('ffmpeg')}" in output
    assert f"ok    ffprobe: {app_bin / _executable_name('ffprobe')}" in output
    assert "info  ffmpeg version: 7.1.4" in output
    assert "ok    current directory is writable" in output


def test_run_scry_fails_when_required_tools_are_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stderr = StringIO()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("yaatv.cli.app_managed_ffmpeg_bin_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr("yaatv.cli.supports_app_managed_ffmpeg_install", lambda: True)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    assert run_scry(stderr=stderr) == 1
    output = stderr.getvalue()
    assert "warn  ffmpeg: not found in app-managed bin" in output
    assert "warn  ffprobe on PATH: not found" in output


def test_check_tool_health_reports_healthy_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command == ["ffmpeg", "-version"]
        stdout = "ffmpeg version 7.1.4-75731192 Copyright\nbuilt with gcc"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    health = check_tool_health("ffmpeg")
    assert health.state == "ok"
    assert health.version == "7.1.4-75731192"
    assert health.detail is None


def test_check_tool_health_reports_missing_tool(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere" / _executable_name("ffmpeg")

    health = check_tool_health(str(missing))
    assert health.state == "missing"
    assert health.version is None

    health = check_tool_health(None)
    assert health.state == "missing"


def test_check_tool_health_reports_tool_that_cannot_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tool = tmp_path / _executable_name("ffmpeg")
    tool.write_bytes(b"")

    def fake_run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("subprocess.run", fake_run)

    health = check_tool_health(str(tool))
    assert health.state == "blocked"
    assert "Access is denied" in (health.detail or "")


def test_check_tool_health_reports_tool_that_exits_unsuccessfully(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="error while loading shared libraries")

    monkeypatch.setattr("subprocess.run", fake_run)

    health = check_tool_health("ffmpeg")
    assert health.state == "failed"
    assert "shared libraries" in (health.detail or "")


def test_check_tool_health_reports_tool_that_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == TOOL_HEALTH_TIMEOUT_SECONDS
        raise subprocess.TimeoutExpired(command, timeout=TOOL_HEALTH_TIMEOUT_SECONDS)

    monkeypatch.setattr("subprocess.run", fake_run)

    health = check_tool_health("ffmpeg")
    assert health.state == "failed"
    assert health.detail == f"did not respond within {TOOL_HEALTH_TIMEOUT_SECONDS} seconds"


def test_run_scry_fails_when_app_tool_cannot_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_bin = tmp_path / "bin"
    app_bin.mkdir()
    (app_bin / _executable_name("ffmpeg")).write_bytes(b"")
    (app_bin / _executable_name("ffprobe")).write_bytes(b"")
    stderr = StringIO()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("yaatv.cli.app_managed_ffmpeg_bin_dir", lambda: app_bin)
    monkeypatch.setattr("yaatv.cli.supports_app_managed_ffmpeg_install", lambda: True)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "yaatv.cli.check_tool_health",
        lambda path: ToolHealth(path=path, state="blocked", detail="Access is denied"),
    )

    assert run_scry(stderr=stderr) == 1
    output = stderr.getvalue()
    broken_ffmpeg = f"{app_bin / _executable_name('ffmpeg')} exists but cannot execute (Access is denied)"
    assert f"fail  ffmpeg: {broken_ffmpeg}" in output
    assert "fail  ffprobe" in output
    assert "info  next step: run yaatv --install-ffmpeg" in output


def test_run_scry_accepts_healthy_path_tool_when_app_tool_cannot_execute(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app_bin = tmp_path / "bin"
    app_bin.mkdir()
    (app_bin / _executable_name("ffmpeg")).write_bytes(b"")
    (app_bin / _executable_name("ffprobe")).write_bytes(b"")
    other_bin = tmp_path / "other"
    stderr = StringIO()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("yaatv.cli.app_managed_ffmpeg_bin_dir", lambda: app_bin)
    monkeypatch.setattr("yaatv.cli.supports_app_managed_ffmpeg_install", lambda: True)
    monkeypatch.setattr("shutil.which", lambda name: str(other_bin / _executable_name(name)))

    def fake_check_tool_health(path: str | None) -> ToolHealth:
        if path is not None and path.startswith(str(app_bin)):
            return ToolHealth(path=path, state="blocked", detail="Access is denied")
        return ToolHealth(path=path, state="ok", version="7.1")

    monkeypatch.setattr("yaatv.cli.check_tool_health", fake_check_tool_health)

    assert run_scry(stderr=stderr) == 0
    output = stderr.getvalue()
    broken_ffmpeg = f"{app_bin / _executable_name('ffmpeg')} exists but cannot execute (Access is denied)"
    assert f"fail  ffmpeg: {broken_ffmpeg}" in output
    assert f"ok    ffmpeg on PATH: {other_bin / _executable_name('ffmpeg')}" in output
    assert "info  ffmpeg version: 7.1" in output


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


def test_run_dry_run_does_not_require_overwrite_when_output_exists(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "out.mp4"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")
    output_path.write_bytes(b"existing")
    existing = output_path.read_bytes()
    stderr = StringIO()

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

    def refuse_overwrite(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("dry run must not confirm overwrite")

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)
    monkeypatch.setattr("yaatv.cli.confirm_overwrite", refuse_overwrite)

    assert run(
        ["-a", str(audio_path), "-i", str(image_path), "-o", str(output_path), "--dry-run"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    assert "ffmpeg" in stderr.getvalue()
    assert "Overwrite?" not in stderr.getvalue()
    assert output_path.read_bytes() == existing


def test_run_dry_run_existing_output_does_not_prompt_when_interactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "out.mp4"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")
    output_path.write_bytes(b"existing")
    stderr = StringIO()

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
    monkeypatch.setattr(
        "yaatv.cli.run_ffmpeg",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("dry run must not encode")),
    )

    assert run(
        ["-a", str(audio_path), "-i", str(image_path), "-o", str(output_path), "--dry-run"],
        stdin=_TtyInput("n\n"),
        stderr=stderr,
    ) == 0
    assert "Overwrite?" not in stderr.getvalue()
    assert output_path.exists()


def test_run_dry_run_does_not_require_ffmpeg_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "out.mp4"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")
    stderr = StringIO()

    def resolve_tools(**_kwargs: object) -> tuple[str, str]:
        raise AssertionError("dry run must not resolve FFmpeg tools")

    def find_tool(**_kwargs: object) -> str:
        raise YaatvError("FFmpeg was not found")

    monkeypatch.setattr("yaatv.cli.resolve_ffmpeg_tools", resolve_tools)
    monkeypatch.setattr("yaatv.cli.find_ffmpeg", find_tool)
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

    assert run(
        ["-a", str(audio_path), "-i", str(image_path), "-o", str(output_path), "--dry-run"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    assert f"Output: {output_path}" in stderr.getvalue()
    assert "ffmpeg -n " in stderr.getvalue()
    assert str(output_path) in stderr.getvalue()
    assert not output_path.exists()


def test_run_quick_mode_dry_run_uses_classified_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
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
            artist="Artist",
            title="Title",
            duration=12.1,
        ),
    )
    monkeypatch.setattr("yaatv.cli.validate_image", lambda _path: (1920, 1080))

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        raise AssertionError("dry run must not encode")

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)

    assert run(
        [str(image_path), str(audio_path), "--resolution", "1440p", "--dry-run"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    command = stderr.getvalue()
    assert str(image_path) in command
    assert str(audio_path) in command
    assert "pad=2560:1440:(ow-iw)/2:(oh-ih)/2:black" in command
    assert str(tmp_path / "Artist - Title.mp4") in command


def test_run_dry_run_uses_selected_aspect(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")
    stderr = StringIO()

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

    assert run(
        [
            "--audio",
            str(audio_path),
            "--image",
            str(image_path),
            "--aspect",
            "9:16",
            "--dry-run",
        ],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    command = stderr.getvalue()
    assert "scale=1080:1920:force_original_aspect_ratio=decrease:out_range=tv" in command
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black" in command


def test_run_dry_run_uses_embedded_cover_when_image_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    output_path = tmp_path / "out.mp4"
    audio_path.write_bytes(b"audio")
    stderr = StringIO()

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

    def extract_cover(_audio_path: Path, directory: Path) -> Path:
        cover_path = directory / "cover.jpg"
        cover_path.write_bytes(_image_bytes())
        return cover_path

    monkeypatch.setattr("yaatv.cli.extract_embedded_cover", extract_cover)

    assert run(
        ["--audio", str(audio_path), "-o", str(output_path), "--dry-run"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    command = stderr.getvalue()
    assert "embedded-cover" not in command
    assert "cover.jpg" in command
    assert str(output_path) in command


def test_run_quick_mode_encodes_with_custom_output_and_open_folder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "custom.mp4"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")
    stderr = StringIO()
    captured: dict[str, object] = {}

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
    monkeypatch.setattr(
        "yaatv.cli.probe_output",
        lambda _ffprobe, _output_path: OutputStats(
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
        ),
    )

    def encode(command: list[str], *, verbose: bool = False) -> int:
        captured["command"] = command
        captured["verbose"] = verbose
        output_path.write_bytes(b"video")
        return 0

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)
    monkeypatch.setattr("yaatv.cli.open_output_folder", lambda path, _stderr: captured.setdefault("opened", path))

    assert run(
        [str(audio_path), str(image_path), "-o", str(output_path), "--open-folder"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    assert captured["verbose"] is False
    assert str(output_path) in captured["command"]
    assert captured["opened"] == output_path
    assert output_path.exists()
    assert "Encoding..." in stderr.getvalue()
    assert "Verifying..." in stderr.getvalue()
    assert f"Created {output_path}" in stderr.getvalue()


def _mock_quick_encode_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path, Path]:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    output_path = tmp_path / "out.mp4"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")

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
    return audio_path, image_path, output_path


def test_failed_encode_removes_newly_created_partial_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, image_path, output_path = _mock_quick_encode_run(monkeypatch, tmp_path)
    stderr = StringIO()

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        output_path.write_bytes(b"partial")
        return 1

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)

    assert run(
        [str(audio_path), str(image_path), "-o", str(output_path), "--overwrite"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 1

    assert not output_path.exists()
    output = stderr.getvalue()
    assert "error: FFmpeg failed with exit code 1" in output
    assert f"warning: removed partial output from failed run: {output_path}" in output


def test_failed_verification_removes_newly_created_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, image_path, output_path = _mock_quick_encode_run(monkeypatch, tmp_path)
    stderr = StringIO()

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        output_path.write_bytes(b"video")
        return 0

    def probe(_ffprobe: str, _output_path: Path) -> OutputStats:
        raise YaatvError("Could not verify output with FFprobe")

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)
    monkeypatch.setattr("yaatv.cli.probe_output", probe)

    with pytest.raises(YaatvError, match="Could not verify output"):
        run(
            [str(audio_path), str(image_path), "-o", str(output_path), "--overwrite"],
            stdin=StringIO(),
            stderr=stderr,
        )

    assert not output_path.exists()
    assert f"warning: removed partial output from failed run: {output_path}" in stderr.getvalue()


def test_failed_output_stats_verification_removes_rejected_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, image_path, output_path = _mock_quick_encode_run(monkeypatch, tmp_path)
    stderr = StringIO()

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        output_path.write_bytes(b"video")
        return 0

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)
    monkeypatch.setattr(
        "yaatv.cli.probe_output",
        lambda _ffprobe, _output_path: OutputStats(
            width=640,
            height=360,
            video_codec="mpeg4",
            pixel_format="yuv420p",
            color_range="tv",
            color_space="bt709",
            color_transfer="bt709",
            color_primaries="bt709",
            frame_rate=1.0,
            audio_codec="aac",
            audio_sample_rate=48_000,
        ),
    )

    with pytest.raises(YaatvError, match="Output verification failed"):
        run(
            [str(audio_path), str(image_path), "-o", str(output_path), "--overwrite"],
            stdin=StringIO(),
            stderr=stderr,
        )

    assert not output_path.exists()
    assert f"warning: removed partial output from failed run: {output_path}" in stderr.getvalue()


def test_failed_encode_without_output_creation_removes_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, image_path, output_path = _mock_quick_encode_run(monkeypatch, tmp_path)
    stderr = StringIO()

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        return 1

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)

    assert run(
        [str(audio_path), str(image_path), "-o", str(output_path), "--overwrite"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 1

    assert not output_path.exists()
    assert "removed partial output" not in stderr.getvalue()


def test_failed_encode_removes_replaced_output_when_overwrite_was_allowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, image_path, output_path = _mock_quick_encode_run(monkeypatch, tmp_path)
    output_path.write_bytes(b"previous output")
    stderr = StringIO()

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        output_path.write_bytes(b"partial")
        return 1

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)

    assert run(
        [str(audio_path), str(image_path), "-o", str(output_path), "--overwrite"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 1

    assert not output_path.exists()
    assert f"warning: removed partial output from failed run: {output_path}" in stderr.getvalue()


def test_failed_encode_never_touches_preexisting_output_without_permission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, image_path, output_path = _mock_quick_encode_run(monkeypatch, tmp_path)
    output_path.write_bytes(b"previous output")

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        raise AssertionError("encoding must not start without overwrite permission")

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)

    with pytest.raises(YaatvError, match="Output already exists"):
        run(
            [str(audio_path), str(image_path), "-o", str(output_path)],
            stdin=StringIO(),
            stderr=StringIO(),
        )

    assert output_path.read_bytes() == b"previous output"


def test_output_cleanup_failure_warns_without_hiding_original_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path, image_path, output_path = _mock_quick_encode_run(monkeypatch, tmp_path)
    stderr = StringIO()

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        output_path.write_bytes(b"partial")
        return 1

    def locked_unlink(self: Path, missing_ok: bool = False) -> None:
        raise OSError("file is locked")

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)
    monkeypatch.setattr("pathlib.Path.unlink", locked_unlink)

    assert run(
        [str(audio_path), str(image_path), "-o", str(output_path), "--overwrite"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 1

    output = stderr.getvalue()
    assert f"warning: could not remove partial output {output_path}: file is locked" in output
    assert "error: FFmpeg failed with exit code 1" in output
    assert output_path.read_bytes() == b"partial"


def test_run_uses_output_dir_and_overwrite_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio_path = tmp_path / "track.flac"
    image_path = tmp_path / "cover.jpg"
    output_dir = tmp_path / "uploads"
    output_path = output_dir / "Artist - Title.mp4"
    audio_path.write_bytes(b"audio")
    image_path.write_bytes(b"image")
    output_dir.mkdir()
    output_path.write_bytes(b"existing")
    stderr = StringIO()
    captured: dict[str, object] = {}

    monkeypatch.setattr("yaatv.cli.resolve_ffmpeg_tools", lambda **_kwargs: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(
        "yaatv.cli.read_audio_metadata",
        lambda _path: AudioMetadata(
            codec="flac",
            bitrate=900_000,
            sample_rate=44_100,
            artist="Artist",
            title="Title",
            duration=12.1,
        ),
    )
    monkeypatch.setattr("yaatv.cli.validate_image", lambda _path: (1920, 1080))

    def encode(command: list[str], *, verbose: bool = False) -> int:
        captured["command"] = command
        return 0

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)
    monkeypatch.setattr(
        "yaatv.cli.probe_output",
        lambda _ffprobe, _output_path: OutputStats(
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
            duration=12.1,
        ),
    )

    assert run(
        [str(audio_path), str(image_path), "--output-dir", str(output_dir), "--overwrite"],
        stdin=StringIO(),
        stderr=stderr,
    ) == 0
    assert captured["command"][1] == "-y"
    assert str(output_path) in captured["command"]


@pytest.mark.parametrize("no_warn", [False, True])
def test_run_dry_run_allows_color_only_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    no_warn: bool,
) -> None:
    audio_path = tmp_path / "track.flac"
    output_path = tmp_path / "out.mp4"
    audio_path.write_bytes(b"audio")
    stderr = StringIO()

    monkeypatch.setattr("yaatv.cli.resolve_ffmpeg_tools", lambda **_kwargs: ("ffmpeg", "ffprobe"))
    monkeypatch.setattr(
        "yaatv.cli.read_audio_metadata",
        lambda _path: AudioMetadata(
            codec="flac",
            bitrate=192_000,
            sample_rate=44_100,
            artist=None,
            title=None,
            duration=12.1,
        ),
    )

    def encode(_command: list[str], *, verbose: bool = False) -> int:
        raise AssertionError("dry run must not encode")

    monkeypatch.setattr("yaatv.cli.run_ffmpeg", encode)

    args = ["-a", str(audio_path), "--bg-color", "white", "-o", str(output_path), "--dry-run"]
    if no_warn:
        args.append("--no-warn")

    assert run(args, stdin=StringIO(), stderr=stderr) == 0
    output = stderr.getvalue()
    assert "color=c=0xffffff:s=1920x1080:d=12.1" in output
    assert str(output_path) in output
    assert ("warning: source audio bitrate is 192kbps" in output) is not no_warn
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


def test_install_windows_ffmpeg_rejects_archive_without_required_tools(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ffmpeg-build/bin/ffplay.exe", b"ffplay")
    archive_bytes = buffer.getvalue()

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(archive_bytes)

    monkeypatch.setattr("yaatv.cli._download_url", download)

    with pytest.raises(YaatvError, match="did not contain bin/ffmpeg.exe"):
        install_windows_ffmpeg(
            install_dir=tmp_path / "yaatv" / "bin",
            expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
            stderr=StringIO(),
        )


def test_install_linux_ffmpeg_rejects_corrupt_zip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive_bytes = b"not a zip archive"

    def download(_url: str, destination: Path) -> None:
        destination.write_bytes(archive_bytes)

    monkeypatch.setattr("yaatv.cli._download_url", download)

    with pytest.raises(YaatvError, match="ffmpeg archive is not a valid ZIP file"):
        install_linux_ffmpeg(
            install_dir=tmp_path / "yaatv" / "bin",
            ffmpeg_expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
            ffprobe_expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
            stderr=StringIO(),
        )


def test_install_macos_ffmpeg_rejects_zip_without_requested_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ffmpeg_bytes = _single_tool_zip_bytes("not-ffmpeg", b"wrong")
    ffprobe_bytes = _single_tool_zip_bytes("ffprobe", b"ffprobe")
    archive_by_url = {
        "https://example.invalid/ffmpeg.zip": ffmpeg_bytes,
        "https://example.invalid/ffprobe.zip": ffprobe_bytes,
    }

    def download(url: str, destination: Path) -> None:
        destination.write_bytes(archive_by_url[url])

    monkeypatch.setattr("yaatv.cli._download_url", download)

    with pytest.raises(YaatvError, match="ffmpeg archive did not contain ffmpeg"):
        install_macos_ffmpeg(
            install_dir=tmp_path / "yaatv" / "bin",
            ffmpeg_archive_url="https://example.invalid/ffmpeg.zip",
            ffmpeg_expected_sha256=hashlib.sha256(ffmpeg_bytes).hexdigest(),
            ffprobe_archive_url="https://example.invalid/ffprobe.zip",
            ffprobe_expected_sha256=hashlib.sha256(ffprobe_bytes).hexdigest(),
            stderr=StringIO(),
        )


def test_download_url_uses_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def urlopen(request: Request, *, timeout: int) -> BytesIO:
        captured["url"] = request.full_url
        captured["user_agent"] = request.get_header("User-agent")
        captured["timeout"] = timeout
        return BytesIO(b"archive")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    destination = tmp_path / "ffmpeg.zip"

    _download_url("https://example.invalid/ffmpeg.zip", destination)

    assert captured == {
        "url": "https://example.invalid/ffmpeg.zip",
        "user_agent": FFMPEG_DOWNLOAD_USER_AGENT,
        "timeout": 60,
    }
    assert destination.read_bytes() == b"archive"


def test_download_url_retries_once_after_network_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0

    def urlopen(_request: Request, *, timeout: int) -> BytesIO:
        nonlocal attempts
        assert timeout == 60
        attempts += 1
        if attempts == 1:
            raise OSError("temporary failure")
        return BytesIO(b"archive")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    destination = tmp_path / "ffmpeg.zip"

    _download_url("https://example.invalid/ffmpeg.zip", destination)

    assert attempts == 2
    assert destination.read_bytes() == b"archive"


def test_download_url_reports_failure_after_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    attempts = 0

    def urlopen(_request: Request, *, timeout: int) -> BytesIO:
        nonlocal attempts
        assert timeout == 60
        attempts += 1
        raise OSError("offline")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    with pytest.raises(OSError, match="after 2 attempts"):
        _download_url("https://example.invalid/ffmpeg.zip", tmp_path / "ffmpeg.zip")

    assert attempts == 2


def test_download_url_rejects_non_https_scheme(tmp_path: Path) -> None:
    with pytest.raises(YaatvError, match="Unsupported download URL scheme"):
        _download_url("http://example.invalid/ffmpeg.zip", tmp_path / "ffmpeg.zip")
    with pytest.raises(YaatvError, match="Unsupported download URL scheme"):
        _download_url("file:///etc/passwd", tmp_path / "ffmpeg.zip")


def test_install_staged_tools_preserves_existing_tool_when_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging_dir = tmp_path / "staging"
    install_dir = tmp_path / "install"
    staging_dir.mkdir()
    install_dir.mkdir()
    (staging_dir / "ffmpeg").write_bytes(b"new ffmpeg")
    (install_dir / "ffmpeg").write_bytes(b"old ffmpeg")

    def replace(_source: Path, _target: Path) -> None:
        raise OSError("locked")

    monkeypatch.setattr("os.replace", replace)

    with pytest.raises(YaatvError, match="Could not install FFmpeg tools"):
        _install_staged_tools(staging_dir, install_dir, ("ffmpeg",), executable=True)

    assert (install_dir / "ffmpeg").read_bytes() == b"old ffmpeg"
    assert not (install_dir / ".ffmpeg.tmp").exists()


def test_install_staged_tools_rolls_back_full_pair_when_second_replace_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging_dir = tmp_path / "staging"
    install_dir = tmp_path / "install"
    staging_dir.mkdir()
    install_dir.mkdir()
    (staging_dir / "ffmpeg").write_bytes(b"new ffmpeg")
    (staging_dir / "ffprobe").write_bytes(b"new ffprobe")
    (install_dir / "ffmpeg").write_bytes(b"old ffmpeg")
    (install_dir / "ffprobe").write_bytes(b"old ffprobe")

    real_replace = os.replace
    call_count = 0

    def replace(source: Path, target: Path) -> None:
        nonlocal call_count
        call_count += 1
        # Let the snapshot phase (calls 1-2) and first commit (call 3) succeed.
        # Fail on the second commit (call 4) so the rollback (calls 5-6) can run.
        if call_count == 4:
            raise OSError("locked")
        real_replace(source, target)

    monkeypatch.setattr("os.replace", replace)

    with pytest.raises(YaatvError, match="Could not install FFmpeg tools"):
        _install_staged_tools(staging_dir, install_dir, ("ffmpeg", "ffprobe"), executable=False)

    assert (install_dir / "ffmpeg").read_bytes() == b"old ffmpeg"
    assert (install_dir / "ffprobe").read_bytes() == b"old ffprobe"
    assert not (install_dir / ".ffmpeg.tmp").exists()
    assert not (install_dir / ".ffprobe.tmp").exists()
    assert not (install_dir / ".ffmpeg.bak").exists()
    assert not (install_dir / ".ffprobe.bak").exists()


def test_install_staged_tools_rolls_back_pair_when_only_one_tool_existed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging_dir = tmp_path / "staging"
    install_dir = tmp_path / "install"
    staging_dir.mkdir()
    install_dir.mkdir()
    (staging_dir / "ffmpeg").write_bytes(b"new ffmpeg")
    (staging_dir / "ffprobe").write_bytes(b"new ffprobe")
    (install_dir / "ffprobe").write_bytes(b"old ffprobe")

    real_replace = os.replace

    def replace(source: Path, target: Path) -> None:
        if Path(target).name == "ffmpeg":
            raise OSError("locked")
        real_replace(source, target)

    monkeypatch.setattr("os.replace", replace)

    with pytest.raises(YaatvError, match="Could not install FFmpeg tools"):
        _install_staged_tools(staging_dir, install_dir, ("ffmpeg", "ffprobe"), executable=False)

    assert not (install_dir / "ffmpeg").exists()
    assert (install_dir / "ffprobe").read_bytes() == b"old ffprobe"
    assert not (install_dir / ".ffmpeg.tmp").exists()
    assert not (install_dir / ".ffprobe.bak").exists()


def test_install_staged_tools_first_install_rolls_back_to_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging_dir = tmp_path / "staging"
    install_dir = tmp_path / "install"
    staging_dir.mkdir()
    (staging_dir / "ffmpeg").write_bytes(b"new ffmpeg")
    (staging_dir / "ffprobe").write_bytes(b"new ffprobe")

    def replace(_source: Path, _target: Path) -> None:
        raise OSError("locked")

    monkeypatch.setattr("os.replace", replace)

    with pytest.raises(YaatvError, match="Could not install FFmpeg tools"):
        _install_staged_tools(staging_dir, install_dir, ("ffmpeg", "ffprobe"), executable=False)

    assert list(install_dir.iterdir()) == []


def test_install_staged_tools_removes_backups_after_successful_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staging_dir = tmp_path / "staging"
    install_dir = tmp_path / "install"
    staging_dir.mkdir()
    install_dir.mkdir()
    (staging_dir / "ffmpeg").write_bytes(b"new ffmpeg")
    (staging_dir / "ffprobe").write_bytes(b"new ffprobe")
    (install_dir / "ffmpeg").write_bytes(b"old ffmpeg")
    (install_dir / "ffprobe").write_bytes(b"old ffprobe")

    _install_staged_tools(staging_dir, install_dir, ("ffmpeg", "ffprobe"), executable=False)

    assert (install_dir / "ffmpeg").read_bytes() == b"new ffmpeg"
    assert (install_dir / "ffprobe").read_bytes() == b"new ffprobe"
    assert not (install_dir / ".ffmpeg.bak").exists()
    assert not (install_dir / ".ffprobe.bak").exists()


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
    ffmpeg_bytes = _single_tool_zip_bytes("ffmpeg", b"ffmpeg")
    ffprobe_bytes = _single_tool_zip_bytes("ffprobe", b"ffprobe")
    archive_by_url = {
        LINUX_FFMPEG_ARCHIVE_URL: ffmpeg_bytes,
        LINUX_FFPROBE_ARCHIVE_URL: ffprobe_bytes,
    }

    def download(url: str, destination: Path) -> None:
        destination.write_bytes(archive_by_url[url])

    monkeypatch.setattr("yaatv.cli._download_url", download)
    install_dir = tmp_path / "yaatv" / "bin"

    assert install_linux_ffmpeg(
        install_dir=install_dir,
        ffmpeg_expected_sha256=hashlib.sha256(ffmpeg_bytes).hexdigest(),
        ffprobe_expected_sha256=hashlib.sha256(ffprobe_bytes).hexdigest(),
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


def test_extract_embedded_cover_uses_apic_tag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeAudio:
        info = object()

        def __init__(self) -> None:
            self.tags = {"APIC:": type("FakePicture", (), {"data": _image_bytes(), "mime": "image/jpeg"})()}

    audio_path = tmp_path / "track.mp3"
    audio_path.write_bytes(b"audio")
    output_dir = tmp_path / "covers"
    output_dir.mkdir()
    monkeypatch.setattr("yaatv.cli.MutagenFile", lambda _path: FakeAudio())

    cover_path = extract_embedded_cover(audio_path, output_dir)

    assert cover_path is not None
    assert cover_path.parent == output_dir
    assert validate_image(cover_path) == (16, 16)


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


def test_overwrite_flag_skips_prompt(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    output.write_bytes(b"existing")

    assert confirm_overwrite(output, stdin=StringIO(), stderr=StringIO(), overwrite=True) is True


def test_existing_output_prompts_when_interactive(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    output.write_bytes(b"existing")
    stderr = StringIO()

    assert confirm_overwrite(output, stdin=_TtyInput("y\n"), stderr=stderr) is True
    assert f"Output already exists: {output}" in stderr.getvalue()


def test_normalize_output_path_rejects_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(YaatvError, match="Output directory does not exist"):
        normalize_output_path(tmp_path / "missing" / "out.mp4")


@pytest.mark.parametrize("filename", ["out.avi", "out.mkv", "out"])
def test_normalize_output_path_rejects_unsupported_extension(filename: str) -> None:
    with pytest.raises(YaatvError, match=r"supported extensions: \.mov, \.mp4"):
        normalize_output_path(Path(filename))


@pytest.mark.parametrize("filename", ["out.mp4", "out.MP4", "out.mov", "out.MOV"])
def test_normalize_output_path_accepts_supported_extension(filename: str) -> None:
    assert normalize_output_path(Path(filename)) == Path(filename)


def test_normalize_output_path_rejects_file_parent(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(YaatvError, match="Output directory is not a directory"):
        normalize_output_path(parent / "out.mp4")


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


def test_run_ffmpeg_reports_missing_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(YaatvError, match="FFmpeg was not found"):
        run_ffmpeg(["ffmpeg", "-version"])


def test_run_ffmpeg_reports_unrunnable_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(YaatvError, match="Could not run FFmpeg"):
        run_ffmpeg(["ffmpeg", "-i", "audio.wav", "out.mp4"])


def test_probe_output_reports_unrunnable_ffprobe(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(_command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("subprocess.run", fake_run)

    with pytest.raises(YaatvError, match="Could not run FFprobe"):
        probe_output("ffprobe", Path("out.mp4"))


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


def test_prores_background_image_uses_yuv422_overlay() -> None:
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
        bg_image_path=Path("background.jpg"),
    )

    assert command[command.index("-c:v") + 1] == "prores_ks"
    assert command[command.index("-pix_fmt") + 1] == "yuv422p10le"
    assert command[command.index("-f") + 1] == "mov"
    assert command[command.index("-filter_complex") + 1].endswith(
        "format=yuv422p10le,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709[v]"
    )
    assert "force_original_aspect_ratio=increase:out_range=tv" in command[command.index("-filter_complex") + 1]
    assert "force_original_aspect_ratio=decrease:out_range=tv" in command[command.index("-filter_complex") + 1]


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


def test_verify_output_stats_accepts_square_profile() -> None:
    stats = OutputStats(
        width=1080,
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

    verify_output_stats(stats, output_size("1080p", "square"))

    assert format_output_stats(stats) == (
        "1080x1080, H.264/yuv420p, bt709, 1fps video, AAC 48kHz"
    )


def test_format_file_details_prints_size_and_duration(tmp_path: Path) -> None:
    output = tmp_path / "out.mp4"
    output.write_bytes(b"0" * 1_048_576)

    assert format_file_details(output, 222.4) == "1.0 MB, 3:42"


def test_format_file_details_omits_unavailable_values(tmp_path: Path) -> None:
    output = tmp_path / "missing.mp4"

    assert format_file_details(output, None) is None
    assert format_duration(3661) == "1:01:01"


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
