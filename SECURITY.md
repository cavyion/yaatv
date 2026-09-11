# Security Policy

## Supported Versions

Security updates are applied to the latest release of yaatv.

| Version | Supported |
| --- | --- |
| 0.6.x | Yes |
| < 0.6.0 | No |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

If you discover a security vulnerability in yaatv, report it privately to **cavyion**:

1. **GitHub Security Advisory (preferred)**: Open a private advisory draft at
   [https://github.com/yaatv/yaatv/security/advisories/new](https://github.com/yaatv/yaatv/security/advisories/new).
2. **Direct contact**: Reach out directly to **cavyion** on GitHub ([@cavyion](https://github.com/cavyion)).

Please include in your report:
- A description of the vulnerability and its potential impact.
- Steps to reproduce, including sample files or commands if applicable.
- Operating system and yaatv version.
- Any proposed remediation or patch.

cavyion will review the report, acknowledge receipt, and coordinate a fix and release before public disclosure.

## Security Considerations in yaatv

yaatv interacts with external binaries and user media files:

- **Subprocess Execution**: yaatv invokes `ffmpeg` and `ffprobe` using structured argument lists (`list[str]`), avoiding `shell=True` to prevent command injection.
- **Media Parsing**: yaatv inspects audio tags via `mutagen` and images via `Pillow`. Corrupted, malformed, or animated files are validated before encoding.
- **FFmpeg Installer**: The `--install-ffmpeg` command fetches binaries over HTTPS from verified upstream sources and extracts them into local app data directories.
- **Binary Releases**: Release executables are built through GitHub Actions with pinned dependencies and workflow actions. Checksums (`SHA256`) and provenance attestations are published with every release.

