import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time


def decode_console(data):
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("Official executable output is neither UTF-8 nor GB18030")


class ExeRuntime:
    def __init__(self, logs=None):
        self.logs = Path(logs) if logs else None
        self.temp = None
        self.display = None
        self.log = None
        self.owns_wine_prefix = False
        self.env = os.environ.copy()

    def __enter__(self):
        try:
            parent = self.logs if sys.platform == "win32" else None
            self.temp = tempfile.TemporaryDirectory(prefix="official-exe-", dir=parent)
            self.root = Path(self.temp.name)
            if self.logs:
                self.logs.mkdir(parents=True, exist_ok=True)
                self.log = (self.logs / "exe-runtime.log").open("wb")
            if sys.platform == "win32":
                self.env.update({"TEMP": str(self.root), "TMP": str(self.root)})
                return self
            for command in ("wine", "wineserver", "wineboot", "Xvfb"):
                if not shutil.which(command):
                    raise RuntimeError(f"Missing {command}; build and use the supplied Docker image")
            # Start Xvfb directly: xvfb-run's readiness signal can hang when it is PID 1.
            display = next(n for n in range(90, 190) if not Path(f"/tmp/.X11-unix/X{n}").exists())
            self.display = subprocess.Popen(
                ["Xvfb", f":{display}", "-screen", "0", "800x600x24", "-nolisten", "tcp", "-ac"],
                stdout=self.log or subprocess.DEVNULL, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 15
            while True:
                if self.display.poll() is not None:
                    raise RuntimeError("Xvfb exited during startup")
                try:
                    with socket.socket(socket.AF_UNIX) as probe:
                        probe.connect(f"/tmp/.X11-unix/X{display}")
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Xvfb did not become ready")
                    time.sleep(.05)
            self.env.update({"DISPLAY": f":{display}", "WINEPREFIX": str(self.root / "wine"),
                             "WINEARCH": "win64", "WINEDEBUG": "-all",
                             "WINEDLLOVERRIDES": "mscoree,mshtml=;winedbg.exe=d",
                             "LANG": "zh_CN.UTF-8", "LC_ALL": "zh_CN.UTF-8"})
            self.owns_wine_prefix = True
            subprocess.run(["wineboot", "--init"], env=self.env, check=True, timeout=120,
                           stdout=self.log or subprocess.DEVNULL, stderr=subprocess.STDOUT)
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def path(self, path):
        absolute = str(Path(path).resolve())
        return absolute if sys.platform == "win32" else "Z:" + absolute.replace("/", "\\")

    def command(self, executable, arguments):
        executable = Path(executable).resolve()
        if not executable.is_file():
            raise FileNotFoundError(executable)
        if sys.platform == "win32":
            return [str(executable), *arguments]
        # The original bootloader crashes when run from a host bind mount under Wine.
        # Copy the binary unchanged to Wine's native filesystem before execution.
        destination = self.root / "wine/drive_c/official" / executable.stem
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(executable, destination / executable.name)
        return ["wine", f"C:\\official\\{executable.stem}\\{executable.name}", *arguments]

    def __exit__(self, *exc):
        try:
            if self.owns_wine_prefix:
                for args in (["wineserver", "-k"], ["wineserver", "-w"]):
                    try:
                        subprocess.run(args, env=self.env, timeout=15, check=False,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            if self.display is not None and self.display.poll() is None:
                self.display.terminate()
                try:
                    self.display.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.display.kill()
                    self.display.wait()
        finally:
            if self.log:
                self.log.close()
            if self.temp:
                self.temp.cleanup()
