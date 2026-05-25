# yaatv

yaatv is made for producers who need to turn finished audio and cover art into a YouTube-ready MP4 without opening a video editor.

```sh
yaatv -a track.flac -i cover.jpg -o output.mp4 --resolution 1440p
```

Give it a track, give it artwork, get a video file back.

Website and docs: <https://yaatv.org>

## Download

Download the build for your system from the latest release:

<https://github.com/cavyion/yaatv/releases/latest>

Release ZIPs bundle FFmpeg and FFprobe. Download one ZIP for your OS, extract it, and run the executable inside; no separate FFmpeg install is needed.

Use the asset that matches your OS:

- Windows x64: `yaatv-windows-x64.zip` containing `yaatv.exe`
- Linux x64: `yaatv-linux-x64.zip` containing `yaatv-linux`
- macOS x64: `yaatv-macos-x64.zip` containing `yaatv-macos`

GitHub also adds source snapshots to every release as "Source code (zip)" and "Source code (tar.gz)".

On Windows, run the executable from PowerShell:

```powershell
.\yaatv.exe --version
.\yaatv.exe -a track.flac -i cover.jpg -o output.mp4
```

On Linux:

```sh
chmod +x ./yaatv-linux
./yaatv-linux --version
./yaatv-linux -a track.flac -i cover.jpg -o output.mp4
```

On macOS:

```sh
chmod +x ./yaatv-macos
./yaatv-macos --version
./yaatv-macos -a track.flac -i cover.jpg -o output.mp4
```

The macOS build is x64 and unsigned. Apple Silicon Macs may need Rosetta installed. If macOS blocks the file after download, allow it from System Settings, or remove the quarantine flag:

```sh
xattr -d com.apple.quarantine ./yaatv-macos
```

## Install from Python

If you prefer to run yaatv as a Python CLI, install it from this repository:

```sh
python -m pip install "git+https://github.com/cavyion/yaatv.git"
yaatv --version
```

Python 3.10 or newer is required.

Python installs do not bundle FFmpeg. Install FFmpeg separately and make sure both commands work:

```sh
ffmpeg -version
ffprobe -version
```

Download FFmpeg from <https://ffmpeg.org/download.html>.

## Usage

The smallest command uses the source file name, or artist/title tags when available, for the output MP4:

```sh
yaatv -a track.flac -i cover.jpg
```

Choose an output path and resolution:

```sh
yaatv -a track.flac -i cover.jpg -o output.mp4 --resolution 1440p
```

Flags:

- `-a`, `--audio`: audio file, required
- `-i`, `--image`: cover image, required
- `-o`, `--output`: output path, default is `[Artist] - [Title].mp4` when tags are available
- `--resolution`: `1080p`, `1440p`, or `4k`, default is `1080p`
- `--pad`: seconds of silence to add at the end, default is `0`, max is `10`
- `--no-warn`: hide low source quality warnings

## Output

- AAC at 48kHz and 320kbps or higher is copied without re-encoding.
- Other audio is encoded as AAC-LC 384kbps at 48kHz.
- Cover images keep their aspect ratio. yaatv adds black bars instead of stretching.
- Video uses 1fps H.264, CRF 16, BT.709 color metadata, yuv420p, and `+faststart`.
- Completed files are verified with FFprobe and summarized after encoding.
- Existing output files require confirmation before overwrite.

Source audio below 256kbps and cover images smaller than the target resolution print warnings unless `--no-warn` is set.

`--pad` cannot be used with high-quality AAC copy mode because adding silence requires a re-encode.

## Third-party binaries

The release ZIPs include `LICENSE`, `THIRD_PARTY_NOTICES.md`, and `FFMPEG_BUILD_INFO.txt`. The yaatv source code is MIT licensed; bundled runtime and media components keep their own licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for source and license links before redistributing release binaries.

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
git tag v0.1.0
git push origin main --tags
```

The website is served from `docs/` with GitHub Pages and uses `docs/CNAME` for `yaatv.org`.
