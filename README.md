# yaatv

yaatv turns an audio file and a cover image into a YouTube-ready MP4.

```sh
yaatv -a track.flac -i cover.jpg -o output.mp4 --resolution 1440p
```

It uses FFmpeg for the actual encode. FFmpeg is not bundled, so install it separately and make sure this works:

```sh
ffmpeg -version
ffprobe -version
```
https://ffmpeg.org/download.html

## Install

Download the binary for your platform from GitHub Releases:

- Windows: `yaatv-windows-x64.exe`
- macOS: `yaatv-macos-x64`
- Linux: `yaatv-linux-x64`

On macOS and Linux, make the binary executable first:

```sh
chmod +x yaatv-macos-x64
```

Run the downloaded file directly from your terminal.

## Usage

```sh
yaatv -a track.flac -i cover.jpg
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

## Release

Before tagging, run the same checks used by CI:

```sh
python -m pip install -e ".[dev]"
yaatv --version
python -m yaatv --version
python -m pytest
python -m build
python -m twine check dist/*
```

```sh
git tag v0.1.0
git push origin main --tags
```
