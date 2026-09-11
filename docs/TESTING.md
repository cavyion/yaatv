# Testing Guide for yaatv

This guide explains the test organization, execution modes, and platform considerations for contributors.

## Test Organization

yaatv divides tests into two suites:

| Suite | Path | External Binaries Required | Typical Runtime |
| --- | --- | --- | --- |
| **Unit Tests** | `tests/test_cli.py` | None (pure Python, mocked subprocess) | ~1–2 seconds |
| **Integration Tests** | `tests/test_ffmpeg_integration.py` | Real `ffmpeg` and `ffprobe` | ~10–30 seconds |

### 1. Unit Tests (`tests/test_cli.py`)

Unit tests verify:
- CLI argument parsing, flags, and default values.
- Resolution, aspect ratio, and canvas dimension calculations.
- Silence padding duration and audio filter parameters.
- FFmpeg filtergraph construction (scaling, padding, background blur/color).
- Error handling when inputs are invalid, missing, or conflicting.
- Windows drag-and-drop argument handling.

These tests mock external calls to `subprocess.run` and `subprocess.Popen` where appropriate. They run quickly, deterministically, and offline without requiring FFmpeg installed on the system.

### 2. Integration Tests (`tests/test_ffmpeg_integration.py`)

Integration tests are marked with `@pytest.mark.integration`. They verify:
- End-to-end media transcoding with real FFmpeg and FFprobe.
- Actual MP4 and ProRes MOV creation.
- Audio and image tag extraction.
- Silence pad concatenation and post-encode verification.

Integration tests automatically skip if `ffmpeg` or `ffprobe` is not found on the system.

---

## Running Tests

### Run unit tests only (recommended for fast iteration)

```sh
python -m pytest tests/test_cli.py -k "not integration"
```

### Run a single test or test subset

Filter by function name or keyword:

```sh
# Run a specific test
python -m pytest tests/test_cli.py -k test_pad_option

# Run all aspect ratio tests
python -m pytest tests/test_cli.py -k aspect
```

### Run all tests including integration

Requires FFmpeg and FFprobe installed and available in your `PATH`:

```sh
python -m pytest
```

### Run the contributor check suite

```sh
# Fast check (linter, typecheck, unit tests)
python scripts/check.py --fast

# Full gate (includes security scan, packaging, twine check)
python scripts/check.py
```

---

## Platform-Specific Testing

- **Windows**:
  - Test path handling with backslashes and drive letters (`C:\...`).
  - Test paths containing spaces and unicode characters.
  - Verify that file handles are properly closed so Windows file-locking does not prevent file replacement.
- **Linux**:
  - Verify execution in headless/CI environments.
  - Check that no GUI or interactive prompt hangs in non-TTY environments.
- **macOS**:
  - Verify both Intel (x64) and Apple Silicon (arm64) runtime compatibility.
  - Verify ProRes `.mov` output container compatibility.

