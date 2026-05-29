# Third-party notices

The yaatv source code is licensed under the MIT License in `LICENSE`.

yaatv release ZIPs include a PyInstaller-built executable directory and third-party runtime components so users do not need a separate Python install. Release ZIPs do not bundle FFmpeg or FFprobe. Each release ZIP also includes this file, `LICENSE`, and `FFMPEG_BUILD_INFO.txt`.

## FFmpeg and FFprobe

When a user runs `yaatv --install-ffmpeg`, yaatv downloads pinned FFmpeg tools for that platform, verifies SHA256 checksums, and extracts only the `ffmpeg` and `ffprobe` executables into yaatv's app-managed bin directory.

Windows and Linux use GPL-enabled static builds from BtbN FFmpeg-Builds:

https://github.com/BtbN/FFmpeg-Builds

macOS x64 uses static FFmpeg and FFprobe binaries from evermeet.cx:

https://evermeet.cx/ffmpeg/

macOS arm64 uses static FFmpeg and FFprobe binaries from ffmpeg.martin-riedl.de:

https://ffmpeg.martin-riedl.de/

FFmpeg is a separate project from yaatv. FFmpeg source code and license information are available from:

https://ffmpeg.org/
https://ffmpeg.org/legal.html
https://git.ffmpeg.org/ffmpeg.git

`FFMPEG_BUILD_INFO.txt` is generated during the release workflow. It records the installer archive URL, checksum, extracted files, and app-managed install path for each release platform.

## Python runtime and bundled packages

Release executables are built with PyInstaller:

https://pyinstaller.org/
https://github.com/pyinstaller/pyinstaller/blob/develop/LICENSE.txt

PyInstaller is licensed under the GNU General Public License v2 with an exception for generated binaries.

The executable directory may include CPython runtime files:

https://www.python.org/
https://docs.python.org/3/license.html

The executable bundles yaatv's Python dependencies:

- mutagen, licensed GPL-2.0-or-later: https://github.com/quodlibet/mutagen
- Pillow, licensed under the MIT-CMU license: https://github.com/python-pillow/Pillow

Bundled components remain under their own licenses.
