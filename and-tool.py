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
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

VERSION = "v6.08.10"

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

    def _run_cmd(self, args, capture=True):
        """Helper to run shell commands."""
        full_cmd = self.adb_cmd + args
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
        apk = _resolve_apk_to_install(target)
        if not apk:
            return

        pkg_name = _get_apk_package_name(apk)
        installed = bool(pkg_name and pkg_name in self._get_packages(pkg_name))
        sig_mismatch = installed and self._signatures_differ(pkg_name, apk)

        if installed:
            print(colored(f"Existing install of {pkg_name}:", CYAN))
            self.info(pkg_name, verbose=False)
            if sig_mismatch:
                print(colored(f"Signing certificate differs from the installed {pkg_name} - a plain install would fail.", RED))
            answer = input(colored(f"Install {apk} over existing {pkg_name}? [y/N]: ", YELLOW)).strip().lower()
            if answer != "y":
                print("Skipping install.")
                return
            if sig_mismatch:
                self._uninstall_pkg(pkg_name)

        print(f"Installing {apk} ...")
        output = self._install_apk(apk)
        if "Success" not in output and pkg_name and not sig_mismatch:
            answer = input(colored(f"Install failed. Uninstall existing {pkg_name} and retry install? [y/N]: ", YELLOW)).strip().lower()
            if answer == "y":
                self._uninstall_pkg(pkg_name)
                print(f"Retrying install of {apk} ...")
                self._install_apk(apk)

    def _install_apk(self, apk):
        """Runs adb install, printing and returning its combined stdout+stderr."""
        result = subprocess.run(self.adb_cmd + ["install", "-r", "-t", "-d", "-i", "com.android.vending", apk], capture_output=True, text=True)
        output = (result.stdout + result.stderr).strip()
        print(output)
        return output

    def _signatures_differ(self, pkg_name, apk):
        """Best-effort pre-check: pulls the installed apk and compares its signing
        cert(s) against the candidate apk via apksigner. Returns False (assume no
        mismatch) if apksigner or the pulled apk is unavailable, leaving the normal
        install-then-retry-on-failure flow in install() as the fallback."""
        installed_apk = self._pull_installed_apk(pkg_name)
        if not installed_apk:
            return False
        try:
            old_sig = _get_apk_signature(installed_apk)
            new_sig = _get_apk_signature(apk)
        finally:
            os.remove(installed_apk)

        if old_sig is None or new_sig is None:
            return False
        return old_sig != new_sig

    def _pull_installed_apk(self, pkg_name):
        """Pulls the installed base apk for pkg_name to a temp file; returns its
        local path, or None if the package has no path or the pull fails."""
        paths = [line.replace("package:", "", 1) for line in self._run_cmd(["shell", "pm", "path", pkg_name]).splitlines() if line.startswith("package:")]
        if not paths:
            return None
        device_path = next((p for p in paths if os.path.basename(p) == "base.apk"), paths[0])

        fd, local_path = tempfile.mkstemp(suffix=".apk")
        os.close(fd)
        result = subprocess.run(self.adb_cmd + ["pull", device_path, local_path], capture_output=True, text=True)
        if result.returncode != 0 or os.path.getsize(local_path) == 0:
            os.remove(local_path)
            return None
        return local_path

    def _uninstall_pkg(self, pkg):
        print(f"Stopping and clearing {pkg}...")
        self._run_cmd(["shell", "am", "force-stop", pkg], capture=False)
        self._run_cmd(["shell", "pm", "clear", "-a", pkg], capture=False)
        print(f"Uninstalling {pkg}...")
        self._run_cmd(["shell", "pm", "uninstall", pkg], capture=False)

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
            self._run_cmd(["shell", "am", "force-stop", pkg], capture=False)
            self._run_cmd(["shell", "pm", "clear", "--user", "0", pkg], capture=False)
            self._run_cmd(["shell", "am", "start", "-a", "android.settings.APPLICATION_DETAILS_SETTINGS", "-d", f"package:{pkg}"], capture=False)

    def clear_logs(self):
        print("Clearing logcat...")
        self._run_cmd(["logcat", "-c"], capture=False)

    def trace(self):
        """Show FATAL EXCEPTION crash traces, falling back to generic java errors."""
        print(colored("---- Show Traces ----", YELLOW))
        lines = self._run_cmd(["logcat", "-d"]).splitlines()

        show = False
        proc_id = None
        trace_count = 0

        for line in lines:
            line = line.strip()
            if "FATAL EXCEPTION" in line:
                trace_count += 1
                show = True
                parts = [p for p in line.split("FATAL")[0].split("E") if p]
                proc_id = parts[-1] if parts else "_none_"
                print(line)
            elif show and proc_id and proc_id in line:
                parts = [p for p in line.split(proc_id) if p]
                if len(parts) > 1:
                    print(parts[1])
            elif show:
                print(colored("---- End Trace ----\n", YELLOW))
                show = False
                proc_id = None

        if trace_count == 0:
            print(colored("---- Show Error logs with 'java' ----", YELLOW))
            for line in self._run_cmd(["logcat", "-d", "*:E"]).splitlines():
                if "java" in line:
                    print(line)


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

    for sdk_root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"), os.path.expanduser("~/Library/Android/sdk")):
        if not sdk_root:
            continue
        build_tools = os.path.join(sdk_root, "build-tools")
        if not os.path.isdir(build_tools):
            continue
        for version in sorted(os.listdir(build_tools), reverse=True):
            candidate = os.path.join(build_tools, version, name)
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
                mtime = datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M:%S %Y")
                rows.append((name, str(st.st_size), mtime, path))
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
        print(f"  {name.ljust(widths[0])}  {size.ljust(widths[1])}  {mtime.ljust(widths[2])}  {path}")


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
        Show recent crash traces from the attached device:
            and-tool.py --trace
        Clear device logs:
            and-tool.py --clear
        Base64 encode/decode stdin:
            and-tool.py --encode < input.txt
        List .apk/.aar files under the current (or given) directory:
            and-tool.py --list
            and-tool.py --list-apk build/
            and-tool.py --list-aar build/
        Install an apk (searches and prompts if there are multiple matches; if the
        apk's package is already installed, shows its --info summary and asks to
        confirm. If the installed and new apk are signed with different certs,
        confirming uninstalls the old copy first instead of waiting for adb install
        to fail; otherwise, on an install failure, offers to uninstall and retry):
            and-tool.py --install
            and-tool.py --install build/
            and-tool.py --install debug
            and-tool.py --install build/debug/debug.apk

    Action flags accept any unambiguous prefix, e.g. --info, --inf, --i
    """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    actions = parser.add_mutually_exclusive_group(required=True)
    for name in ("uninstall", "packages", "info", "stop-and-clear", "clear", "trace", "encode", "decode", "list-apk", "list-aar", "list", "install"):
        actions.add_argument(f"--{name}", dest="action", action="store_const", const=name, help=f"{name} action (accepts any unambiguous prefix)")

    parser.add_argument("pattern", nargs="?", default="", help="Partial package name (substring match, case-insensitive), a path to a local .apk file for --info/--install, a directory to search for --list/--list-apk/--list-aar/--install (defaults to '.'), or a partial .apk filename for --install. Defaults to matching every installed package")
    parser.add_argument("--verbose", "-v", action="store_true", help="With --info, show fuller dumpsys breakdown: activities, permissions, flags")
    parser.add_argument("--version", "-V", action="version", version=f"and-tool {VERSION}")

    args = parser.parse_args()

    if args.action == "encode":
        return do_encode()
    if args.action == "decode":
        return do_decode()

    if args.action == "info" and args.pattern and args.pattern.lower().endswith(".apk") and os.path.isfile(args.pattern):
        return apk_info(args.pattern)

    if args.action == "list-apk":
        return list_apk(args.pattern or ".")
    if args.action == "list-aar":
        return list_aar(args.pattern or ".")
    if args.action == "list":
        return list_all(args.pattern or ".")

    tool = ADBTool()
    if args.action == "uninstall":
        tool.uninstall(args.pattern)
    elif args.action == "packages":
        tool.packages(args.pattern)
    elif args.action == "info":
        tool.info(args.pattern, verbose=args.verbose)
    elif args.action == "stop-and-clear":
        tool.stop_and_clear(args.pattern)
    elif args.action == "clear":
        tool.clear_logs()
    elif args.action == "trace":
        tool.trace()
    elif args.action == "install":
        tool.install(args.pattern)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
