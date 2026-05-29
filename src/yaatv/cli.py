from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from mutagen import File as MutagenFile
from mutagen import MutagenError
from PIL import Image, ImageColor, UnidentifiedImageError

from . import __version__

RESOLUTIONS = {
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "4k": (3840, 2160),
}

COPY_AAC_MIN_BITRATE = 320_000
COPY_AAC_SAMPLE_RATE = 48_000
LOW_BITRATE_WARNING = 256_000
TRANSCODE_AUDIO_BITRATE = "384k"
TRANSCODE_AUDIO_SAMPLE_RATE = "48000"
DEFAULT_BACKGROUND_COLOR = "black"
KNOWN_AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".alac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}
KNOWN_IMAGE_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "CONIN$",
    "CONOUT$",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
FFMPEG_DOWNLOAD_PAGE = "https://ffmpeg.org/download.html"
FFMPEG_DOWNLOAD_TIMEOUT_SECONDS = 60
WINDOWS_FFMPEG_ARCHIVE_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "autobuild-2026-05-25-14-02/"
    "ffmpeg-n7.1.4-6-g181cfa1008-win64-gpl-7.1.zip"
)
WINDOWS_FFMPEG_ARCHIVE_SHA256 = "a995684af075645484534ba84bc6a60320735395e1640d816f43b8d4a5b5775a"
WINDOWS_FFMPEG_TOOLS = ("ffmpeg.exe", "ffprobe.exe")
LINUX_FFMPEG_ARCHIVE_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "autobuild-2026-05-25-14-02/"
    "ffmpeg-N-124633-gc79dfd29e6-linux64-gpl.tar.xz"
)
LINUX_FFMPEG_ARCHIVE_SHA256 = "de58117d6dd2c20e38e66febefe9732b00def28cf580195132478b64e679c8af"
MACOS_FFMPEG_ARCHIVE_URL = "https://evermeet.cx/ffmpeg/ffmpeg-8.1.1.zip"
MACOS_FFMPEG_ARCHIVE_SHA256 = "4610988e2f54c243c50da73a09e4e2c36d9bb77546f9aa6c84cb328dcb1a98c1"
MACOS_FFPROBE_ARCHIVE_URL = "https://evermeet.cx/ffmpeg/ffprobe-8.1.1.zip"
MACOS_FFPROBE_ARCHIVE_SHA256 = "aeade29dee3c3844e9bcc974f4ae4b29cc4f87994177d77003a8589fa531009e"
MACOS_ARM64_FFMPEG_ARCHIVE_URL = (
    "https://ffmpeg.martin-riedl.de/download/macos/arm64/1778761665_8.1.1/ffmpeg.zip"
)
MACOS_ARM64_FFMPEG_ARCHIVE_SHA256 = "a05b1a47bb3ac89a95a55eec713f8bbb347051bb07015f3b7d08fb62ed81a21e"
MACOS_ARM64_FFPROBE_ARCHIVE_URL = (
    "https://ffmpeg.martin-riedl.de/download/macos/arm64/1778761665_8.1.1/ffprobe.zip"
)
MACOS_ARM64_FFPROBE_ARCHIVE_SHA256 = "135e70d2518beeb568183952dbc4bdeca1628dd49a7376d57e6b27dbc57d209f"
UNIX_FFMPEG_TOOLS = ("ffmpeg", "ffprobe")


class YaatvError(Exception):
    """An expected user-facing failure."""


@dataclass(frozen=True)
class AudioMetadata:
    codec: str | None
    bitrate: int | None
    sample_rate: int | None
    artist: str | None
    title: str | None
    duration: float | None = None


@dataclass(frozen=True)
class AudioPlan:
    copy: bool
    codec_args: tuple[str, ...]
    filter_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputStats:
    width: int | None
    height: int | None
    video_codec: str | None
    pixel_format: str | None
    color_range: str | None
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None
    frame_rate: float | None
    audio_codec: str | None
    audio_sample_rate: int | None


def pad_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--pad must be a number of seconds") from exc

    if seconds < 0 or seconds > 10:
        raise argparse.ArgumentTypeError("--pad must be between 0 and 10 seconds")
    return seconds


def background_color(value: str) -> str:
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("--bg-color must not be empty")

    try:
        red, green, blue = ImageColor.getrgb(text)[:3]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--bg-color must be a valid #RRGGBB hex color or named CSS color: {value}"
        ) from exc

    normalized = f"0x{red:02x}{green:02x}{blue:02x}"
    if text.startswith("#"):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
            raise argparse.ArgumentTypeError(f"--bg-color must use #RRGGBB hex format: {value}")
        return normalized

    if re.fullmatch(r"[A-Za-z]+", text):
        return DEFAULT_BACKGROUND_COLOR if normalized == "0x000000" else normalized

    raise argparse.ArgumentTypeError(f"--bg-color must be a valid #RRGGBB hex color or named CSS color: {value}")


def is_default_background_color(value: str) -> bool:
    return value == DEFAULT_BACKGROUND_COLOR or value == "0x000000"


def format_seconds(seconds: float) -> str:
    seconds = float(seconds)
    return str(int(seconds)) if seconds.is_integer() else f"{seconds:g}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="yaatv",
        description="Combine an audio file and cover image into a YouTube-ready video.",
    )
    parser.add_argument(
        "-a",
        "--audio",
        type=Path,
        help="Path to audio file (required unless using --install-ffmpeg)",
    )
    parser.add_argument(
        "-i",
        "--image",
        type=Path,
        help="Path to cover image (required unless using --install-ffmpeg or color-only output)",
    )
    parser.add_argument(
        "-b",
        "--bg-image",
        type=Path,
        help="Path to background image",
    )
    parser.add_argument(
        "--bg-color",
        default=DEFAULT_BACKGROUND_COLOR,
        type=background_color,
        help="Background color as #RRGGBB or a named CSS color",
    )
    parser.add_argument(
        "--bg-blur",
        action="store_true",
        help="Use a blurred copy of the cover image as the background",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output path (default: [Artist] - [Title].mp4; .mov writes ProRes MOV)",
    )
    parser.add_argument(
        "--resolution",
        choices=tuple(RESOLUTIONS),
        default="1080p",
        help="Output resolution: 1080p, 1440p, or 4k",
    )
    parser.add_argument(
        "--pad",
        default=0.0,
        type=pad_seconds,
        help="Seconds of silence to pad at the end (default: 0, max: 10)",
    )
    parser.add_argument(
        "--no-warn",
        action="store_true",
        help="Suppress low source quality warnings",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the FFmpeg command without creating an output file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show FFmpeg progress output while encoding",
    )
    parser.add_argument(
        "--install-ffmpeg",
        action="store_true",
        help="Install FFmpeg and FFprobe into yaatv's app-managed bin directory",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv_list)
    args.bg_color_explicit = any(arg == "--bg-color" or arg.startswith("--bg-color=") for arg in argv_list)
    return args


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser()
    if not resolved.exists():
        raise YaatvError(f"{label} not found: {path}")
    if not resolved.is_file():
        raise YaatvError(f"{label} is not a file: {path}")
    return resolved


def find_ffmpeg(
    *,
    app_bin_dir: Path | None = None,
    packaged_paths: Sequence[Path] | None = None,
) -> str:
    return find_external_tool("ffmpeg", "FFmpeg", app_bin_dir=app_bin_dir, packaged_paths=packaged_paths)


def find_ffprobe(
    *,
    app_bin_dir: Path | None = None,
    packaged_paths: Sequence[Path] | None = None,
) -> str:
    return find_external_tool("ffprobe", "FFprobe", app_bin_dir=app_bin_dir, packaged_paths=packaged_paths)


def find_external_tool(
    name: str,
    label: str,
    *,
    app_bin_dir: Path | None = None,
    packaged_paths: Sequence[Path] | None = None,
) -> str:
    app_paths = app_managed_tool_paths(name, app_bin_dir=app_bin_dir)
    for candidate in app_paths:
        if candidate.is_file():
            return str(candidate)

    package_paths = bundled_tool_paths(name) if packaged_paths is None else tuple(packaged_paths)
    for candidate in package_paths:
        if candidate.is_file():
            return str(candidate)

    tool = shutil.which(name)
    if tool:
        return tool

    app_install_supported = app_bin_dir is not None or supports_app_managed_ffmpeg_install()
    raise YaatvError(missing_tool_message(name, label, app_install_supported=app_install_supported))


def missing_tool_message(name: str, label: str, *, app_install_supported: bool) -> str:
    if app_install_supported:
        return (
            f"{label} was not found. Run yaatv --install-ffmpeg to install FFmpeg for yaatv, "
            f"or install FFmpeg from {FFMPEG_DOWNLOAD_PAGE} and make sure {name} is on PATH."
        )

    return (
        f"{label} was not found. Install FFmpeg from {FFMPEG_DOWNLOAD_PAGE} and make sure "
        f"{name} is on PATH."
    )


def app_managed_tool_paths(name: str, *, app_bin_dir: Path | None = None) -> tuple[Path, ...]:
    if app_bin_dir is None:
        try:
            app_bin_dir = app_managed_ffmpeg_bin_dir()
        except YaatvError:
            return ()
    return (app_bin_dir / tool_executable_name(name),)


def tool_executable_name(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def supports_app_managed_ffmpeg_install() -> bool:
    if os.name == "nt" or sys.platform.startswith("linux"):
        return _is_x64_machine()
    if sys.platform == "darwin":
        return _is_x64_machine() or _is_arm64_machine()
    return False


def app_managed_ffmpeg_bin_dir() -> Path:
    if os.name == "nt":
        if not _is_x64_machine():
            raise YaatvError("yaatv --install-ffmpeg is only supported on Windows x64.")
        return windows_ffmpeg_bin_dir()

    if sys.platform == "darwin":
        if not (_is_x64_machine() or _is_arm64_machine()):
            raise YaatvError("yaatv --install-ffmpeg is only supported on macOS x64 and macOS arm64.")
        return Path.home() / "Library" / "Application Support" / "yaatv" / "bin"

    if sys.platform.startswith("linux"):
        if not _is_x64_machine():
            raise YaatvError("yaatv --install-ffmpeg is only supported on Linux x64.")
        data_home = os.environ.get("XDG_DATA_HOME")
        base_dir = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
        return base_dir / "yaatv" / "bin"

    raise YaatvError("yaatv --install-ffmpeg is not supported on this system.")


def _is_x64_machine() -> bool:
    return platform.machine().lower() in {"amd64", "x86_64"}


def _is_arm64_machine() -> bool:
    return platform.machine().lower() in {"arm64", "aarch64"}


def windows_ffmpeg_bin_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise YaatvError("%LOCALAPPDATA% is not set; cannot choose yaatv's FFmpeg install directory.")
    return Path(local_app_data) / "yaatv" / "bin"


def bundled_tool_paths(name: str) -> tuple[Path, ...]:
    executable = tool_executable_name(name)
    paths: list[Path] = []

    if getattr(sys, "frozen", False):
        paths.append(Path(sys.executable).resolve().parent / "bin" / executable)

    return tuple(paths)


def resolve_ffmpeg_tools(
    stdin: TextIO,
    stderr: TextIO,
    *,
    app_bin_dir: Path | None = None,
    packaged_paths: Sequence[Path] | None = None,
    install_supported: bool | None = None,
    installer: Callable[[], Path] | None = None,
) -> tuple[str, str]:
    try:
        return (
            find_ffmpeg(app_bin_dir=app_bin_dir, packaged_paths=packaged_paths),
            find_ffprobe(app_bin_dir=app_bin_dir, packaged_paths=packaged_paths),
        )
    except YaatvError as exc:
        default_install_supported = app_bin_dir is not None or supports_app_managed_ffmpeg_install()
        can_install = default_install_supported if install_supported is None else install_supported
        if not can_install or not stdin.isatty():
            raise

        print(str(exc), file=stderr)
        print("Install FFmpeg for yaatv now? [Y/n] ", end="", file=stderr, flush=True)
        answer = stdin.readline().strip().lower()
        if answer in {"n", "no"}:
            raise YaatvError("FFmpeg was not installed. Run yaatv --install-ffmpeg to install it.") from exc

        if installer is None:
            install_ffmpeg(stderr=stderr)
        else:
            installer()

        return (
            find_ffmpeg(app_bin_dir=app_bin_dir, packaged_paths=packaged_paths),
            find_ffprobe(app_bin_dir=app_bin_dir, packaged_paths=packaged_paths),
        )


def install_ffmpeg(
    *,
    install_dir: Path | None = None,
    stderr: TextIO = sys.stderr,
) -> Path:
    if os.name == "nt":
        return install_windows_ffmpeg(install_dir=install_dir, stderr=stderr)
    if sys.platform.startswith("linux"):
        return install_linux_ffmpeg(install_dir=install_dir, stderr=stderr)
    if sys.platform == "darwin":
        return install_macos_ffmpeg(install_dir=install_dir, stderr=stderr)
    raise YaatvError("yaatv --install-ffmpeg is not supported on this system.")


def install_windows_ffmpeg(
    *,
    install_dir: Path | None = None,
    archive_url: str = WINDOWS_FFMPEG_ARCHIVE_URL,
    expected_sha256: str = WINDOWS_FFMPEG_ARCHIVE_SHA256,
    stderr: TextIO = sys.stderr,
) -> Path:
    if install_dir is None:
        if os.name != "nt" or not _is_x64_machine():
            raise YaatvError("yaatv --install-ffmpeg is only supported on Windows x64.")
        install_dir = app_managed_ffmpeg_bin_dir()

    with tempfile.TemporaryDirectory(prefix="yaatv-ffmpeg-") as temp_name:
        temp_dir = Path(temp_name)
        archive_path = temp_dir / "ffmpeg.zip"
        staging_dir = temp_dir / "bin"

        print(f"Downloading FFmpeg from {archive_url}", file=stderr)
        try:
            _download_url(archive_url, archive_path)
        except OSError as exc:
            raise YaatvError(f"Could not download FFmpeg: {exc}") from exc

        _verify_sha256(archive_path, expected_sha256)
        _extract_windows_ffmpeg_tools(archive_path, staging_dir)

        _install_staged_tools(staging_dir, install_dir, WINDOWS_FFMPEG_TOOLS, executable=False)

    print(f"Installed FFmpeg and FFprobe to {install_dir}", file=stderr)
    return install_dir


def install_linux_ffmpeg(
    *,
    install_dir: Path | None = None,
    archive_url: str = LINUX_FFMPEG_ARCHIVE_URL,
    expected_sha256: str = LINUX_FFMPEG_ARCHIVE_SHA256,
    stderr: TextIO = sys.stderr,
) -> Path:
    if install_dir is None:
        if not sys.platform.startswith("linux") or not _is_x64_machine():
            raise YaatvError("yaatv --install-ffmpeg is only supported on Linux x64.")
        install_dir = app_managed_ffmpeg_bin_dir()

    with tempfile.TemporaryDirectory(prefix="yaatv-ffmpeg-") as temp_name:
        temp_dir = Path(temp_name)
        archive_path = temp_dir / "ffmpeg.tar.xz"
        staging_dir = temp_dir / "bin"

        print(f"Downloading FFmpeg from {archive_url}", file=stderr)
        try:
            _download_url(archive_url, archive_path)
        except OSError as exc:
            raise YaatvError(f"Could not download FFmpeg: {exc}") from exc

        _verify_sha256(archive_path, expected_sha256)
        _extract_tar_ffmpeg_tools(archive_path, staging_dir)
        _install_staged_tools(staging_dir, install_dir, UNIX_FFMPEG_TOOLS, executable=True)

    print(f"Installed FFmpeg and FFprobe to {install_dir}", file=stderr)
    return install_dir


def install_macos_ffmpeg(
    *,
    install_dir: Path | None = None,
    ffmpeg_archive_url: str | None = None,
    ffmpeg_expected_sha256: str | None = None,
    ffprobe_archive_url: str | None = None,
    ffprobe_expected_sha256: str | None = None,
    stderr: TextIO = sys.stderr,
) -> Path:
    if install_dir is None:
        if sys.platform != "darwin" or not (_is_x64_machine() or _is_arm64_machine()):
            raise YaatvError("yaatv --install-ffmpeg is only supported on macOS x64 and macOS arm64.")
        install_dir = app_managed_ffmpeg_bin_dir()

    if ffmpeg_archive_url is None:
        ffmpeg_archive_url = MACOS_ARM64_FFMPEG_ARCHIVE_URL if _is_arm64_machine() else MACOS_FFMPEG_ARCHIVE_URL
    if ffmpeg_expected_sha256 is None:
        ffmpeg_expected_sha256 = (
            MACOS_ARM64_FFMPEG_ARCHIVE_SHA256 if _is_arm64_machine() else MACOS_FFMPEG_ARCHIVE_SHA256
        )
    if ffprobe_archive_url is None:
        ffprobe_archive_url = MACOS_ARM64_FFPROBE_ARCHIVE_URL if _is_arm64_machine() else MACOS_FFPROBE_ARCHIVE_URL
    if ffprobe_expected_sha256 is None:
        ffprobe_expected_sha256 = (
            MACOS_ARM64_FFPROBE_ARCHIVE_SHA256 if _is_arm64_machine() else MACOS_FFPROBE_ARCHIVE_SHA256
        )

    with tempfile.TemporaryDirectory(prefix="yaatv-ffmpeg-") as temp_name:
        temp_dir = Path(temp_name)
        staging_dir = temp_dir / "bin"
        downloads = (
            (ffmpeg_archive_url, temp_dir / "ffmpeg.zip", ffmpeg_expected_sha256, "ffmpeg"),
            (ffprobe_archive_url, temp_dir / "ffprobe.zip", ffprobe_expected_sha256, "ffprobe"),
        )

        for archive_url, archive_path, expected_sha256, tool_name in downloads:
            print(f"Downloading {tool_name} from {archive_url}", file=stderr)
            try:
                _download_url(archive_url, archive_path)
            except OSError as exc:
                raise YaatvError(f"Could not download {tool_name}: {exc}") from exc

            _verify_sha256(archive_path, expected_sha256)
            _extract_zip_tool(archive_path, staging_dir, tool_name)

        _install_staged_tools(staging_dir, install_dir, UNIX_FFMPEG_TOOLS, executable=True)

    print(f"Installed FFmpeg and FFprobe to {install_dir}", file=stderr)
    return install_dir


def _install_staged_tools(
    staging_dir: Path,
    install_dir: Path,
    tool_names: Iterable[str],
    *,
    executable: bool,
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    for tool_name in tool_names:
        target = install_dir / tool_name
        if target.exists():
            target.unlink()
        shutil.move(str(staging_dir / tool_name), str(target))
        if executable:
            target.chmod(0o755)


def _download_url(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=FFMPEG_DOWNLOAD_TIMEOUT_SECONDS) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output)


def _verify_sha256(path: Path, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)

    actual_sha256 = digest.hexdigest()
    if actual_sha256.lower() != expected_sha256.lower():
        raise YaatvError(
            "FFmpeg archive checksum mismatch: "
            f"expected {expected_sha256.lower()}, got {actual_sha256.lower()}"
        )


def _extract_windows_ffmpeg_tools(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for tool_name in WINDOWS_FFMPEG_TOOLS:
                member = _find_ffmpeg_zip_member(archive, tool_name)
                with archive.open(member) as source:
                    with (destination / tool_name).open("wb") as output:
                        shutil.copyfileobj(source, output)
    except zipfile.BadZipFile as exc:
        raise YaatvError("FFmpeg archive is not a valid ZIP file.") from exc


def _find_ffmpeg_zip_member(archive: zipfile.ZipFile, tool_name: str) -> zipfile.ZipInfo:
    normalized_tool = tool_name.lower()
    candidates = []
    for member in archive.infolist():
        normalized_name = member.filename.replace("\\", "/").lower()
        if member.is_dir():
            continue
        if normalized_name != f"bin/{normalized_tool}" and not normalized_name.endswith(f"/bin/{normalized_tool}"):
            continue
        candidates.append(member)

    if not candidates:
        raise YaatvError(f"FFmpeg archive did not contain bin/{tool_name}.")
    return sorted(candidates, key=lambda member: member.filename)[0]


def _extract_tar_ffmpeg_tools(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path) as archive:
            for tool_name in UNIX_FFMPEG_TOOLS:
                member = _find_ffmpeg_tar_member(archive, tool_name)
                source = archive.extractfile(member)
                if source is None:
                    raise YaatvError(f"FFmpeg archive did not contain bin/{tool_name}.")
                with source:
                    with (destination / tool_name).open("wb") as output:
                        shutil.copyfileobj(source, output)
    except tarfile.TarError as exc:
        raise YaatvError("FFmpeg archive is not a valid tar file.") from exc


def _find_ffmpeg_tar_member(archive: tarfile.TarFile, tool_name: str) -> tarfile.TarInfo:
    normalized_tool = tool_name.lower()
    candidates = []
    for member in archive.getmembers():
        normalized_name = member.name.replace("\\", "/").lower()
        if not member.isfile():
            continue
        if normalized_name != f"bin/{normalized_tool}" and not normalized_name.endswith(f"/bin/{normalized_tool}"):
            continue
        candidates.append(member)

    if not candidates:
        raise YaatvError(f"FFmpeg archive did not contain bin/{tool_name}.")
    return sorted(candidates, key=lambda member: member.name)[0]


def _extract_zip_tool(archive_path: Path, destination: Path, tool_name: str) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            member = _find_zip_tool_member(archive, tool_name)
            with archive.open(member) as source:
                with (destination / tool_name).open("wb") as output:
                    shutil.copyfileobj(source, output)
    except zipfile.BadZipFile as exc:
        raise YaatvError(f"{tool_name} archive is not a valid ZIP file.") from exc


def _find_zip_tool_member(archive: zipfile.ZipFile, tool_name: str) -> zipfile.ZipInfo:
    normalized_tool = tool_name.lower()
    candidates = []
    for member in archive.infolist():
        normalized_name = member.filename.replace("\\", "/").lower()
        basename = normalized_name.rsplit("/", 1)[-1]
        if member.is_dir() or normalized_name.startswith("__macosx/"):
            continue
        if basename != normalized_tool:
            continue
        candidates.append(member)

    if not candidates:
        raise YaatvError(f"{tool_name} archive did not contain {tool_name}.")
    return sorted(candidates, key=lambda member: (member.filename.count("/"), member.filename))[0]


def validate_image(path: Path, label: str = "Cover image") -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) > 1:
                raise YaatvError(f"{label} must be a static image: {path}")
            image.verify()
    except YaatvError:
        raise
    except (UnidentifiedImageError, OSError) as exc:
        raise YaatvError(f"Could not read {label.lower()}: {path}") from exc

    return width, height


def read_audio_metadata(path: Path) -> AudioMetadata:
    try:
        audio = MutagenFile(path)
    except (MutagenError, OSError) as exc:
        raise YaatvError(f"Could not read audio metadata: {path}") from exc

    if audio is None or getattr(audio, "info", None) is None:
        raise YaatvError(f"Could not read audio metadata: {path}")

    info = audio.info
    bitrate = _audio_bitrate(path, info)
    sample_rate = _int_or_none(getattr(info, "sample_rate", None))
    codec = _audio_codec(audio, path)

    return AudioMetadata(
        codec=codec,
        bitrate=bitrate,
        sample_rate=sample_rate,
        artist=_tag_value(getattr(audio, "tags", None), ("artist", "albumartist", "TPE1", "\xa9ART")),
        title=_tag_value(getattr(audio, "tags", None), ("title", "TIT2", "\xa9nam")),
        duration=_float_or_none(getattr(info, "length", None)),
    )


def _audio_bitrate(path: Path, info: object) -> int | None:
    bitrate = _int_or_none(getattr(info, "bitrate", None))
    if bitrate:
        return bitrate

    length = getattr(info, "length", None)
    try:
        if length and float(length) > 0:
            return int((path.stat().st_size * 8) / float(length))
    except OSError:
        return None

    return None


def _audio_codec(audio: object, path: Path) -> str | None:
    info = getattr(audio, "info", None)
    candidates = [
        getattr(info, "codec", None),
        getattr(info, "codec_description", None),
        getattr(info, "codec_id", None),
        audio.__class__.__name__,
        path.suffix.lstrip("."),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate).strip().lower()
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _tag_value(tags: object, keys: Iterable[str]) -> str | None:
    if not tags:
        return None

    tag_keys: list[str] = []
    if hasattr(tags, "keys"):
        tag_keys = [str(key) for key in tags.keys()]

    for key in keys:
        value = _get_tag(tags, key)
        if value is None:
            lower_key = key.lower()
            matching_key = next((candidate for candidate in tag_keys if candidate.lower() == lower_key), None)
            value = _get_tag(tags, matching_key) if matching_key else None
        normalized = _normalize_tag(value)
        if normalized:
            return normalized
    return None


def _get_tag(tags: object, key: str | None) -> object | None:
    if key is None:
        return None
    try:
        return tags.get(key)  # type: ignore[attr-defined]
    except AttributeError:
        try:
            return tags[key]  # type: ignore[index]
        except (KeyError, TypeError):
            return None


def _normalize_tag(value: object) -> str | None:
    if value is None:
        return None

    text = getattr(value, "text", None)
    if text is not None:
        value = text

    if isinstance(value, (list, tuple)):
        value = value[0] if value else None

    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")

    if value is None:
        return None

    result = str(value).strip()
    return result or None


def default_output_path(audio_path: Path, metadata: AudioMetadata) -> Path:
    if metadata.artist and metadata.title:
        name = f"{metadata.artist} - {metadata.title}"
    else:
        name = audio_path.stem
    return Path(f"{sanitize_filename(name)}.mp4")


def sanitize_filename(value: str) -> str:
    sanitized = INVALID_FILENAME_CHARS.sub("_", value).strip(" .")
    sanitized = re.sub(r"\s+", " ", sanitized)
    if not sanitized:
        return "output"
    if sanitized.split(".", 1)[0].upper() in WINDOWS_RESERVED_FILENAMES:
        return f"_{sanitized}"
    return sanitized


def choose_audio_plan(metadata: AudioMetadata, pad: float) -> AudioPlan:
    if is_high_quality_aac(metadata):
        if pad > 0:
            raise YaatvError(
                "--pad cannot be used with high-quality AAC copy mode because adding silence "
                "requires audio filtering. Rerun without --pad or use a source that will be transcoded."
            )
        return AudioPlan(copy=True, codec_args=("-c:a", "copy"))

    filter_args: tuple[str, ...] = ()
    if pad > 0:
        filter_args = ("-af", f"apad=pad_dur={format_seconds(pad)}")

    return AudioPlan(
        copy=False,
        codec_args=(
            "-c:a",
            "aac",
            "-b:a",
            TRANSCODE_AUDIO_BITRATE,
            "-ar",
            TRANSCODE_AUDIO_SAMPLE_RATE,
        ),
        filter_args=filter_args,
    )


def is_high_quality_aac(metadata: AudioMetadata) -> bool:
    return (
        is_aac_codec(metadata.codec)
        and metadata.sample_rate == COPY_AAC_SAMPLE_RATE
        and metadata.bitrate is not None
        and metadata.bitrate >= COPY_AAC_MIN_BITRATE
    )


def is_aac_codec(codec: str | None) -> bool:
    if not codec:
        return False
    normalized = codec.strip().lower()
    return (
        "aac" in normalized
        or normalized == "mp4a"
        or (normalized.startswith("mp4a.40.") and not normalized.endswith(".34"))
    )


def quality_warnings(
    metadata: AudioMetadata,
    image_size: tuple[int, int],
    target_size: tuple[int, int],
) -> list[str]:
    warnings: list[str] = []
    if metadata.bitrate is not None and metadata.bitrate < LOW_BITRATE_WARNING:
        warnings.append(
            f"source audio bitrate is {metadata.bitrate // 1000}kbps, below the 256kbps warning threshold"
        )

    image_width, image_height = image_size
    target_width, target_height = target_size
    scale_factor = min(target_width / image_width, target_height / image_height)
    if scale_factor > 1:
        warnings.append(
            f"cover image is {image_width}x{image_height}; FFmpeg will upscale it for "
            f"{target_width}x{target_height}. Consider using an image at least {target_width}x{target_height}"
        )

    return warnings


def input_format_warnings(audio_path: Path, image_path: Path | None, bg_image_path: Path | None = None) -> list[str]:
    warnings: list[str] = []
    if audio_path.suffix.lower() not in KNOWN_AUDIO_EXTENSIONS:
        warnings.append(f"audio file extension is unusual: {audio_path.suffix or '(none)'}")
    if image_path is not None and image_path.suffix.lower() not in KNOWN_IMAGE_EXTENSIONS:
        warnings.append(f"cover image extension is unusual: {image_path.suffix or '(none)'}")
    if bg_image_path is not None and bg_image_path.suffix.lower() not in KNOWN_IMAGE_EXTENSIONS:
        warnings.append(f"background image extension is unusual: {bg_image_path.suffix or '(none)'}")
    return warnings


def build_ffmpeg_command(
    ffmpeg: str,
    audio_path: Path,
    image_path: Path | None,
    output_path: Path,
    target_size: tuple[int, int],
    audio_plan: AudioPlan,
    overwrite: bool,
    output_duration: float | None = None,
    is_prores: bool = False,
    bg_image_path: Path | None = None,
    bg_color: str = DEFAULT_BACKGROUND_COLOR,
    bg_blur: bool = False,
) -> list[str]:
    width, height = target_size
    video_format = "yuv422p10le" if is_prores else "yuv420p"
    video_tail = (
        f"format={video_format},"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    video_codec_args = (
        (
            "-c:v",
            "prores_ks",
            "-profile:v",
            "2",
            "-pix_fmt",
            "yuv422p10le",
            "-vendor",
            "apl0",
        )
        if is_prores
        else (
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
        )
    )
    output_format_args = ("-f", "mov") if is_prores else ()
    faststart_args = () if is_prores else ("-movflags", "+faststart")

    if image_path is None:
        color_source = f"color=c={bg_color}:s={width}x{height}"
        if output_duration is not None:
            color_source = f"{color_source}:d={format_seconds(output_duration)}"
        return [
            ffmpeg,
            "-y" if overwrite else "-n",
            "-i",
            str(audio_path),
            "-f",
            "lavfi",
            "-i",
            color_source,
            "-map",
            "1:v:0",
            "-map",
            "0:a:0",
            *video_codec_args,
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            *audio_plan.codec_args,
            *audio_plan.filter_args,
            *(("-shortest",) if output_duration is None else ()),
            *faststart_args,
            "-vf",
            f"fps=fps=1:start_time=0,{video_tail}",
            *(("-t", format_seconds(output_duration)) if output_duration is not None else ()),
            *output_format_args,
            str(output_path),
        ]

    if bg_image_path is not None:
        video_filter = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase:out_range=tv,"
            f"crop={width}:{height}[bg];"
            f"[1:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{video_tail}[v]"
        )
        return [
            ffmpeg,
            "-y" if overwrite else "-n",
            "-loop",
            "1",
            "-framerate",
            "1",
            "-i",
            str(bg_image_path),
            "-loop",
            "1",
            "-framerate",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            video_filter,
            "-map",
            "[v]",
            "-map",
            "2:a:0",
            *video_codec_args,
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            *audio_plan.codec_args,
            *audio_plan.filter_args,
            *(("-shortest",) if output_duration is None else ()),
            *faststart_args,
            *(("-t", format_seconds(output_duration)) if output_duration is not None else ()),
            *output_format_args,
            str(output_path),
        ]

    if bg_blur:
        video_filter = (
            "[0:v]split[s1][s2];"
            f"[s1]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=20:5[bg];"
            f"[s2]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{video_tail}[v]"
        )
        return [
            ffmpeg,
            "-y" if overwrite else "-n",
            "-loop",
            "1",
            "-framerate",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-filter_complex",
            video_filter,
            "-map",
            "[v]",
            "-map",
            "1:a:0",
            *video_codec_args,
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            *audio_plan.codec_args,
            *audio_plan.filter_args,
            *(("-shortest",) if output_duration is None else ()),
            *faststart_args,
            *(("-t", format_seconds(output_duration)) if output_duration is not None else ()),
            *output_format_args,
            str(output_path),
        ]

    if is_prores:
        video_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{bg_color},"
            f"format={video_format},"
            "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
        )
        return [
            ffmpeg,
            "-y" if overwrite else "-n",
            "-loop",
            "1",
            "-framerate",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "2",
            "-pix_fmt",
            "yuv422p10le",
            "-vendor",
            "apl0",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
            *audio_plan.codec_args,
            *audio_plan.filter_args,
            "-shortest",
            "-vf",
            video_filter,
            *(("-t", format_seconds(output_duration)) if output_duration is not None else ()),
            "-f",
            "mov",
            str(output_path),
        ]

    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:{bg_color},"
        f"format={video_format},"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )

    return [
        ffmpeg,
        "-y" if overwrite else "-n",
        "-loop",
        "1",
        "-framerate",
        "1",
        "-i",
        str(image_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_primaries",
        "bt709",
        *audio_plan.codec_args,
        *audio_plan.filter_args,
        "-shortest",
        "-movflags",
        "+faststart",
        "-vf",
        video_filter,
        *(("-t", format_seconds(output_duration)) if output_duration is not None else ()),
        str(output_path),
    ]


def confirm_overwrite(path: Path, stdin: TextIO, stderr: TextIO) -> bool:
    if not path.exists():
        return False

    if not stdin.isatty():
        raise YaatvError(f"Output already exists and cannot be overwritten without confirmation: {path}")

    print(f"Output already exists: {path}", file=stderr)
    print("Overwrite? [y/N] ", end="", file=stderr, flush=True)
    answer = stdin.readline().strip().lower()
    if answer in {"y", "yes"}:
        return True
    raise YaatvError("Aborted; output file was not overwritten.")


def normalize_output_path(path: Path) -> Path:
    output_path = path.expanduser()
    if output_path.exists() and output_path.is_dir():
        raise YaatvError(f"Output path is a directory: {output_path}")
    if output_path.parent != Path(".") and not output_path.parent.exists():
        raise YaatvError(f"Output directory does not exist: {output_path.parent}")
    return output_path


def quote_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(part) for part in command])


def run_ffmpeg(command: Sequence[str], *, verbose: bool = False) -> int:
    try:
        completed = subprocess.run(command, check=False, stderr=None if verbose else subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise YaatvError(
            "FFmpeg was not found. Run yaatv --install-ffmpeg to install FFmpeg for yaatv, "
            f"or install it from {FFMPEG_DOWNLOAD_PAGE} and make sure ffmpeg is on PATH."
        ) from exc
    return completed.returncode


def probe_output(ffprobe: str, output_path: Path) -> OutputStats:
    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        str(output_path),
    ]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise YaatvError(
            "FFprobe was not found. Run yaatv --install-ffmpeg to install FFmpeg for yaatv, "
            f"or install FFmpeg from {FFMPEG_DOWNLOAD_PAGE} and make sure ffprobe is on PATH."
        ) from exc

    if completed.returncode != 0:
        details = completed.stderr.strip()
        message = f"Could not verify output with FFprobe: {output_path}"
        raise YaatvError(f"{message}: {details}" if details else message)

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise YaatvError(f"Could not parse FFprobe output for: {output_path}") from exc

    streams = data.get("streams", [])
    if not isinstance(streams, list):
        streams = []
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")

    return OutputStats(
        width=_int_or_none(video.get("width")),
        height=_int_or_none(video.get("height")),
        video_codec=_string_or_none(video.get("codec_name")),
        pixel_format=_string_or_none(video.get("pix_fmt")),
        color_range=_string_or_none(video.get("color_range")),
        color_space=_string_or_none(video.get("color_space")),
        color_transfer=_string_or_none(video.get("color_transfer")),
        color_primaries=_string_or_none(video.get("color_primaries")),
        frame_rate=_rate_or_none(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        audio_codec=_string_or_none(audio.get("codec_name")),
        audio_sample_rate=_int_or_none(audio.get("sample_rate")),
    )


def verify_output_stats(stats: OutputStats, target_size: tuple[int, int], is_prores: bool = False) -> None:
    target_width, target_height = target_size
    failures: list[str] = []

    if (stats.width, stats.height) != target_size:
        failures.append(f"expected {target_width}x{target_height}, got {_resolution_label(stats)}")

    if is_prores:
        if stats.video_codec != "prores":
            failures.append(f"expected ProRes video, got {stats.video_codec or 'unknown'}")
        if stats.pixel_format != "yuv422p10le":
            failures.append(f"expected yuv422p10le video, got {stats.pixel_format or 'unknown'}")
    else:
        if stats.video_codec != "h264":
            failures.append(f"expected H.264 video, got {stats.video_codec or 'unknown'}")
        if stats.pixel_format != "yuv420p":
            failures.append(f"expected yuv420p video, got {stats.pixel_format or 'unknown'}")
    # Some FFprobe builds do not report color_range for ProRes MOV.
    if stats.color_range not in {"tv", "mpeg"} and not (is_prores and stats.color_range is None):
        failures.append(f"expected limited color range, got {stats.color_range or 'unknown'}")
    if stats.color_space != "bt709":
        failures.append(f"expected bt709 colorspace, got {stats.color_space or 'unknown'}")
    if stats.color_transfer != "bt709":
        failures.append(f"expected bt709 transfer, got {stats.color_transfer or 'unknown'}")
    if stats.color_primaries != "bt709":
        failures.append(f"expected bt709 primaries, got {stats.color_primaries or 'unknown'}")
    if stats.frame_rate is None or abs(stats.frame_rate - 1.0) > 0.01:
        failures.append(f"expected 1fps video, got {_frame_rate_label(stats.frame_rate)}")
    if stats.audio_codec != "aac":
        failures.append(f"expected AAC audio, got {stats.audio_codec or 'unknown'}")
    if stats.audio_sample_rate != COPY_AAC_SAMPLE_RATE:
        failures.append(
            f"expected 48kHz audio, got {_sample_rate_label(stats.audio_sample_rate)}"
        )

    if failures:
        raise YaatvError("Output verification failed: " + "; ".join(failures))


def format_output_stats(stats: OutputStats) -> str:
    return ", ".join(
        (
            _resolution_label(stats),
            f"{_video_codec_label(stats.video_codec)}/{stats.pixel_format or 'unknown'}",
            _color_label(stats),
            f"{_frame_rate_label(stats.frame_rate)} video",
            f"{_audio_codec_label(stats.audio_codec)} {_sample_rate_label(stats.audio_sample_rate)}",
        )
    )


def print_output_summary(output_path: Path, stats: OutputStats, stderr: TextIO) -> None:
    print(f"Created {output_path}", file=stderr)
    print(f"Verified: {format_output_stats(stats)}", file=stderr)


def _first_stream(streams: Iterable[object], codec_type: str) -> dict[str, object]:
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == codec_type:
            return stream
    return {}


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result if result and result != "N/A" else None


def _rate_or_none(value: object) -> float | None:
    text = _string_or_none(value)
    if not text:
        return None
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else None
        return float(text)
    except ValueError:
        return None


def _resolution_label(stats: OutputStats) -> str:
    if stats.width is None or stats.height is None:
        return "unknown resolution"
    return f"{stats.width}x{stats.height}"


def _video_codec_label(codec: str | None) -> str:
    if codec == "h264":
        return "H.264"
    if codec == "prores":
        return "ProRes 422"
    return codec or "unknown"


def _audio_codec_label(codec: str | None) -> str:
    return "AAC" if codec == "aac" else codec or "unknown"


def _color_label(stats: OutputStats) -> str:
    values = {stats.color_space, stats.color_transfer, stats.color_primaries}
    if values == {"bt709"}:
        return "bt709"
    return "/".join(value or "unknown" for value in (stats.color_space, stats.color_transfer, stats.color_primaries))


def _frame_rate_label(frame_rate: float | None) -> str:
    if frame_rate is None:
        return "unknown fps"
    return f"{frame_rate:g}fps"


def _sample_rate_label(sample_rate: int | None) -> str:
    if sample_rate is None:
        return "unknown sample rate"
    if sample_rate % 1000 == 0:
        return f"{sample_rate // 1000}kHz"
    return f"{sample_rate}Hz"


def run(
    argv: Sequence[str] | None = None,
    stdin: TextIO = sys.stdin,
    stderr: TextIO = sys.stderr,
) -> int:
    args = parse_args(argv)
    if args.install_ffmpeg:
        install_ffmpeg(stderr=stderr)
        return 0

    if args.audio is None:
        raise YaatvError("Audio file is required. Use -a/--audio to choose one.")
    color_only = args.image is None and args.bg_color_explicit and not is_default_background_color(args.bg_color)
    if args.image is None and args.bg_blur:
        raise YaatvError("--bg-blur requires a cover image. Use -i/--image to choose one.")
    if args.image is None and args.bg_image is not None:
        raise YaatvError("--bg-image requires a cover image. Use -i/--image to choose one.")
    if args.image is None and not color_only:
        raise YaatvError("Cover image is required. Use -i/--image to choose one.")

    audio_path = require_file(args.audio, "Audio file")
    image_path = require_file(args.image, "Cover image") if args.image is not None else None
    bg_image_path = require_file(args.bg_image, "Background image") if args.bg_image is not None else None
    ffmpeg, ffprobe = resolve_ffmpeg_tools(stdin=stdin, stderr=stderr)
    metadata = read_audio_metadata(audio_path)
    image_size = validate_image(image_path) if image_path is not None else None
    if bg_image_path is not None:
        validate_image(bg_image_path, "Background image")
    target_size = RESOLUTIONS[args.resolution]
    output_path = normalize_output_path(args.output if args.output else default_output_path(audio_path, metadata))
    overwrite = confirm_overwrite(output_path, stdin=stdin, stderr=stderr)
    audio_plan = choose_audio_plan(metadata, args.pad)
    output_duration = math.ceil(metadata.duration + args.pad) if metadata.duration is not None else None

    is_prores = output_path.suffix.lower() == ".mov"

    if not args.no_warn:
        warnings = input_format_warnings(audio_path, image_path, bg_image_path)
        if image_size is not None:
            warnings.extend(quality_warnings(metadata, image_size, target_size))
        for warning in [
            *warnings,
        ]:
            print(f"warning: {warning}", file=stderr)
    if is_prores:
        print("note: .mov output uses ProRes 422; file sizes will be very large", file=stderr)

    command = build_ffmpeg_command(
        ffmpeg=ffmpeg,
        audio_path=audio_path,
        image_path=image_path,
        output_path=output_path,
        target_size=target_size,
        audio_plan=audio_plan,
        overwrite=overwrite,
        output_duration=output_duration,
        is_prores=is_prores,
        bg_image_path=bg_image_path,
        bg_color=args.bg_color,
        bg_blur=args.bg_blur,
    )
    if args.dry_run:
        print(quote_command(command), file=stderr)
        return 0

    exit_code = run_ffmpeg(command, verbose=args.verbose)
    if exit_code != 0:
        if not args.verbose:
            print(
                f"error: FFmpeg failed with exit code {exit_code}. Rerun with --verbose to show FFmpeg output.",
                file=stderr,
            )
        return exit_code

    stats = probe_output(ffprobe, output_path)
    verify_output_stats(stats, target_size, is_prores=is_prores)
    print_output_summary(output_path, stats, stderr=stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except YaatvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
