#!/usr/bin/env python3
# ----------------------------------------------------------------------
# Copyright (c) 2026 LanDen Labs - Dennis Lang
# https://landenlabs.com
# ----------------------------------------------------------------------
"""Unified ADB tool: device/package management, crash traces, base64 helpers.

Merges adb-tool.py and adb-legacy.py, dropping their overlapping
"list installed packages" functionality in favor of the single info()/
packages() implementation.
"""

import argparse
import base64
import glob
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta

VERSION = "v6.08.11"

RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[00m"


def colored(msg, color):
    return f"{color}{msg}{RESET}"


class ADBTool:
    def __init__(self):
        self.adb_cmd = ["adb"]
        self._setup_device()

    @staticmethod
    def _echo_cmd(cmd):
        """Prints the actual command line about to run (shell-quoted so it can be copy/pasted)."""
        print(colored("$ " + " ".join(shlex.quote(c) for c in cmd), CYAN))

    def _run_cmd(self, args, capture=True, echo=False):
        """Helper to run shell commands. echo=True prints the full adb command first."""
        full_cmd = self.adb_cmd + args
        if echo:
            self._echo_cmd(full_cmd)
        try:
            result = subprocess.run(full_cmd, capture_output=capture, text=True)
            return result.stdout.strip() if capture else None
        except Exception as e:
            print(f"Error running {' '.join(full_cmd)}: {e}")
            return ""

    def _setup_device(self):
        """Identifies connected devices and handles selection if multiple exist."""
        output = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
        devices = [line.split('\t')[0] for line in output.strip().split('\n')[1:] if 'device' in line or 'unauthorized' in line]

        if not devices:
            print(colored("No devices found.", RED))
            sys.exit(1)
        elif len(devices) > 1:
            print("--- Multiple devices ---")
            for i, dev in enumerate(devices, 1):
                print(f"  {i}  {dev}")
            try:
                choice = int(input(f"Enter device # (1..{len(devices)}): "))
                self.adb_cmd = ["adb", "-s", devices[choice - 1]]
            except (ValueError, IndexError):
                print(colored("Invalid selection.", RED))
                sys.exit(1)
        else:
            self.adb_cmd = ["adb", "-s", devices[0]]

        model = self._run_cmd(["shell", "getprop", "ro.product.model"])
        print(f"Adb set to: {' '.join(self.adb_cmd)} ({model})\n")

    def _get_packages(self, partial_name):
        """Retrieves packages whose full name contains partial_name (case-insensitive substring)."""
        cmd1 = self._run_cmd(["shell", "pm", "list", "package"]).splitlines()
        cmd2 = self._run_cmd(["shell", "pm", "list", "package", "-a"]).splitlines()
        all_pkgs = sorted(set(cmd1 + cmd2))

        needle = partial_name.lower()
        return [p.replace("package:", "") for p in all_pkgs if needle in p.lower()]

    def info(self, pattern, verbose=False):
        """Detailed dumpsys info (installer, signatures, version, flags, file path).

        With verbose=True, adds the fuller breakdown from adb-info.csh: exported
        activities, version/last-install lines, flags, per-user runtime
        permissions, and USER_SET/USER_FIXED permission denials.
        """
        for pkg in self._get_packages(pattern):
            print(colored(f"---- {pkg} ----", CYAN))
            dumpsys = self._run_cmd(["shell", "dumpsys", "package", pkg]).splitlines()
            for line in dumpsys:
                if any(key in line for key in ("installerPackage", "signatures", " Package ", "versionName", "pkgFlags")):
                    print(f"  {line.strip()}")

            if verbose:
                self._info_verbose(dumpsys)

            path = self._run_cmd(["shell", "pm", "path", pkg]).replace("package:", "")
            if path:
                print(f"  File info: {self._run_cmd(['shell', 'stat', path])}")
            print()

        if verbose:
            self._active_activities(pattern)

    def _info_verbose(self, dumpsys):
        """Prints the adb-info.csh-style sections for one package's dumpsys output."""
        print(colored("  --Exported Activities--", CYAN))
        for line in dumpsys:
            if "Activity" in line:
                print(f"    {line.strip()}")

        print(colored("  --Versions--", CYAN))
        for line in dumpsys:
            if re.search(r"version[NC]|Hidden ", line):
                print(f"    {line.strip()}")

        print(colored("  --Last Install--", CYAN))
        for line in dumpsys:
            if "last" in line or "Hidden " in line:
                print(f"    {line.strip()}")

        print(colored("  --Flags--", CYAN))
        for line in dumpsys:
            if "flags" in line.lower():
                print(f"    {line.strip()}")

        print(colored("  --User Permissions--", CYAN))
        self._print_user_permissions(dumpsys)

        print(colored("  --User Permissions denied (USER_SET/USER_FIXED)--", CYAN))
        for line in dumpsys:
            if "USER_SET" in line or "USER_FIXED" in line:
                print(f"    {line.strip()}")

    def _print_user_permissions(self, dumpsys):
        """Per-user runtime permission grants, skipping users the package isn't installed for."""
        user = None
        in_runtime = False
        for line in dumpsys:
            match = re.search(r"User (\d+):", line)
            if match:
                if "true" in line:
                    print(f"    {line.strip()}")
                user = match.group(1) if "installed=true" in line else None
                in_runtime = False
                continue
            if "runtime permissions" in line:
                in_runtime = True
                continue
            parts = line.split()
            if in_runtime and user is not None and parts and parts[0].startswith("android.permission"):
                granted = parts[1] if len(parts) > 1 else ""
                print(f"    user {int(user):2d}, {granted:>15} {parts[0]}")

    def _active_activities(self, pattern):
        """Recent activity stack entries mentioning pattern (mirrors the trailing
        'Active activities' section of adb-info.csh: dumpsys activity activities,
        grep -B20 mActivityComponent, filtered to task/intent/component lines)."""
        lines = self._run_cmd(["shell", "dumpsys", "activity", "activities"]).splitlines()
        match_idxs = [i for i, line in enumerate(lines) if "mActivityComponent" in line]

        seen = set()
        context_lines = []
        for idx in match_idxs:
            for j in range(max(0, idx - 20), idx + 1):
                if j not in seen:
                    seen.add(j)
                    context_lines.append(lines[j])

        result = [
            line for line in context_lines
            if any(key in line for key in ("mActivityComponent", "Intent ", "* Task")) and pattern in line
        ]
        if result:
            print(colored("===  Active activities  ===", CYAN))
            for line in result:
                print(line.strip())

    def packages(self, pattern):
        """Short version + last-update summary per matching package."""
        for pkg in self._get_packages(pattern):
            dumpsys = self._run_cmd(["shell", "dumpsys", "package", pkg]).splitlines()
            version = next((line for line in dumpsys if "versionName" in line), "Unknown")
            last_update = next((line for line in dumpsys if "last" in line), "Unknown")
            print(f"{pkg}\n  {version.strip()}\n  {last_update.strip()}\n")

    def install(self, target):
        """Installs an apk, returning the installed package name on success, or
        None if resolution failed, the user declined, or the install ultimately failed.
        If the installed apk is byte-for-byte identical to the on-disk apk, skips the
        install entirely."""
        apk = _resolve_apk_to_install(target)
        if not apk:
            return None

        pkg_name = _get_apk_package_name(apk)
        installed = bool(pkg_name and pkg_name in self._get_packages(pkg_name))
        identical = sig_mismatch = False
        if installed:
            identical, sig_mismatch = self._compare_installed_apk(pkg_name, apk)

        if installed:
            print(colored(f"Existing install of {pkg_name}:", CYAN))
            self.info(pkg_name, verbose=False)
            if identical:
                print(colored(f"Installed {pkg_name} is identical to {apk} - skipping install.", CYAN))
                return pkg_name
            if sig_mismatch:
                print(colored(f"Signing certificate differs from the installed {pkg_name} - a plain install would fail.", RED))
            answer = input(colored(f"Install {apk} over existing {pkg_name}? [y/N]: ", YELLOW)).strip().lower()
            if answer != "y":
                print("Skipping install.")
                return None

        print(f"Installing {apk} ...")
        output = self._install_apk(apk)
        if "Success" not in output and pkg_name:
            answer = input(colored(f"Install failed. Uninstall existing {pkg_name} and retry install? [y/N]: ", YELLOW)).strip().lower()
            if answer == "y":
                self._uninstall_pkg(pkg_name)
                print(f"Retrying install of {apk} ...")
                output = self._install_apk(apk)

                if "Success" not in output:
                    answer = input(colored(f"Install still failed. Clear any remaining data for {pkg_name} and retry install? [y/N]: ", YELLOW)).strip().lower()
                    if answer == "y":
                        self._uninstall_pkg(pkg_name, clear_data=True)
                        print(f"Retrying install of {apk} ...")
                        output = self._install_apk(apk)

        return pkg_name if "Success" in output else None

    def start(self, pattern):
        """Launches an installed app matching pattern (case-insensitive substring
        on package name); prompts to choose when multiple packages match."""
        matches = self._get_packages(pattern)
        if not matches:
            print(colored(f"No installed package matches '{pattern}'.", RED))
            return
        pkg = matches[0] if len(matches) == 1 else _prompt_pkg_choice(matches)
        if pkg:
            self.launch(pkg)

    def launch(self, pkg):
        """Launches pkg's default activity via the monkey trick, avoiding the need
        to know its launch activity's class name."""
        print(f"Starting {pkg} ...")
        self._run_cmd(["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], capture=False, echo=True)

    def wifi_adb(self, enable):
        """Toggles adb-over-wifi on the connected device. Enabling switches adbd
        to tcpip mode and connects to the device's wlan0 IP on port 5555;
        disabling switches the device back to USB mode."""
        if enable:
            ip_out = self._run_cmd(["shell", "ip", "-f", "inet", "addr", "show", "wlan0"])
            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ip_out)
            if not match:
                print(colored("Could not determine device wifi IP (is wlan0 connected?).", RED))
                return
            addr = match.group(1)
            print(f"Switching {' '.join(self.adb_cmd)} to tcpip mode on port 5555...")
            self._run_cmd(["tcpip", "5555"], capture=False, echo=True)
            time.sleep(1)
            print(f"Connecting to {addr}:5555 ...")
            connect_cmd = ["adb", "connect", f"{addr}:5555"]
            self._echo_cmd(connect_cmd)
            subprocess.run(connect_cmd)
        else:
            print(f"Switching {' '.join(self.adb_cmd)} back to USB mode...")
            self._run_cmd(["usb"], capture=False, echo=True)

    def _install_apk(self, apk):
        """Runs adb install, printing and returning its combined stdout+stderr."""
        cmd = self.adb_cmd + ["install", "-r", "-t", "-d", "-i", "com.android.vending", apk]
        self._echo_cmd(cmd)
        result = subprocess.run(cmd, capture_output=True, text=True)
        output = (result.stdout + result.stderr).strip()
        print(output)
        return output

    def _compare_installed_apk(self, pkg_name, apk):
        """Pulls the installed apk for pkg_name and compares it against the candidate
        apk. Returns (identical, sig_mismatch): identical is True if the two apks are
        byte-for-byte the same, in which case sig_mismatch is always False since a
        same-apk install can't fail on signing; sig_mismatch is True if their signing
        cert(s) differ (a plain install would fail). Both default to False (best-effort)
        if the installed apk can't be pulled or the relevant tool is unavailable,
        leaving the normal install-then-retry-on-failure flow in install() as the
        fallback."""
        installed_apk = self._pull_installed_apk(pkg_name)
        if not installed_apk:
            return False, False
        try:
            if _files_identical(installed_apk, apk):
                return True, False
            old_sig = _get_apk_signature(installed_apk)
            new_sig = _get_apk_signature(apk)
        finally:
            os.remove(installed_apk)

        sig_mismatch = old_sig is not None and new_sig is not None and old_sig != new_sig
        return False, sig_mismatch

    def _pull_installed_apk(self, pkg_name):
        """Pulls the installed base apk for pkg_name to a temp file; returns its
        local path, or None if the package has no path or the pull fails."""
        paths = [line.replace("package:", "", 1) for line in self._run_cmd(["shell", "pm", "path", pkg_name]).splitlines() if line.startswith("package:")]
        if not paths:
            return None
        device_path = next((p for p in paths if os.path.basename(p) == "base.apk"), paths[0])

        fd, local_path = tempfile.mkstemp(suffix=".apk")
        os.close(fd)
        cmd = self.adb_cmd + ["pull", device_path, local_path]
        self._echo_cmd(cmd)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or os.path.getsize(local_path) == 0:
            os.remove(local_path)
            return None
        return local_path

    def _backup_enabled(self):
        """Best-effort check of the device's Backup Manager state via 'bmgr enabled'.
        Returns True/False, or None if the state can't be determined (e.g. bmgr
        unavailable), in which case callers should skip warning rather than guess."""
        output = self._run_cmd(["shell", "bmgr", "enabled"]).lower()
        if "disabled" in output:
            return False
        if "enabled" in output:
            return True
        return None

    def _uninstall_pkg(self, pkg, clear_data=False):
        print(f"Stopping {pkg}...")
        self._run_cmd(["shell", "am", "force-stop", pkg], capture=False, echo=True)
        if self._backup_enabled() is False:
            print(colored(f"Warning: device backup is disabled - {pkg}'s data will not be recoverable after this uninstall.", YELLOW))
        print(f"Uninstalling {pkg}...")
        if clear_data:
            self._run_cmd(["shell", "pm", "uninstall", pkg], capture=False, echo=True)
        else:
            self._run_cmd(["shell", "pm", "uninstall", "-k", pkg], capture=False, echo=True)

    def uninstall(self, pattern):
        for pkg in self._get_packages(pattern):
            answer = input(colored(f"Uninstall {pkg}? [y/N]: ", YELLOW)).strip().lower()
            if answer != "y":
                print(f"Skipping {pkg}")
                continue
            self._uninstall_pkg(pkg)

    def stop_and_clear(self, pattern):
        for pkg in self._get_packages(pattern):
            print(f"== Processing {pkg}")
            self._run_cmd(["shell", "am", "force-stop", pkg], capture=False, echo=True)
            self._run_cmd(["shell", "pm", "clear", "--user", "0", pkg], capture=False, echo=True)
            self._run_cmd(["shell", "am", "start", "-a", "android.settings.APPLICATION_DETAILS_SETTINGS", "-d", f"package:{pkg}"], capture=False, echo=True)

    def clear_logs(self):
        print("Clearing logcat...")
        self._run_cmd(["logcat", "-c"], capture=False, echo=True)

    def _get_pids(self, partial_name):
        """Retrieves (pid, name) pairs from `ps -A` whose process name contains
        partial_name (case-insensitive substring); a process name is usually the
        package name, but can be suffixed (e.g. 'pkg:service') for a named process."""
        lines = self._run_cmd(["shell", "ps", "-A"]).splitlines()
        needle = partial_name.lower()
        matches = []
        for line in lines[1:]:  # skip header
            parts = line.split()
            if len(parts) < 2:
                continue
            pid, name = parts[1], parts[-1]
            if needle in name.lower():
                matches.append((pid, name))
        return matches

    def log_pid(self, pattern):
        """Finds running process(es) matching pattern (case-insensitive substring
        of process name via `ps -A`), prompting to choose when more than one
        matches, then dumps that process's log via `adb logcat --pid=<pid> -d`."""
        matches = self._get_pids(pattern)
        if not matches:
            print(colored(f"No running process matches '{pattern}'.", RED))
            return

        if len(matches) == 1:
            pid, name = matches[0]
        else:
            print(colored("--- Multiple processes found ---", YELLOW))
            for i, (pid, name) in enumerate(matches, 1):
                print(f"  {i}  {name} (pid {pid})")
            choice = input(f"Enter # (1..{len(matches)}): ").strip()
            try:
                idx = int(choice)
                if not 1 <= idx <= len(matches):
                    raise ValueError
            except ValueError:
                print(colored("Invalid selection.", RED))
                return
            pid, name = matches[idx - 1]

        print(colored(f"---- Log for {name} (pid {pid}) ----", CYAN))
        for line in self._run_cmd(["logcat", f"--pid={pid}", "-d"], echo=True).splitlines():
            print(_colorize_logcat_line(line))

    @staticmethod
    def _parse_trace_filters(filters):
        """Split raw --trace values into (include, exclude) substring lists; a leading '!' marks exclude."""
        include, exclude = [], []
        for raw in filters or []:
            raw = raw.strip()
            if not raw:
                continue
            if raw.startswith("!"):
                exclude.append(raw[1:])
            else:
                include.append(raw)
        return include, exclude

    def trace(self, filters=None):
        """Show FATAL EXCEPTION crash traces, deduped to the most recent occurrence of each, falling back to generic java errors."""
        print(colored("---- Show Traces ----", YELLOW))
        lines = self._run_cmd(["logcat", "-d"]).splitlines()
        include, exclude = self._parse_trace_filters(filters)

        traces = {}  # dedup signature -> {"header": str, "body": list, "count": int, "order": int}
        order = []
        show = False
        proc_id = None
        header = None
        body = []

        def signature(body_lines):
            # PID varies run to run even for the identical crash, so ignore it when deduping.
            return tuple(re.sub(r"PID:\s*\d+", "PID:<pid>", line) for line in body_lines)

        def flush():
            if header is None:
                return
            key = signature(body)
            entry = traces.get(key)
            if entry is None:
                entry = {"count": 0, "order": len(order)}
                traces[key] = entry
                order.append(key)
            entry["header"] = header
            entry["body"] = body
            entry["count"] += 1

        for line in lines:
            line = line.strip()
            if "FATAL EXCEPTION" in line:
                flush()
                show = True
                parts = [p for p in line.split("FATAL")[0].split("E") if p]
                proc_id = parts[-1] if parts else "_none_"
                header = line
                body = []
            elif show and proc_id and proc_id in line:
                parts = [p for p in line.split(proc_id) if p]
                if len(parts) > 1:
                    body.append(parts[1])
            elif show:
                flush()
                show = False
                proc_id = None
                header = None
                body = []
        flush()

        def matches(entry):
            haystack = entry["header"] + "\n" + "\n".join(entry["body"])
            if include and not any(p in haystack for p in include):
                return False
            if exclude and any(p in haystack for p in exclude):
                return False
            return True

        shown = 0
        for key in order:
            entry = traces[key]
            if not matches(entry):
                continue
            shown += 1
            if entry["count"] > 1:
                print(colored(f"---- {entry['count']}x duplicate, showing most recent ----", YELLOW))
            print(entry["header"])
            for body_line in entry["body"]:
                print(body_line)
            print(colored("---- End Trace ----\n", YELLOW))

        if not traces:
            print(colored("---- Show Error logs with 'java' ----", YELLOW))
            for line in self._run_cmd(["logcat", "-d", "*:E"]).splitlines():
                if "java" in line:
                    print(line)
        elif shown == 0:
            print(colored("---- No traces matched the given --trace filter ----", YELLOW))


# ---------------------------------------------------------------------------
# --log-time: reformat a saved `adb logcat -v threadtime` file for readability
# when diagnosing startup/UI-responsiveness delays
# ---------------------------------------------------------------------------

# Full `-v threadtime` line: 'MM-DD HH:MM:SS.mmm PID TID LEVEL TAG: msg'.
_LOGCAT_LINE_RE = re.compile(
    r'^(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+(\d+)\s+([A-Z])\s+([^:]*):\s*(.*)$'
)
# Colorized wrappers (e.g. pidcat) often drop the date and PID, leaving just
# 'HH:MM:SS.mmm TID LEVEL TAG: msg'.
_LOGCAT_LINE_NO_PID_RE = re.compile(
    r'^(\d{2}:\d{2}:\d{2}\.\d{3})\s+(\d+)\s+([A-Z])\s+([^:]*):\s*(.*)$'
)
# ALog's own tagStr() prefixes every message with 'Tmain#' or 'T<javaThreadId>#',
# which lets us recover the thread name when there's no PID column to compare against.
_TMAIN_MSG_RE = re.compile(r'^Tmain#')
_TID_MSG_RE = re.compile(r'^T(\d+)#')

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')

_LOG_LEVEL_COLORS = {"F": RED, "E": RED, "W": YELLOW, "I": CYAN}

# Default (`-v brief`) logcat format: 'LEVEL/TAG(PID): msg', e.g. 'I/ActivityManager( 1234): ...'.
_LOGCAT_BRIEF_LEVEL_RE = re.compile(r'^([A-Z])/')


def _strip_ansi(line):
    """Removes ANSI escape sequences (e.g. SGR color codes) so a pre-colored
    log file still matches the plain logcat format."""
    return _ANSI_ESCAPE_RE.sub("", line)


def _colorize_logcat_line(line):
    """Colors a raw (`-v brief` format) logcat line by its level, same palette
    as --log-time (F/E red, W yellow, I cyan); lines with no recognized level
    prefix (D/V, or a wrapped stack-trace continuation line) print unchanged."""
    match = _LOGCAT_BRIEF_LEVEL_RE.match(line)
    if match and match.group(1) in _LOG_LEVEL_COLORS:
        return colored(line, _LOG_LEVEL_COLORS[match.group(1)])
    return line


def _msg_thread_hint(msg):
    """Recovers a thread label from ALog's embedded 'Tmain#'/'T<id>#' message
    prefix, or None if the message has no such prefix."""
    if _TMAIN_MSG_RE.match(msg):
        return "main"
    match = _TID_MSG_RE.match(msg)
    return match.group(1) if match else None


def _parse_logcat_line(line):
    """Parses one logcat line into a dict, or None if the line doesn't match
    either the full `-v threadtime` format or the date/PID-less format some
    colorized wrappers (e.g. pidcat) produce (e.g. logcat's own
    '--------- beginning of main' markers match neither)."""
    match = _LOGCAT_LINE_RE.match(line)
    if match:
        date_str, pid, tid, level, tag, msg = match.groups()
        try:
            # logcat's threadtime format omits the year; anchor to a fixed one and
            # correct for a Dec->Jan rollover afterwards (see log_time).
            ts = datetime.strptime(date_str + " 2001", "%m-%d %H:%M:%S.%f %Y")
        except ValueError:
            return None
        thread = "main" if tid == pid else tid
        return {"ts": ts, "thread": thread, "level": level, "tag": tag.strip(), "msg": msg}

    match = _LOGCAT_LINE_NO_PID_RE.match(line)
    if match:
        time_str, tid, level, tag, msg = match.groups()
        try:
            ts = datetime.strptime(time_str, "%H:%M:%S.%f")
        except ValueError:
            return None
        # Thread not yet resolved: this tid's name (if any line for it carries
        # ALog's 'Tmain#'/'T<id>#' hint) is filled in across the whole file by
        # log_time, so every line for a given tid gets the same label even if
        # this particular message happens not to carry the hint itself.
        return {"ts": ts, "tid": tid, "hint": _msg_thread_hint(msg), "level": level, "tag": tag.strip(), "msg": msg}

    return None


def _normalize_log_msg(msg):
    """Blanks out numbers/addresses so otherwise-identical repeated log lines
    (differing only by a counter, size, or object hash) compare equal."""
    return re.sub(r'0x[0-9a-fA-F]+|@[0-9a-fA-F]{4,}|\d+', '#', msg)


def _format_log_row(rec, base_ts, elapsed_width, thread_width):
    elapsed = (rec["ts"] - base_ts).total_seconds()
    line = f"+{elapsed:{elapsed_width}.3f} {rec['thread']:>{thread_width}} {rec['level']} {rec['tag']}: {rec['msg']}"
    return colored(line, _LOG_LEVEL_COLORS[rec["level"]]) if rec["level"] in _LOG_LEVEL_COLORS else line


def log_time(path, gap_seconds=1.0, collapse_min=5):
    """Reformats a saved adb logcat file (full `-v threadtime`, or the date/PID-
    less variant some colorized wrappers like pidcat produce) to make startup
    delays easier to spot: the thread column becomes 'main' for the main
    thread (via PID==TID, or -- when there's no PID column -- via ALog's own
    embedded 'Tmain#'/'T<id>#' message prefix), the timestamp column becomes
    an offset from the first line, a marker is inserted at any gap >=
    gap_seconds, and runs of >= collapse_min consecutive lines that are
    structurally identical (same thread/level/tag, message differing only by
    numbers/addresses) are collapsed to their first and last entry. Any ANSI
    color escape codes already in the file (e.g. from a pre-colored log) are
    stripped before parsing."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8", errors="replace") as f:
            lines = [_strip_ansi(line) for line in f.readlines()]
    except OSError as e:
        print(colored(f"Error reading {path}: {e}", RED))
        return

    records = [_parse_logcat_line(line) or {"raw": line.rstrip("\n")} for line in lines]
    matched = [r for r in records if "ts" in r]

    # Correct for a Dec->Jan rollover: logcat's timestamp has no year, and all
    # lines were parsed onto the same anchor year, so a later line whose clock
    # time is far behind the previous one crossed into the following year.
    prev_ts = None
    year_bump = timedelta(days=0)
    for rec in matched:
        rec["ts"] += year_bump
        if prev_ts is not None and rec["ts"] < prev_ts - timedelta(hours=12):
            year_bump += timedelta(days=366)
            rec["ts"] += timedelta(days=366)
        prev_ts = rec["ts"]

    if not matched:
        print(colored(
            f"No parseable logcat lines found in {path} "
            "(expected 'adb logcat -v threadtime' format: 'MM-DD HH:MM:SS.mmm PID TID LEVEL TAG: msg', "
            "or a colorized wrapper's 'HH:MM:SS.mmm TID LEVEL TAG: msg').",
            RED,
        ))
        return

    # Resolve the date/PID-less format's unresolved records: any line for a
    # given tid that carried ALog's 'Tmain#'/'T<id>#' message hint names that
    # tid for every line sharing it, not just the ones with the hint.
    tid_names = {}
    for rec in matched:
        if "hint" in rec and rec["hint"] is not None:
            tid_names.setdefault(rec["tid"], rec["hint"])
    for rec in matched:
        if "hint" in rec:
            rec["thread"] = tid_names.get(rec["tid"], rec["tid"])

    base_ts = matched[0]["ts"]
    total_elapsed = (matched[-1]["ts"] - base_ts).total_seconds()
    elapsed_width = len(str(int(total_elapsed))) + 4  # digits + '.' + 3 decimals
    thread_width = max(len("main"), max(len(r["thread"]) for r in matched))
    if collapse_min < 2:
        collapse_min = len(records) + 1  # disabled: no run can ever be this long

    def signature(rec):
        return (rec["thread"], rec["level"], rec["tag"], _normalize_log_msg(rec["msg"]))

    prev_ts = None
    i, n = 0, len(records)
    while i < n:
        rec = records[i]
        if "ts" not in rec:
            print(rec["raw"])
            i += 1
            continue

        if prev_ts is not None:
            gap = (rec["ts"] - prev_ts).total_seconds()
            if gap >= gap_seconds:
                print(colored(f"\n----- GAP {gap:.3f}s -----\n", YELLOW))

        sig = signature(rec)
        j = i + 1
        while j < n and "ts" in records[j] and signature(records[j]) == sig:
            j += 1
        run = records[i:j]

        if len(run) >= collapse_min:
            print(_format_log_row(run[0], base_ts, elapsed_width, thread_width))
            span = (run[-1]["ts"] - run[0]["ts"]).total_seconds()
            print(colored(f"    ... {len(run) - 2} similar lines omitted ({span:.3f}s) ...", CYAN))
            print(_format_log_row(run[-1], base_ts, elapsed_width, thread_width))
            prev_ts = run[-1]["ts"]
            i = j
        else:
            print(_format_log_row(rec, base_ts, elapsed_width, thread_width))
            prev_ts = rec["ts"]
            i += 1


def _get_apk_package_name(apk_path):
    """Extracts the package name from a local .apk via aapt dump badging, or None if unavailable."""
    aapt = _find_aapt()
    if not aapt:
        return None
    result = subprocess.run([aapt, "dump", "badging", apk_path], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("package:"):
            match = re.search(r"name='([^']+)'", line)
            if match:
                return match.group(1)
    return None


def _find_sdk_tool(name):
    """Locates a build-tool by name on PATH, falling back to the newest Android SDK build-tools dir."""
    tool = shutil.which(name)
    if tool:
        return tool

    sdk_roots = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            sdk_roots.append(os.path.join(local_appdata, "Android", "Sdk"))
    else:
        sdk_roots.append(os.path.expanduser("~/Library/Android/sdk"))

    # apksigner ships as a shell script on Unix but a .bat on Windows; aapt/aapt2 get a .exe.
    candidate_names = [name, name + ".exe"] if sys.platform == "win32" else [name]
    if sys.platform == "win32" and name == "apksigner":
        candidate_names = ["apksigner.bat"] + candidate_names

    for sdk_root in sdk_roots:
        if not sdk_root or not os.path.isdir(sdk_root):
            continue
        build_tools = os.path.join(sdk_root, "build-tools")
        if not os.path.isdir(build_tools):
            continue
        for version in sorted(os.listdir(build_tools), reverse=True):
            for candidate_name in candidate_names:
                candidate = os.path.join(build_tools, version, candidate_name)
                if os.path.isfile(candidate):
                    return candidate
    return None


def _find_aapt():
    return _find_sdk_tool("aapt")


def _find_apksigner():
    return _find_sdk_tool("apksigner")


def _get_apk_signature(apk_path):
    """Returns the set of signing-cert SHA-256 digests for apk_path via `apksigner verify
    --print-certs`, or None if apksigner is unavailable or reports no certs."""
    apksigner = _find_apksigner()
    if not apksigner:
        return None
    result = subprocess.run([apksigner, "verify", "--print-certs", apk_path], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    digests = {line.rsplit(":", 1)[1].strip() for line in result.stdout.splitlines() if "SHA-256 digest" in line}
    return digests or None


def _files_identical(path_a, path_b):
    """Returns True if two files are byte-for-byte identical, comparing size first
    then SHA-256 digest."""
    if os.path.getsize(path_a) != os.path.getsize(path_b):
        return False
    return _sha256_file(path_a) == _sha256_file(path_b)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _get_apk_manifest_flags(aapt, apk_path):
    """Derives dumpsys-style pkgFlags (DEBUGGABLE, HAS_CODE, ALLOW_CLEAR_USER_DATA,
    ALLOW_BACKUP, TEST_ONLY) from the manifest's <application> boolean attributes,
    applying Android's documented defaults for any attribute that's absent."""
    defaults = {"debuggable": False, "hasCode": True, "allowClearUserData": True, "allowBackup": True, "testOnly": False}
    flag_names = {"debuggable": "DEBUGGABLE", "hasCode": "HAS_CODE", "allowClearUserData": "ALLOW_CLEAR_USER_DATA",
                  "allowBackup": "ALLOW_BACKUP", "testOnly": "TEST_ONLY"}

    result = subprocess.run([aapt, "dump", "xmltree", apk_path, "AndroidManifest.xml"], capture_output=True, text=True)
    if result.returncode != 0:
        return None

    values = dict(defaults)
    app_indent = None
    for line in result.stdout.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if app_indent is None:
            if stripped.startswith("E: application"):
                app_indent = indent
            continue
        if stripped.startswith("E: ") and indent <= app_indent:
            break
        match = re.match(r"A: android:(\w+)\(0x[0-9a-fA-F]+\)=\(type 0x12\)(0x[0-9a-fA-F]+)", stripped)
        if match and match.group(1) in defaults:
            values[match.group(1)] = match.group(2) != "0x0"

    return [flag_names[key] for key in flag_names if values[key]]


def apk_info(apk_path):
    """Prints package/version/permission/other info for a local .apk (mirrors apk-info.csh)."""
    aapt = _find_aapt()
    if not aapt:
        print(colored("Error: aapt not found on PATH or under an Android SDK's build-tools.", RED))
        return

    result = subprocess.run([aapt, "dump", "badging", apk_path], capture_output=True, text=True)
    if result.returncode != 0:
        print(colored(f"Error running aapt dump badging {apk_path}: {result.stderr.strip()}", RED))
        return
    lines = result.stdout.splitlines()

    print(apk_path)

    print(colored("=== Package ===", CYAN))
    for line in lines:
        if "package" in line.lower():
            for token in line.split():
                print(f"    {token}")

    print()
    print(colored("=== Version ===", CYAN))
    version_lines = [line for line in lines if "version" in line.lower()]
    for line in version_lines:
        for token in line.split():
            if "name" in token.lower():
                print(f"    {token}")
    version_tokens = sorted(token for line in version_lines for token in line.split() if "version" in token.lower())
    for token in version_tokens:
        print(f"    {token}")

    print()
    print(colored("=== Permissions ===", CYAN))
    perms = sorted(re.sub(r".*name=", "   ", line) for line in lines if "permission" in line.lower())
    for perm in perms:
        print(perm)

    print()
    print(colored("=== Other ===", CYAN))
    for line in lines:
        if re.search(r"(debug|native|testOnly|launchable)", line, re.IGNORECASE):
            print(f"    {line.strip()}")

    signature = _get_apk_signature(apk_path)
    if signature:
        print(f"    signatures=[{', '.join(sorted(signature))}]")

    flags = _get_apk_manifest_flags(aapt, apk_path)
    if flags is not None:
        print(f"    pkgFlags=[ {' '.join(flags)} ]")


def _format_age(mtime_ts):
    """Formats a mtime as: 'MMM dd yyyy' if >30 days old, '#days'/'#hours'/'#mins' otherwise."""
    delta = datetime.now() - datetime.fromtimestamp(mtime_ts)
    seconds = delta.total_seconds()
    days = seconds / 86400
    if days > 30:
        return datetime.fromtimestamp(mtime_ts).strftime("%b %d %Y")
    if days > 2:
        return f"{int(days)} days"
    hours = seconds / 3600
    if hours > 1:
        return f"{int(hours)} hours"
    minutes = seconds / 60
    return f"{max(0, int(minutes))} mins"


def _find_by_ext(directory, ext):
    """Recursively finds files under directory ending in ext (mirrors find-apk.csh's `find . -name *.ext`)."""
    rows = []
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if name.lower().endswith(ext):
                path = os.path.join(root, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                mtime = _format_age(st.st_mtime)
                rows.append((name, f"{st.st_size:,}", mtime, path))
    rows.sort(key=lambda row: row[0].lower())
    return rows


def _print_file_list(directory, ext, label):
    """Prints one find-apk.csh-style section: header, then name/size/mtime/path columns."""
    print(colored(f"===== {label} ====", YELLOW))
    rows = _find_by_ext(directory, ext)
    if not rows:
        return
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    for name, size, mtime, path in rows:
        print(f"  {name.ljust(widths[0])}  {size.rjust(widths[1])}  {mtime.ljust(widths[2])}  {path}")


def _find_apks(root, needle=None):
    """Recursively finds *.apk under root, optionally filtered by a case-insensitive
    substring of the filename. Returns paths sorted oldest-first (falls back to ctime
    if mtime is unavailable), so the newest match is always last."""
    needle = needle.lower() if needle else None
    rows = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".apk"):
                continue
            if needle and needle not in name.lower():
                continue
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            rows.append((path, getattr(st, "st_mtime", None) or st.st_ctime))
    rows.sort(key=lambda row: row[1])
    return [path for path, _mtime in rows]


def _prompt_apk_choice(matches):
    """Numbered prompt (1-based, oldest first) with 'n' always meaning newest (last)."""
    print(colored("--- Multiple APKs found (oldest first, newest last) ---", YELLOW))
    for i, path in enumerate(matches, 1):
        print(f"  {i}  {path}")
    choice = input(f"Enter # (1..{len(matches)}), or 'n' for newest: ").strip().lower()
    if choice == "n":
        return matches[-1]
    try:
        idx = int(choice)
        if 1 <= idx <= len(matches):
            return matches[idx - 1]
    except ValueError:
        pass
    print(colored("Invalid selection.", RED))
    return None


def _prompt_pkg_choice(matches):
    """Numbered prompt (1-based) for selecting one of several matching packages."""
    print(colored("--- Multiple packages found ---", YELLOW))
    for i, pkg in enumerate(matches, 1):
        print(f"  {i}  {pkg}")
    choice = input(f"Enter # (1..{len(matches)}): ").strip()
    try:
        idx = int(choice)
        if 1 <= idx <= len(matches):
            return matches[idx - 1]
    except ValueError:
        pass
    print(colored("Invalid selection.", RED))
    return None


def _resolve_apk_to_install(target):
    """Resolves --install's argument to a single .apk path: a full path installs
    directly; a directory searches it for every .apk; anything else is treated as
    a case-insensitive substring filter searched from '.'. Prompts on multiple matches."""
    if target and target.lower().endswith(".apk") and os.path.isfile(target):
        return target

    if target and os.path.isdir(target):
        matches = _find_apks(target)
    else:
        matches = _find_apks(".", needle=target or None)

    if not matches:
        print(colored("No matching .apk found.", RED))
        return None
    if len(matches) == 1:
        return matches[0]
    return _prompt_apk_choice(matches)


def list_apk(directory):
    _print_file_list(directory, ".apk", "APKs")


def list_aar(directory):
    _print_file_list(directory, ".aar", "AAR")


def list_all(directory):
    list_aar(directory)
    list_apk(directory)


# ---------------------------------------------------------------------------
# --summary: per-project reports for build.gradle Android projects
# ---------------------------------------------------------------------------

GRADLE_SKIP_DIRS = {".git", ".gradle", ".idea", "build", "node_modules", "out"}

_AGP_VERSION_PATTERNS = (
    re.compile(r'com\.android\.tools\.build:gradle:([0-9][\w.\-]*)'),
    re.compile(r'id\s*\(?\s*["\']com\.android\.(?:application|library|base)["\']\s*\)?\s*version\s*["\']([0-9][\w.\-]*)["\']'),
)


def _read_text(path):
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def _resolve_gradle_file(directory, stem):
    """Returns the first of stem.gradle.kts / stem.gradle found in directory, or None."""
    for name in (f"{stem}.gradle.kts", f"{stem}.gradle"):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            return path
    return None


def _has_android_block(text):
    return bool(re.search(r'\bandroid\s*\{', text))


def _find_gradle_projects(root):
    """Recursively finds Android Gradle project roots under root.

    A project root is a directory containing settings.gradle(.kts) (its modules
    come from that file's include(...) statements), or -- absent that -- a
    standalone directory whose build.gradle(.kts) declares an android {} block.
    Does not descend further once a project root is found, so a project's own
    modules aren't also reported as separate top-level projects.
    """
    projects = []
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in GRADLE_SKIP_DIRS)
        if _resolve_gradle_file(dirpath, "settings"):
            projects.append(dirpath)
            dirnames[:] = []
            continue
        build_file = _resolve_gradle_file(dirpath, "build")
        if build_file and _has_android_block(_read_text(build_file)):
            projects.append(dirpath)
            dirnames[:] = []
    return projects


def _parse_settings_modules(text):
    """Extracts module paths from include(...) statements, Groovy (include ':app',
    ':lib') and Kotlin DSL (include("app")) alike, including multi-line lists."""
    modules = []
    for call in re.finditer(r'\binclude\s*\(?\s*((?:["\'][^"\']+["\']\s*,?\s*)+)\)?', text):
        for name in re.finditer(r'["\']([^"\']+)["\']', call.group(1)):
            modules.append(name.group(1).lstrip(":"))
    return modules


def _parse_module_gradle(text):
    """Extracts common android{} fields from a module's build.gradle(.kts) text."""
    def find(pattern):
        m = re.search(pattern, text)
        return m.group(1) if m else None

    is_app = bool(re.search(
        r'(id\s*\(?\s*["\']com\.android\.application["\']|apply\s+plugin:\s*["\']com\.android\.application["\'])',
        text,
    ))
    return {
        "is_app": is_app,
        "namespace": find(r'namespace\s*=?\s*["\']([^"\']+)["\']'),
        "applicationId": find(r'applicationId\s*=?\s*["\']([^"\']+)["\']'),
        "compileSdk": find(r'compileSdk(?:Version)?\s*=?\s*(\d+)'),
        "targetSdk": find(r'targetSdk(?:Version)?\s*=?\s*(\d+)'),
        "versionCode": find(r'versionCode\s*=?\s*(\d+)'),
        "versionName": find(r'versionName\s*=?\s*["\']([^"\']+)["\']'),
    }


def _find_agp_version(texts, root):
    """Searches buildscript classpath / plugins-DSL version declarations first,
    falling back to gradle/libs.versions.toml's [versions] agp entry."""
    for text in texts:
        for pattern in _AGP_VERSION_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
    toml_text = _read_text(os.path.join(root, "gradle", "libs.versions.toml"))
    match = re.search(r'^\s*agp\s*=\s*["\']([0-9][\w.\-]*)["\']', toml_text, re.MULTILINE)
    return match.group(1) if match else None


def _find_gradle_version(root):
    props_text = _read_text(os.path.join(root, "gradle", "wrapper", "gradle-wrapper.properties"))
    match = re.search(r'gradle-([0-9][\w.\-]*)-(?:bin|all)\.zip', props_text)
    return match.group(1) if match else None


def _record_mtime(mtimes, path):
    try:
        mtimes.append(os.path.getmtime(path))
    except OSError:
        pass


_MODULE_FIELDS = ("namespace", "applicationId", "compileSdk", "targetSdk", "versionCode", "versionName")


def _print_module_fields(info):
    present = [(label, info[label]) for label in _MODULE_FIELDS if info.get(label) is not None]
    if not present:
        print("      (none)")
        return
    width = max(len(label) for label, _value in present)
    for label, value in present:
        print(f"      {(label + ':').ljust(width + 1)} {value}")


def print_gradle_summary(root, module_names, modules_info, agp_version, gradle_version, last_mod_ts):
    print(colored(f"==== {root} ====", YELLOW))
    print(f"  Last modified:  {_format_age(last_mod_ts) if last_mod_ts else 'unknown'}")
    print(f"  Modules:        [{', '.join(module_names)}]")
    print(f"  AGP version:    {agp_version or 'unknown'}")
    print(f"  Gradle version: {gradle_version or 'unknown'}")
    for name, info in modules_info:
        print(colored(f"  {name}", CYAN))
        _print_module_fields(info)
    print()


def _print_gradle_project_summary(root):
    """Builds and prints one project's report: module list, sdk/version fields
    (one line per module that defines the field), AGP/Gradle versions, and the
    newest mtime across the project's build.gradle files. Returns
    (module_count, agp_version, gradle_version, modules_info) for --summary's
    final aggregate report across projects."""
    settings_file = _resolve_gradle_file(root, "settings")
    if settings_file:
        module_names = _parse_settings_modules(_read_text(settings_file)) or ["."]
        module_dirs = [(name, os.path.join(root, name.replace(":", os.sep))) for name in module_names]
    else:
        module_dirs = [(os.path.basename(os.path.abspath(root)) or root, root)]

    gradle_texts = []
    modules_info = []
    mtimes = []

    root_build_file = _resolve_gradle_file(root, "build")
    if root_build_file:
        gradle_texts.append(_read_text(root_build_file))
        _record_mtime(mtimes, root_build_file)

    for name, module_dir in module_dirs:
        build_file = _resolve_gradle_file(module_dir, "build")
        if not build_file:
            modules_info.append((name, {}))
            continue
        text = _read_text(build_file)
        gradle_texts.append(text)
        modules_info.append((name, _parse_module_gradle(text)))
        _record_mtime(mtimes, build_file)

    agp_version = _find_agp_version(gradle_texts, root)
    gradle_version = _find_gradle_version(root)
    last_mod = max(mtimes) if mtimes else None

    print_gradle_summary(root, [name for name, _dir in module_dirs], modules_info, agp_version, gradle_version, last_mod)
    return len(module_dirs), agp_version, gradle_version, modules_info


def _natural_sort_key(value):
    """Sorts dotted version-like strings ('8.9.2' < '9.4.1') and plain integers
    ('35' < '36') numerically instead of lexically."""
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', value)]


def _format_value_counts(counter):
    if not counter:
        return "(none)"
    items = sorted(counter.items(), key=lambda kv: _natural_sort_key(kv[0]))
    return ", ".join(f"{value}({count})" for value, count in items)


def print_gradle_overall_summary(project_count, module_count, agp_counts, gradle_counts, compile_sdk_counts, target_sdk_counts):
    print(colored("==== Summary ====", YELLOW))
    print(f"  Number of projects: {project_count}")
    print(f"  Number of modules:  {module_count}")
    print(f"  AGP version:        {_format_value_counts(agp_counts)}")
    print(f"  Gradle version:     {_format_value_counts(gradle_counts)}")
    print(f"  Compile Sdk:        {_format_value_counts(compile_sdk_counts)}")
    print(f"  Target Sdk:         {_format_value_counts(target_sdk_counts)}")


def _expand_dir_patterns(patterns):
    """Expands each pattern to existing directories via glob, so wildcards (e.g.
    'dir*') work even on shells that don't expand them themselves -- notably
    Windows cmd.exe/PowerShell, unlike bash/zsh. A literal directory name with no
    wildcard just matches itself. Returns (dirs, unmatched_patterns), both deduped/
    ordered by first encounter (dirs by real path)."""
    seen = set()
    dirs = []
    unmatched = []
    for pattern in patterns:
        matches = [p for p in sorted(glob.glob(pattern)) if os.path.isdir(p)]
        if not matches:
            unmatched.append(pattern)
            continue
        for path in matches:
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                dirs.append(path)
    return dirs, unmatched


def summary(directories):
    """Scans each directory tree in `directories` (globs expanded via
    _expand_dir_patterns) for Android Gradle projects (see _find_gradle_projects)
    and prints one report per project found, deduped by real path in case the
    given directories overlap."""
    dirs, unmatched = _expand_dir_patterns(directories)
    for pattern in unmatched:
        print(colored(f"No directory matches '{pattern}'.", RED))

    seen = set()
    roots = []
    for directory in dirs:
        for root in _find_gradle_projects(directory):
            real = os.path.realpath(root)
            if real not in seen:
                seen.add(real)
                roots.append(root)

    if not roots:
        if dirs:
            print(colored(f"No build.gradle android projects found under {', '.join(dirs)}.", RED))
        return

    module_count = 0
    agp_counts = Counter()
    gradle_counts = Counter()
    compile_sdk_counts = Counter()
    target_sdk_counts = Counter()

    for root in sorted(roots):
        project_modules, agp_version, gradle_version, modules_info = _print_gradle_project_summary(root)
        module_count += project_modules
        if agp_version:
            agp_counts[agp_version] += 1
        if gradle_version:
            gradle_counts[gradle_version] += 1
        for _name, info in modules_info:
            if info.get("compileSdk"):
                compile_sdk_counts[info["compileSdk"]] += 1
            if info.get("targetSdk"):
                target_sdk_counts[info["targetSdk"]] += 1

    if len(roots) > 1:
        print_gradle_overall_summary(len(roots), module_count, agp_counts, gradle_counts, compile_sdk_counts, target_sdk_counts)


def _find_gradle_clean_targets(root):
    """Finds directories to run `gradlew clean` in, one level deep: for each
    subdirectory of root, checks it for build.gradle(.kts); if absent, checks
    one level further into its own subdirectories. Stops descending a branch
    as soon as a build.gradle(.kts) is found there."""
    targets = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path) or name in GRADLE_SKIP_DIRS:
            continue
        if _resolve_gradle_file(path, "build"):
            targets.append(path)
            continue
        for sub_name in sorted(os.listdir(path)):
            sub_path = os.path.join(path, sub_name)
            if not os.path.isdir(sub_path) or sub_name in GRADLE_SKIP_DIRS:
                continue
            if _resolve_gradle_file(sub_path, "build"):
                targets.append(sub_path)

    return targets


def _run_gradle_clean(directory):
    """Runs the project's gradlew wrapper (gradlew.bat on Windows, ./gradlew
    elsewhere) with the `clean` task in directory; warns and skips if the
    wrapper is missing."""
    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    if not os.path.isfile(os.path.join(directory, wrapper_name)):
        print(colored(f"No {wrapper_name} found in {directory}, skipping.", RED))
        return
    print(colored(f"==== {directory} ====", YELLOW))
    wrapper = wrapper_name if os.name == "nt" else f"./{wrapper_name}"
    subprocess.run(f"{wrapper} clean", cwd=directory, shell=True)


def clean_gradle(directories):
    """Scans each directory tree in `directories` (globs expanded via
    _expand_dir_patterns) one level deep for build.gradle(.kts) projects (see
    _find_gradle_clean_targets) and runs `gradlew clean` in each, deduped by
    real path in case the given directories overlap."""
    dirs, unmatched = _expand_dir_patterns(directories)
    for pattern in unmatched:
        print(colored(f"No directory matches '{pattern}'.", RED))

    seen = set()
    targets = []
    for directory in dirs:
        for target in _find_gradle_clean_targets(directory):
            real = os.path.realpath(target)
            if real not in seen:
                seen.add(real)
                targets.append(target)

    if not targets:
        if dirs:
            print(colored(f"No build.gradle project found one level deep under {', '.join(dirs)}.", RED))
        return

    for target in sorted(targets):
        _run_gradle_clean(target)


def do_encode():
    """Base64 encode stdin, one line at a time."""
    for line in sys.stdin:
        print(base64.b64encode(line.rstrip().encode("ascii")).decode("ascii"))


def do_decode():
    """Base64 decode stdin, one line at a time."""
    for line in sys.stdin:
        print(base64.b64decode(line.rstrip().encode("ascii")).decode("ascii"))


def main():
    parser = argparse.ArgumentParser(
        description="Android Tool - Unified ADB Utility",
        epilog="""
    Usage Examples
        View info for specific apps:
           and-tool.py --info 'com.google.*'
        View fuller info (activities, permissions, flags) for installed apps:
           and-tool.py --verbose --info drop.red
        View info for a local .apk file (no device required):
           and-tool.py --info build/debug/debug.apk
        Stop and clear data for an app:
            and-tool.py --stop-and-clear 'atak'
        Uninstall apps matching a pattern:
            and-tool.py --uninstall 'wx2'
        Show recent crash traces from the attached device, deduped to the most
        recent occurrence of each (preceded by a count when duplicated); optionally
        filter by a substring of the trace text (repeat --trace to OR multiple
        include patterns, or prefix with '!' to exclude):
            and-tool.py --trace
            and-tool.py --trace com.foo --trace com.bar
            and-tool.py --trace "!com.foo"
        Clear device logs:
            and-tool.py --clear
        Base64 encode/decode stdin:
            and-tool.py --encode < input.txt
        List .apk/.aar files under the current (or given) directory:
            and-tool.py --list
            and-tool.py --list-apk build/
            and-tool.py --list-aar build/
        Report module list, sdk/version fields, AGP/Gradle versions, and last
        build.gradle modification date for every Android project found under the
        current (or given) director(ies):
            and-tool.py --summary
            and-tool.py --summary ~/projects
            and-tool.py --summary dir1 dir2
        Run './gradlew clean' for every Android project found one directory
        level deep under the current (default) or given director(ies); repeat
        --clean-gradle to scan more than one directory:
            and-tool.py --clean-gradle
            and-tool.py --clean-gradle ~/projects
            and-tool.py --clean-gradle dir1 --clean-gradle dir2
        Install an apk (searches and prompts if there are multiple matches; if the
        apk's package is already installed, shows its --info summary and asks to
        confirm. If the installed apk is byte-for-byte identical to the on-disk apk,
        skips the install instead of prompting. If the installed and new apk are
        signed with different certs, confirming uninstalls the old copy first instead
        of waiting for adb install to fail; otherwise, on an install failure, offers
        to uninstall and retry):
            and-tool.py --install
            and-tool.py --install build/
            and-tool.py --install debug
            and-tool.py --install build/debug/debug.apk
        Launch an installed app (only launches if the pattern matches a single
        package, otherwise prompts to choose which one; add --start to --install to
        launch the just-installed apk, or use --start alone with no pattern to
        install from the current directory and then launch it):
            and-tool.py --start com.full.package.name
            and-tool.py --start full
            and-tool.py --install --start
            and-tool.py --start
        Enable or disable adb over wifi on the connected (USB) device (combine
        with --start to launch an app right after toggling):
            and-tool.py --wifi-adb on
            and-tool.py --wifi-adb off
            and-tool.py --wifi-adb on --start full
        Reformat a saved adb logcat file (full '-v threadtime', or the
        date/PID-less format some colorized wrappers like pidcat produce;
        ANSI color codes already in the file are stripped) to make startup/UI
        delays easier to spot (no device required): shows 'main' for the main
        thread, replaces the timestamp with an offset from the first line,
        marks gaps, and collapses runs of near-identical lines to their
        first/last entry:
            and-tool.py --log-time ~/Downloads/log.txt
            and-tool.py --log-time log.txt --gap-seconds 0.5
            and-tool.py --log-time log.txt --collapse-min 0
        Dump the logcat for a running process, matched by partial package/process
        name; prompts with a numbered list to choose when more than one process
        matches:
            and-tool.py --log-pid drop.red

    Action flags accept any unambiguous prefix, e.g. --info, --inf, --i
    """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    actions = parser.add_mutually_exclusive_group(required=False)
    for name in ("uninstall", "packages", "info", "stop-and-clear", "clear", "encode", "decode", "list-apk", "list-aar", "list", "install", "summary"):
        if name == "info":
            help_text = "info action using adb, or aapt for a local .apk file (accepts any unambiguous prefix)"
        elif name in ("uninstall", "packages", "stop-and-clear", "clear", "install"):
            help_text = f"{name} action using adb (accepts any unambiguous prefix)"
        else:
            help_text = f"{name} action (accepts any unambiguous prefix)"
        actions.add_argument(f"--{name}", dest="action", action="store_const", const=name, help=help_text)
    actions.add_argument(
        "--trace",
        dest="trace_filters",
        nargs="?",
        const="",
        action="append",
        default=None,
        metavar="[!]PATTERN",
        help="trace action using adb (accepts any unambiguous prefix): show FATAL EXCEPTION crash traces from logcat, "
        "deduped to the most recent occurrence of each (preceded by a count when there were duplicates). Repeat "
        "--trace PATTERN to only show traces whose text contains PATTERN (OR'd together), or --trace '!PATTERN' to "
        "exclude traces containing PATTERN; include and exclude filters can be combined. With no PATTERN, shows all traces",
    )
    actions.add_argument("--wifi-adb", dest="wifi_adb", choices=["on", "off"], help="Enable or disable adb over wifi on the connected device. Can be combined with --start to launch an app right after toggling")
    actions.add_argument(
        "--log-pid",
        dest="log_pid_pattern",
        metavar="PARTIAL_PKG_NAME",
        help="log-pid action using adb: finds running process(es) whose name contains PARTIAL_PKG_NAME "
        "(case-insensitive substring, via 'adb shell ps -A'), prompting to choose by number when more than "
        "one matches, then dumps that process's log via 'adb logcat --pid=<pid> -d'",
    )
    actions.add_argument(
        "--clean-gradle",
        dest="clean_gradle_dirs",
        nargs="?",
        const=".",
        action="append",
        default=None,
        metavar="DIR",
        help="clean-gradle action: scans DIR (default '.') one level deep for build.gradle projects and runs 'gradlew clean' in each; repeat --clean-gradle DIR to scan multiple directories",
    )
    actions.add_argument(
        "--log-time",
        dest="log_time_path",
        metavar="LOGFILE",
        help="log-time action (no device required): reformats a saved adb logcat file (full '-v threadtime', or the "
        "date/PID-less format some colorized wrappers like pidcat produce; any ANSI color codes already in the file "
        "are stripped) to make startup/UI-responsiveness delays easier to spot -- shows the thread column as 'main' "
        "for the main thread, replaces the timestamp column with an offset from the first line, marks gaps of "
        "--gap-seconds or more, and collapses runs of --collapse-min or more consecutive structurally-identical "
        "lines to their first/last entry",
    )

    parser.add_argument("patterns", nargs="*", default=[], help="Partial package name (substring match, case-insensitive), a path to a local .apk file for --info/--install, a directory to search for --list/--list-apk/--list-aar/--install (defaults to '.'), one or more directories to search for --summary (defaults to '.'), or a partial .apk filename for --install. Defaults to matching every installed package")
    parser.add_argument("--start", action="store_true", help="Launch an installed app using adb, matching pattern. With --install, launches the just-installed apk instead of matching pattern against installed packages. Alone with no pattern, installs an apk found under '.' first, then launches it")
    parser.add_argument("--verbose", "-v", action="store_true", help="With --info, show fuller dumpsys breakdown: activities, permissions, flags")
    parser.add_argument("--gap-seconds", type=float, default=1.0, metavar="SECS", help="With --log-time, mark gaps of at least SECS between consecutive log lines (default 1.0)")
    parser.add_argument("--collapse-min", type=int, default=5, metavar="N", help="With --log-time, collapse runs of at least N consecutive structurally-identical log lines (default 5); use 0 to disable collapsing")
    parser.add_argument("--version", "-V", action="version", version=f"and-tool {VERSION}")

    args = parser.parse_args()

    if (not args.action and not args.start and not args.wifi_adb and args.clean_gradle_dirs is None
            and args.trace_filters is None and not args.log_time_path and not args.log_pid_pattern):
        parser.error("one of the action arguments is required")
    if args.start and args.action and args.action != "install":
        parser.error("--start can only be combined with --install")
    if len(args.patterns) > 1 and args.action != "summary":
        parser.error("only --summary accepts more than one directory argument")
    if args.clean_gradle_dirs is not None and args.patterns:
        parser.error("--clean-gradle takes its directories via repeated '--clean-gradle DIR', not positional arguments")
    if args.trace_filters is not None and args.patterns:
        parser.error("--trace takes its filters via repeated '--trace [!]PATTERN', not positional arguments")
    if args.log_time_path and args.patterns:
        parser.error("--log-time takes its file via '--log-time LOGFILE', not positional arguments")

    if args.clean_gradle_dirs is not None:
        return clean_gradle(args.clean_gradle_dirs)

    if args.action == "encode":
        return do_encode()
    if args.action == "decode":
        return do_decode()
    if args.trace_filters is not None:
        return ADBTool().trace(args.trace_filters)
    if args.log_time_path:
        return log_time(args.log_time_path, gap_seconds=args.gap_seconds, collapse_min=args.collapse_min)
    if args.log_pid_pattern:
        return ADBTool().log_pid(args.log_pid_pattern)

    pattern = args.patterns[0] if args.patterns else ""

    if args.action == "info" and pattern and pattern.lower().endswith(".apk") and os.path.isfile(pattern):
        return apk_info(pattern)

    if args.action == "list-apk":
        return list_apk(pattern or ".")
    if args.action == "list-aar":
        return list_aar(pattern or ".")
    if args.action == "list":
        return list_all(pattern or ".")
    if args.action == "summary":
        return summary(args.patterns or ["."])

    tool = ADBTool()
    if args.wifi_adb:
        tool.wifi_adb(args.wifi_adb == "on")
        if not args.start:
            return

    if args.action == "uninstall":
        tool.uninstall(pattern)
    elif args.action == "packages":
        tool.packages(pattern)
    elif args.action == "info":
        tool.info(pattern, verbose=args.verbose)
    elif args.action == "stop-and-clear":
        tool.stop_and_clear(pattern)
    elif args.action == "clear":
        tool.clear_logs()
    elif args.action == "install":
        pkg = tool.install(pattern)
        if pkg and args.start:
            tool.launch(pkg)
    elif args.start:
        if pattern:
            tool.start(pattern)
        else:
            pkg = tool.install(pattern)
            if pkg:
                tool.launch(pkg)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
