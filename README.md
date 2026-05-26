# yaatv

yaatv turns audio and cover art into a YouTube-ready MP4 without opening a video editor.

It is built for audio creators: producers, ASMRtists, podcasters, DJs, narrators, and anyone publishing audio with a static image.

Works with common audio files like WAV, FLAC, MP3, M4A/AAC, OGG, and Opus, plus static cover images like JPG, PNG, and WebP. Animated images are not accepted.

```sh
yaatv -a audio.flac -i cover.jpg -o upload.mp4
```

Give it audio. Give it artwork. Get an MP4 you can upload.

Website and docs: <https://yaatv.org>

## Download

Download the ZIP for your computer from the latest release:

<https://github.com/cavyion/yaatv/releases/latest>

Use one of these release assets:

- Windows x64: `yaatv-windows-x64.zip` containing `yaatv.exe`
- Linux x64: `yaatv-linux-x64.zip` containing `yaatv-linux`
- macOS x64: `yaatv-macos-x64.zip` containing `yaatv-macos`

You can ignore GitHub's "Source code (zip)" and "Source code (tar.gz)" files unless you specifically want the code.

Windows release ZIPs include `yaatv.exe` without FFmpeg. On Windows, run `.\yaatv.exe --install-ffmpeg` once to install FFmpeg and FFprobe into `%LOCALAPPDATA%\yaatv\bin`; yaatv checks that location before PATH and does not modify system PATH. Linux and macOS release ZIPs include FFmpeg and FFprobe.

## Run

On Windows, run the executable from PowerShell:

```powershell
.\yaatv.exe --version
.\yaatv.exe --install-ffmpeg
.\yaatv.exe -a audio.flac -i cover.jpg -o output.mp4
```

If FFmpeg is missing during an interactive Windows run, yaatv asks before installing it. In non-interactive runs, install FFmpeg first with `.\yaatv.exe --install-ffmpeg`.

On Linux:

```sh
chmod +x ./yaatv-linux
./yaatv-linux --version
./yaatv-linux -a episode.wav -i cover.jpg -o output.mp4
```

On macOS:

```sh
chmod +x ./yaatv-macos
./yaatv-macos --version
./yaatv-macos -a session.mp3 -i cover.jpg -o output.mp4
```

The macOS build is x64 and unsigned. Apple Silicon Macs may need Rosetta installed. If macOS blocks the file after download, allow it from System Settings, or remove the quarantine flag:

```sh
xattr -d com.apple.quarantine ./yaatv-macos
```

## Usage

The smallest command uses the audio file name, or artist/title tags when available, for the output MP4:

```sh
yaatv -a audio.flac -i cover.jpg
```

Choose the output file and resolution:

```sh
yaatv -a episode.wav -i cover.jpg -o output.mp4 --resolution 1440p
```

Flags:

- `-a`, `--audio`: audio file, required
- `-i`, `--image`: cover image, required
- `-o`, `--output`: output path, default is `[Artist] - [Title].mp4` when tags are available
- `--resolution`: `1080p`, `1440p`, or `4k`, default is `1080p`
- `--pad`: seconds of silence to add at the end, default is `0`, max is `10`
- `--no-warn`: hide low source quality warnings
- `--install-ffmpeg`: install FFmpeg and FFprobe into yaatv's app-managed Windows bin directory

## Output

yaatv creates a normal MP4 with your cover image shown for the full length of the audio.

- Audio is kept in an upload-friendly format. High-quality AAC can be copied directly when no padding is needed.
- Cover images keep their aspect ratio. yaatv adds black bars instead of stretching.
- Video is encoded for broad playback compatibility and YouTube uploads.
- Completed files are checked after encoding and summarized before yaatv exits.
- Existing output files require confirmation before overwrite.

Low-quality source audio and cover images smaller than the target resolution print warnings unless `--no-warn` is set.

`--pad` cannot be used with high-quality AAC copy mode because adding silence requires a re-encode.

## Python install (optional)

If you prefer to run yaatv as a Python CLI, install it from this repository:

```sh
python -m pip install "git+https://github.com/cavyion/yaatv.git"
yaatv --version
```

Python 3.10 or newer is required.

Python installs do not bundle FFmpeg. On Windows, `yaatv --install-ffmpeg` installs yaatv's app-managed copy. On other systems, install FFmpeg separately and make sure both commands work:

```sh
ffmpeg -version
ffprobe -version
```

Download FFmpeg from <https://ffmpeg.org/download.html>.

## Third-party binaries

The release ZIPs include `LICENSE`, `THIRD_PARTY_NOTICES.md`, and `FFMPEG_BUILD_INFO.txt`. The yaatv source code is MIT licensed; third-party runtime and media components keep their own licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for source and license links before redistributing release binaries.

## Development

Install the project with test/build tools:

```sh
python -m pip install -e ".[dev]"
```

Run the same checks used by CI:

```sh
yaatv --version
python -m yaatv --version
python -m pytest
python -m build
python -m twine check dist/*
```

## Publishing

Tagging a version that starts with `v` builds the Windows, Linux, and macOS assets, then attaches them to a GitHub release.

```sh
git tag v0.2.0
git push origin main --tags
```

The website is served from `docs/` with GitHub Pages and uses `docs/CNAME` for `yaatv.org`.
