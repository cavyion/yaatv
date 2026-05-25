# yaatv

yaatv is made for producers who need to turn finished audio and cover art into a YouTube-ready MP4 without opening a video editor.

```sh
yaatv -a track.flac -i cover.jpg -o output.mp4 --resolution 1440p
```

Give it a track, give it artwork, get a video file back.

## Requirements

yaatv uses FFmpeg and FFprobe for encoding and verification. They are not bundled, so install FFmpeg first and make sure both commands work:

```sh
ffmpeg -version
ffprobe -version
```

Download FFmpeg from <https://ffmpeg.org/download.html>.

## Download

Download the build for your system from the latest release:

<https://github.com/cavyion/yaatv/releases/latest>

Use the asset that matches your OS:

- Windows x64: `yaatv-windows-x64.exe`
- Linux x64: `yaatv-linux-x64`
- macOS x64: `yaatv-macos-x64`

GitHub also adds source snapshots to every release as "Source code (zip)" and "Source code (tar.gz)".

On Windows, run the executable from PowerShell:

```powershell
.\yaatv-windows-x64.exe --version
.\yaatv-windows-x64.exe -a track.flac -i cover.jpg -o output.mp4
```

On Linux:

```sh
chmod +x ./yaatv-linux-x64
./yaatv-linux-x64 --version
./yaatv-linux-x64 -a track.flac -i cover.jpg -o output.mp4
```

On macOS:

```sh
chmod +x ./yaatv-macos-x64
./yaatv-macos-x64 --version
./yaatv-macos-x64 -a track.flac -i cover.jpg -o output.mp4
```

The macOS build is x64 and unsigned. Apple Silicon Macs may need Rosetta installed. If macOS blocks the file after download, allow it from System Settings, or remove the quarantine flag:

```sh
xattr -d com.apple.quarantine ./yaatv-macos-x64
```

## Install from Python

If you prefer to run yaatv as a Python CLI, install it from this repository:

```sh
python -m pip install "git+https://github.com/cavyion/yaatv.git"
yaatv --version
```

Python 3.10 or newer is required.

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
