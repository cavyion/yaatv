from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence, TextIO

from mutagen import File as MutagenFile
from mutagen import MutagenError
from PIL import Image, UnidentifiedImageError

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
INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


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


def pad_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--pad must be a number of seconds") from exc

    if seconds < 0 or seconds > 10:
        raise argparse.ArgumentTypeError("--pad must be between 0 and 10 seconds")
    return seconds


def format_seconds(seconds: float) -> str:
    seconds = float(seconds)
    return str(int(seconds)) if seconds.is_integer() else f"{seconds:g}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="yaatv",
        description="Combine an audio file and cover image into a YouTube-optimized MP4.",
    )
    parser.add_argument("-a", "--audio", required=True, type=Path, help="Path to audio file")
    parser.add_argument("-i", "--image", required=True, type=Path, help="Path to cover image")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output path (default: [Artist] - [Title].mp4)",
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser()
    if not resolved.exists():
        raise YaatvError(f"{label} not found: {path}")
    if not resolved.is_file():
        raise YaatvError(f"{label} is not a file: {path}")
    return resolved


def find_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise YaatvError(
            "FFmpeg was not found on PATH. Install it from https://ffmpeg.org/download.html"
        )
    return ffmpeg


def validate_image(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise YaatvError(f"Could not read cover image: {path}") from exc

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
    duration = _positive_float_or_none(getattr(info, "length", None))
    codec = _audio_codec(audio, path)

    return AudioMetadata(
        codec=codec,
        bitrate=bitrate,
        sample_rate=sample_rate,
        artist=_tag_value(getattr(audio, "tags", None), ("artist", "albumartist", "TPE1", "\xa9ART")),
        title=_tag_value(getattr(audio, "tags", None), ("title", "TIT2", "\xa9nam")),
        duration=duration,
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


def _int_or_none(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _positive_float_or_none(value: object) -> float | None:
    try:
        result = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return result if result and result > 0 else None


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
    return sanitized or "output"


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


def output_duration(metadata: AudioMetadata, pad: float) -> float | None:
    if metadata.duration is None:
        return None
    return metadata.duration + pad


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
            f"cover image is {image_width}x{image_height}; FFmpeg will upscale it for {target_width}x{target_height}"
        )

    return warnings


def build_ffmpeg_command(
    ffmpeg: str,
    audio_path: Path,
    image_path: Path,
    output_path: Path,
    target_size: tuple[int, int],
    audio_plan: AudioPlan,
    overwrite: bool,
    output_duration_seconds: float | None = None,
) -> list[str]:
    width, height = target_size
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:out_range=tv,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        "format=yuv420p,"
        "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
    )
    duration_args = (
        ["-t", format_seconds(output_duration_seconds)]
        if output_duration_seconds is not None
        else ["-shortest"]
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
        *duration_args,
        "-movflags",
        "+faststart",
        "-vf",
        video_filter,
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


def run_ffmpeg(command: Sequence[str]) -> int:
    try:
        completed = subprocess.run(command, check=False)
    except FileNotFoundError as exc:
        raise YaatvError(
            "FFmpeg was not found on PATH. Install it from https://ffmpeg.org/download.html"
        ) from exc
    return completed.returncode


def run(argv: Sequence[str] | None = None, stdin: TextIO = sys.stdin, stderr: TextIO = sys.stderr) -> int:
    args = parse_args(argv)
    audio_path = require_file(args.audio, "Audio file")
    image_path = require_file(args.image, "Cover image")
    ffmpeg = find_ffmpeg()
    metadata = read_audio_metadata(audio_path)
    image_size = validate_image(image_path)
    target_size = RESOLUTIONS[args.resolution]
    output_path = normalize_output_path(args.output if args.output else default_output_path(audio_path, metadata))
    overwrite = confirm_overwrite(output_path, stdin=stdin, stderr=stderr)
    audio_plan = choose_audio_plan(metadata, args.pad)

    if not args.no_warn:
        for warning in quality_warnings(metadata, image_size, target_size):
            print(f"warning: {warning}", file=stderr)

    command = build_ffmpeg_command(
        ffmpeg=ffmpeg,
        audio_path=audio_path,
        image_path=image_path,
        output_path=output_path,
        target_size=target_size,
        audio_plan=audio_plan,
        overwrite=overwrite,
        output_duration_seconds=output_duration(metadata, args.pad),
    )
    return run_ffmpeg(command)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(argv)
    except YaatvError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
