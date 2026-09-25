"""Thin ADB wrapper — the phone's hands and camera for the dev harness.

Phase 0 drives a USB-connected device from the computer, exactly as the design
docs describe ("actions issued as ADB shell commands"). This is the harness, not
the shippable app: the on-device app will use AccessibilityService instead (an
app cannot ADB itself). Keeping the two behind the same Action model means the
control loop is identical either way.

The client is an interface so the whole loop is testable with FakeADB — no
device, exactly like perception's stub detectors and NullPlanner.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Protocol

log = logging.getLogger(__name__)


class ADBError(RuntimeError):
    pass


class ADBClient(Protocol):
    def devices(self) -> list[str]: ...
    def screencap(self) -> bytes: ...            # raw PNG bytes
    def shell(self, *args: str) -> str: ...
    def list_packages(self) -> list[str]: ...    # installed package names


def find_adb() -> Optional[str]:
    """Locate adb: ADB_PATH env first, then PATH, then the usual SDK spots."""
    env_path = os.environ.get("ADB_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    found = shutil.which("adb")
    if found:
        return found
    candidates = []
    for root in (os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT"),
                 str(Path.home() / "AppData/Local/Android/Sdk"),
                 str(Path.home() / "Android/Sdk")):
        if root:
            candidates += [Path(root) / "platform-tools" / n for n in ("adb", "adb.exe")]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


class SubprocessADB:
    """Real ADB over subprocess. Talks to one device (serial optional)."""

    def __init__(self, serial: Optional[str] = None, adb_path: Optional[str] = None,
                 timeout: float = 30.0) -> None:
        self.adb = adb_path or find_adb()
        if not self.adb:
            raise ADBError("adb not found. Install platform-tools or set ANDROID_HOME.")
        self.serial = serial
        self.timeout = timeout

    def _base(self) -> list[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else [])

    def _run(self, args: list[str], *, binary: bool = False):
        proc = subprocess.run(self._base() + args, capture_output=True,
                              timeout=self.timeout)
        if proc.returncode != 0:
            raise ADBError(f"adb {' '.join(args)} failed: {proc.stderr.decode(errors='replace')[:200]}")
        return proc.stdout if binary else proc.stdout.decode(errors="replace")

    def devices(self) -> list[str]:
        out = self._run(["devices"])
        serials = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if line and "\tdevice" in line:
                serials.append(line.split("\t")[0])
        return serials

    def screencap(self) -> bytes:
        # Preferred: exec-out streams raw bytes with no CRLF mangling (adb 1.0.35+).
        try:
            png = self._run(["exec-out", "screencap", "-p"], binary=True)
            if png.startswith(b"\x89PNG"):
                return png
        except ADBError:
            pass
        # Fallback for old adb (no exec-out): capture on device, pull the file.
        import os
        import tempfile
        remote = "/sdcard/_agent_screencap.png"
        self.shell("screencap", "-p", remote)
        tmp = Path(tempfile.gettempdir()) / f"_agent_cap_{os.getpid()}.png"
        try:
            self._run(["pull", remote, str(tmp)])
            data = tmp.read_bytes()
        finally:
            try:
                self.shell("rm", "-f", remote)
            except ADBError:
                pass
            tmp.unlink(missing_ok=True)
        if not data.startswith(b"\x89PNG"):
            raise ADBError("screencap did not return a PNG (exec-out and fallback both failed)")
        return data

    def shell(self, *args: str) -> str:
        return self._run(["shell", *args])

    def list_packages(self) -> list[str]:
        out = self.shell("pm", "list", "packages")
        # lines look like "package:com.android.chrome"
        return [ln.split(":", 1)[1].strip() for ln in out.splitlines()
                if ln.startswith("package:")]


class FakeADB:
    """In-memory ADB for tests. Records shell commands, returns a canned PNG."""

    def __init__(self, png: bytes = b"\x89PNG\r\n\x1a\n", serials: Optional[list[str]] = None) -> None:
        self._png = png
        self._serials = serials if serials is not None else ["emulator-5554"]
        self.commands: list[tuple[str, ...]] = []

    def devices(self) -> list[str]:
        return list(self._serials)

    def screencap(self) -> bytes:
        self.commands.append(("screencap",))
        return self._png

    def shell(self, *args: str) -> str:
        self.commands.append(tuple(args))
        return ""

    def list_packages(self) -> list[str]:
        return ["com.android.chrome", "com.google.android.youtube",
                "com.whatsapp", "com.google.android.apps.maps"]
