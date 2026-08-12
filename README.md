<table border="0">
  <tr>
    <td>
      <!-- VERSION -->v1.0.0<br>
      <!-- DATE -->11-Aug-2026<br>
      macOS &nbsp;|&nbsp; Windows &nbsp;|&nbsp; Linux<br>
      <a href="https://landenlabs.com">Home</a>
    </td>
    <td>
      <a href="https://landenlabs.com">
        <img src="screens/landenlabs_400.webp" width="300" alt="LanDen Labs">
      </a>
    </td>
  </tr>
</table>

<img src="icon.png" width="72" align="left" alt="and-tool icon">

# and-tool (Android Tool)

A CLI wrapper around `adb` for common Android device workflows: inspect or uninstall
packages matching a name pattern, force-stop and clear an app's data, pull recent
crash traces from logcat, and base64 encode/decode stdin.

**By [LanDen Labs](https://github.com/landenlabs) (2026)**

---

## Features

- **Device selection.** Auto-selects the sole connected device, or prompts when
  multiple devices are attached.
- **Package matching.** Every action takes a case-insensitive substring pattern
  against the package name — no need to type the full package id.
- **Actions:**
  - `--info` — detailed `dumpsys` info per matching package: installer, signatures,
    version, flags, and install file path. Add `--verbose` for a fuller breakdown
    (exported activities, per-user runtime permissions, USER_SET/USER_FIXED denials,
    and a trailing active-activities scan) — the same depth as `adb-info.csh`. If
    `pattern` is instead a path to a local `.apk` file, `--info` reads it directly
    with `aapt dump badging` (package/version/permissions/other) — the same as
    `apk-info.csh` — and doesn't need a device attached.
  - `--packages` — short version + last-update summary per matching package.
  - `--uninstall` — force-stop, clear data, then uninstall matching packages.
  - `--stop-and-clear` — force-stop and clear data for matching packages, then open
    each one's app-details settings screen.
  - `--clear` — clear the device's logcat buffer.
  - `--trace` — show `FATAL EXCEPTION` crash traces from logcat, falling back to
    generic `java` error lines when there are none.
  - `--encode` / `--decode` — base64 encode/decode stdin, one line at a time.
- **Standard CLI.** `--version`, `--help` with usage examples. Action flags accept
  any unambiguous prefix (e.g. `--info`, `--inf`, `--i`).

---

## Requirements

- Python 3.9 or later
- No third-party packages — uses only the standard library
- `adb` must be on `PATH` and a device attached (not required for `--encode`/`--decode`,
  or for `--info` on a local `.apk` file)
- `aapt` must be on `PATH`, or under an Android SDK's `build-tools/` (checked via
  `$ANDROID_HOME`, `$ANDROID_SDK_ROOT`, or `~/Library/Android/sdk`) — only needed for
  `--info` on a local `.apk` file

---

## Installation

### Run from source

```bash
git clone https://github.com/landenlabs/and-tool.git
cd and-tool
python and-tool.py --help
```

### Build a standalone binary

**macOS / Linux**

```bash
pyinstaller --onefile --name and-tool and-tool.py
```

**Windows**

```powershell
pyinstaller --onefile --name and-tool and-tool.py
```

Both commands use [PyInstaller](https://pyinstaller.org) to produce a self-contained executable.

Pushing a `v*` tag (e.g. `v1.0.0`) triggers `.github/workflows/build.yml`, which builds
macOS and Windows binaries and publishes them to a GitHub Release automatically.

---

## Usage

```bash
and-tool.py --info 'com.google.*'
and-tool.py --verbose --info drop.red
and-tool.py --info build/debug/debug.apk
and-tool.py --stop-and-clear 'atak'
and-tool.py --uninstall 'wx2'
and-tool.py --trace
and-tool.py --clear
and-tool.py --encode < input.txt
```

The `pattern` argument defaults to `""`, matching every installed package, for any
action that needs one.

### All action flags

| Flag | Purpose |
| ---- | ------- |
| `--info` | Detailed `dumpsys` info per matching package, or `aapt` info for a local `.apk` path |
| `--verbose` / `-v` | With `--info` on a device package, show activities/permissions/flags |
| `--packages` | Short version + last-update summary per matching package |
| `--uninstall` | Force-stop, clear data, and uninstall matching packages |
| `--stop-and-clear` | Force-stop and clear data, then open app-details settings |
| `--clear` | Clear the device's logcat buffer |
| `--trace` | Show recent crash traces from logcat |
| `--encode` | Base64 encode stdin, one line at a time |
| `--decode` | Base64 decode stdin, one line at a time |
| `--version` | Print version and exit |
| `--help` | Show full usage and examples |

---

## Project structure

```
and-tool/
├── and-tool.py                  # Main script (single-file CLI; hardcodes VERSION = "vX.Y.Z")
├── VERSION                      # Bare X.Y.Z, mirrors and-tool.py's VERSION literal
├── set-version.bash             # Bump version, commit, tag, push (macOS/Linux)
├── set-version.ps1              # Bump version, commit, tag, push (Windows)
├── icon.png                     # App icon: baked into the release binaries, shown in README
├── icon.icns                    # App icon baked into the macOS release build
├── icon.ico                     # App icon baked into the Windows release build
├── make-icons.py                # Regenerate icon.icns/icon.ico from icon.png
├── requirements.txt
├── README.md
├── LICENSE
├── screens/                     # Images used in this README
└── .github/workflows/build.yml  # Tag-triggered build + GitHub Release
```

---

## Releasing

Versions are bumped with `set-version.bash` (or `set-version.ps1` on Windows), run from
the repo root:

```bash
./set-version.bash -version 1.0.1 -message "Fix trace fallback edge case"
```

This updates `VERSION`, the `VERSION` literal hardcoded in `and-tool.py`, and the
version/date markers at the top of this README that feed the release build above.

---

## License

Apache 2.0 © [LanDen Labs](https://github.com/landenlabs) 2026
