# yaatv

yaatv turns audio and cover art into a YouTube-ready video without opening a video editor.

It is built for audio creators: producers, ASMRtists, podcasters, DJs, narrators, and anyone publishing audio with a static image.

Works with common audio files like WAV, FLAC, MP3, M4A/AAC, OGG, and Opus, plus static cover images like JPG, PNG, and WebP. Animated images are not accepted.

```sh
yaatv -a audio.flac -i cover.jpg -o upload.mp4
```

Give it audio. Give it artwork. Get a video you can upload.

Website and docs: <https://yaatv.org>

## Download

Download the ZIP for your computer from the latest release:

<https://github.com/cavyion/yaatv/releases/latest>

Use one of these release assets:

- Windows x64: `yaatv-windows-x64.zip` containing `yaatv.exe` and runtime files
- Linux x64: `yaatv-linux-x64.zip` containing `yaatv-linux` and runtime files
- macOS x64: `yaatv-macos-x64.zip` containing `yaatv-macos` and runtime files

You can ignore GitHub's "Source code (zip)" and "Source code (tar.gz)" files unless you specifically want the code.

Release ZIPs include the yaatv executable without FFmpeg. Run `--install-ffmpeg` once to install FFmpeg and FFprobe into yaatv's app-managed bin directory; yaatv checks that location before PATH and does not modify system PATH. Normal encoding stays local after those tools are installed.

## Run

yaatv is a command-line app. Do not double-click the executable; open PowerShell or a terminal in the extracted folder and run it with your audio and image paths.

On Windows, run the executable from PowerShell:

```powershell
.\yaatv.exe --version
.\yaatv.exe --install-ffmpeg
.\yaatv.exe -a audio.flac -i cover.jpg -o output.mp4
```

If FFmpeg is missing during an interactive run, yaatv asks before installing it. In non-interactive runs, install FFmpeg first with `--install-ffmpeg`.

On Linux:

```sh
chmod +x ./yaatv-linux
./yaatv-linux --version
./yaatv-linux --install-ffmpeg
./yaatv-linux -a episode.wav -i cover.jpg -o output.mp4
```

On macOS:

```sh
chmod +x ./yaatv-macos
./yaatv-macos --version
./yaatv-macos --install-ffmpeg
./yaatv-macos -a session.mp3 -i cover.jpg -o output.mp4
```

The macOS build is x64 and unsigned. Apple Silicon Macs may need Rosetta installed. `--install-ffmpeg` supports both macOS x64 and Apple Silicon. If macOS blocks the file after download, allow it from System Settings, or remove the quarantine flag:

```sh
xattr -d com.apple.quarantine ./yaatv-macos
```

## Usage

The smallest command uses the audio file name, or artist/title tags when available, for the output file:

```sh
yaatv -a audio.flac -i cover.jpg
```

Choose the output file and resolution:

```sh
yaatv -a episode.wav -i cover.jpg -o output.mp4 --resolution 1440p
```

Flags:

- `-a`, `--audio`: audio file, required unless using `--install-ffmpeg`
- `-i`, `--image`: cover image, required unless using `--install-ffmpeg`
- `-o`, `--output`: output path, default is `[Artist] - [Title].mp4` when tags are available; `.mov` writes ProRes MOV
- `--resolution`: `1080p`, `1440p`, or `4k`, default is `1080p`
- `--pad`: seconds of silence to add at the end, default is `0`, max is `10`
- `--no-warn`: hide low source quality warnings
- `--dry-run`: print the FFmpeg command without creating an output file
- `--verbose`: show FFmpeg progress output while encoding
- `--install-ffmpeg`: install FFmpeg and FFprobe into yaatv's app-managed bin directory

## Source files

yaatv accepts common audio files and static cover images. Higher-quality source files give cleaner uploads, especially at 1440p and 4k.

Audio:

- WAV, FLAC, MP3, M4A/AAC, OGG, and Opus are supported.
- Use WAV, FLAC, or high-bitrate AAC when available.
- 24-bit WAV is a good source format.

Cover image:

- Use at least 1920x1080 for 1080p, 2560x1440 for 1440p, or 3840x2160 for 4k.
- Larger square artwork, such as 3000x3000, can avoid upscaling for 4k output.
- Use JPG, PNG, or static WebP. GIF and animated WebP files are rejected.
- Square images fill the whole frame. Non-square images get black bars on the sides.

yaatv warns you if your source is smaller than the output resolution. Pass `--no-warn` to hide those warnings.

## Output

yaatv creates an MP4 by default. If the output path ends in `.mov`, yaatv creates a ProRes MOV instead.

- MP4 output uses H.264 video at CRF 16, preset `slow`, yuv420p pixel format, and `+faststart`.
- MOV output uses ProRes 422 profile 2, yuv422p10le pixel format, and a MOV container. MOV files are much larger than MP4 files.
- Audio is kept in an upload-friendly format. High-quality AAC can be copied directly when no padding is needed.
- Cover images keep their aspect ratio. yaatv adds black bars instead of stretching.
- Video is encoded for broad playback compatibility and YouTube uploads.
- Completed files are checked after encoding and summarized before yaatv exits.
- Existing output files require confirmation before overwrite.

Low-quality source audio, unusual file extensions, and cover images smaller than the target resolution print warnings unless `--no-warn` is set.

`--pad` cannot be used with high-quality AAC copy mode because adding silence requires a re-encode.

## Python install (optional)

If you prefer to run yaatv as a Python CLI, install it from this repository:

```sh
python -m pip install "git+https://github.com/cavyion/yaatv.git"
yaatv --version
```

Python 3.10 or newer is required.

Python installs do not bundle FFmpeg. On supported systems, `yaatv --install-ffmpeg` installs yaatv's app-managed copy. If you use your own FFmpeg install instead, make sure both commands work:

```sh
ffmpeg -version
ffprobe -version
```

Download FFmpeg from <https://ffmpeg.org/download.html> if you want to manage the tools yourself.

## Third-party binaries

The release ZIPs include `LICENSE`, `THIRD_PARTY_NOTICES.md`, and `FFMPEG_BUILD_INFO.txt`. The yaatv source code is MIT licensed; third-party runtime and media components keep their own licenses. FFmpeg and FFprobe are downloaded only when `--install-ffmpeg` is used. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for source and license links before redistributing release binaries.

## Development

Install the project with test/build tools:

```sh
python -m pip install -e ".[dev]"
```

Run the same checks used by CI:

```sh
yaatv --version
python -m yaatv --version
python -m ruff check .
python -m mypy
python -m bandit -c pyproject.toml -r src
python -m pip_audit . --strict
python -m pytest
python -m build
python -m twine check dist/*
```

## Publishing

Tagging a version that starts with `v` builds the Windows, Linux, and macOS assets, then attaches them to a GitHub release.

```sh
git tag v0.5.1
git push origin main --tags
```

The website is served from `docs/` with GitHub Pages and uses `docs/CNAME` for `yaatv.org`.
