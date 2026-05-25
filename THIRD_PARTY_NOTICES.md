# Third-party notices

The yaatv source code is licensed under the MIT License in `LICENSE`.

yaatv release ZIPs include a PyInstaller-built executable and third-party runtime/media components so users do not need a separate Python or FFmpeg install. Each release ZIP also includes this file, `LICENSE`, and `FFMPEG_BUILD_INFO.txt`.

## FFmpeg and FFprobe

The Windows and Linux release builds use GPL-enabled static FFmpeg and FFprobe builds from BtbN FFmpeg-Builds:

https://github.com/BtbN/FFmpeg-Builds

The macOS x64 release build uses static FFmpeg and FFprobe binaries from evermeet.cx:

https://evermeet.cx/ffmpeg/

FFmpeg is a separate project from yaatv. FFmpeg source code and license information are available from:

https://ffmpeg.org/
https://ffmpeg.org/legal.html
https://git.ffmpeg.org/ffmpeg.git

`FFMPEG_BUILD_INFO.txt` is generated during the release workflow and records the FFmpeg/FFprobe download source, archive checksum, version, and build configuration used for that ZIP.

## Python runtime and bundled packages

Release executables are built with PyInstaller:

https://pyinstaller.org/
https://pyinstaller.org/en/stable/license.html

The executable may include CPython runtime files:

https://www.python.org/
https://docs.python.org/3/license.html

The executable bundles yaatv's Python dependencies:

- mutagen, licensed GPL-2.0-or-later: https://github.com/quodlibet/mutagen
- Pillow, licensed under the MIT-CMU license: https://github.com/python-pillow/Pillow

Bundled components remain under their own licenses.
