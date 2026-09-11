# ![yaatv](docs/docs-assets/yaatv.svg)

![License](https://img.shields.io/github/license/yaatv/yaatv)
![Release](https://img.shields.io/github/v/release/yaatv/yaatv)
![Python](https://img.shields.io/badge/python-3.10+-blue)

yaatv turns audio and cover art into an optimized video for YouTube and other
upload sites.

```sh
yaatv audio.flac cover.jpg
```

Give it audio. Give it artwork. Get a video you can upload.

Website and docs: <https://yaatv.org>

## Download

Download the ZIP for your computer from the latest release:

<https://github.com/yaatv/yaatv/releases/latest>

Use one of these release assets:

- Windows x64:
  [`yaatv-windows-x64.zip`](https://github.com/yaatv/yaatv/releases/latest/download/yaatv-windows-x64.zip)
- Linux x64:
  [`yaatv-linux-x64.zip`](https://github.com/yaatv/yaatv/releases/latest/download/yaatv-linux-x64.zip)
- macOS x64:
  [`yaatv-macos-x64.zip`](https://github.com/yaatv/yaatv/releases/latest/download/yaatv-macos-x64.zip)
- macOS arm64:
  [`yaatv-macos-arm64.zip`](https://github.com/yaatv/yaatv/releases/latest/download/yaatv-macos-arm64.zip)

You can ignore GitHub's "Source code (zip)" and "Source code (tar.gz)" files
unless you specifically want the code.

## Drag and drop

On Windows, drag one audio file and one cover image onto `yaatv.exe` for a
default video next to the audio file.

Use PowerShell when you want to choose the output file, canvas, background, or
padding.

## Quick start

Open PowerShell or a terminal in the extracted folder, install the local media
tools once, check setup, then create a video.

Windows:

```powershell
.\yaatv.exe --install-ffmpeg
.\yaatv.exe --scry
.\yaatv.exe audio.flac cover.jpg
```

Linux:

```sh
chmod +x ./yaatv-linux
./yaatv-linux --install-ffmpeg
./yaatv-linux --scry
./yaatv-linux audio.flac cover.jpg
```

macOS:

```sh
chmod +x ./yaatv-macos
./yaatv-macos --install-ffmpeg
./yaatv-macos --scry
./yaatv-macos audio.flac cover.jpg
```

Use `yaatv-macos-arm64` instead when you download the Apple Silicon ZIP.

## Common uses

Choose an output file:

```sh
yaatv -a episode.wav -i cover.jpg -o output.mp4
```

Use embedded cover art:

```sh
yaatv -a track.flac
```

Open the output folder after a successful encode:

```sh
yaatv -a track.flac -i cover.jpg --open-folder
```

Create a square or vertical video:

```sh
yaatv -a track.flac -i cover.jpg --aspect square
yaatv -a short.wav -i cover.jpg --aspect 9:16
```

Use a larger canvas:

```sh
yaatv -a mix.flac -i cover.jpg --resolution 1440p
```

Choose a background:

```sh
yaatv -a track.flac -i cover.jpg --bg-color "#202020"
yaatv -a track.flac -i cover.jpg --bg-image background.jpg
yaatv -a track.flac -i cover.jpg --bg-blur
```

Create a solid-color video without cover art:

```sh
yaatv -a track.flac --bg-color "#202020"
```

Create a large MOV master:

```sh
yaatv -a track.flac -i cover.jpg -o master.mov
```

Add a short silence pad:

```sh
yaatv -a track.flac -i cover.jpg --pad 2
```

## What to expect

- yaatv keeps cover art from stretching.
- Audio with embedded cover art can be used without `-i`.
- Square album art works well for square videos.
- WAV, FLAC, and high-bitrate AAC are good source choices.
- JPG, PNG, and static WebP are good cover choices.
- Animated images are rejected.
- The planned output file appears before encoding starts.
- yaatv shows simple progress while encoding and verifying.
- Existing output files require confirmation before replacement.
- Warnings appear when source audio, image size, or file extensions may be
  less than ideal.

## Options

| Option | Purpose |
| --- | --- |
| Positional files | Use one audio file and one cover image. |
| `-a`, `--audio` | Choose the audio file. |
| `-i`, `--image` | Choose the cover image. |
| `-b`, `--bg-image` | Choose a background image. |
| `--bg-color` | Choose a background color. |
| `--bg-blur` | Use a blurred copy of the cover image as the background. |
| `-o`, `--output` | Choose an `.mp4` or `.mov` output file. A `.mov` path creates ProRes MOV output. |
| `--output-dir` | Choose the folder for the default output filename. |
| `--resolution` | Choose `1080p`, `1440p`, or `4k`. |
| `--aspect` | Choose `16:9`, `square`, or `9:16`. |
| `--pad` | Add 0 through 10 seconds of silence at the end. |
| `--no-warn` | Hide source quality warnings. |
| `--dry-run` | Print the command without creating a file. |
| `--verbose` | Show raw encoding output. |
| `--overwrite` | Replace an existing output file without asking first. |
| `--open-folder` | Open the output folder after a successful encode. |
| `--install-ffmpeg` | Install local media tools for yaatv. |
| `--scry` | Check yaatv, media tools, and output folder setup. |

## Troubleshooting

Run `yaatv --scry` when setup looks wrong.

If macOS blocks the downloaded executable, allow it from System Settings, or
remove the quarantine flag:

```sh
xattr -d com.apple.quarantine ./yaatv-macos
```

If an output file already exists, choose a different output path, confirm
replacement, or use `--overwrite` deliberately.

## Python install

Most people should use the release ZIPs. If you prefer a Python install:

```sh
python -m pip install "git+https://github.com/yaatv/yaatv.git"
yaatv --version
```

Python 3.10 or newer is required.

Python installs still need the local media tools. Run:

```sh
yaatv --install-ffmpeg
yaatv --scry
```

## License

yaatv is MIT licensed. Release ZIPs include `LICENSE`,
`THIRD_PARTY_LICENSES.txt`, and `FFMPEG_BUILD_INFO.txt`.

## Development

Install the project with test and build tools:

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
python -m piplicenses --packages mutagen pillow --with-urls
python -m pip_audit . --strict
python -m pytest
python -m build
python -m twine check dist/*
```

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup,
workflows, and guidelines.

- Look through [good first issues](https://github.com/yaatv/yaatv/labels/good%20first%20issue)
  to get started.
- Report bugs or suggest features on the [issue tracker](https://github.com/yaatv/yaatv/issues).
- See [CONTRIBUTORS.md](CONTRIBUTORS.md) for recognition of everyone who has helped
  build and test yaatv.
- Read our [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before participating.

## Publishing

Tagging a version that starts with `v` builds the Windows, Linux, macOS x64,
and macOS arm64 assets, then attaches them to a GitHub release.

```sh
git tag v0.6.3
git push origin main --tags
```

The website is served from `docs/` with GitHub Pages and uses `docs/CNAME` for
`yaatv.org`.
