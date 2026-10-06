"""
GazeAssist — start the whole system with one command:

    python run_all.py

What it does
  1. Checks port 8000 is free (clear message if not).
  2. Starts the Django caregiver dashboard (gazeassist_web) in the background
     with `runserver 127.0.0.1:8000 --noreload`, using gazeassist_web\\.venv,
     and prints its log lines prefixed with "[web]".
  3. Waits until the dashboard really answers HTTP (up to 20 s).
  4. Starts the Tkinter gaze app (gazeassist\\main.py) with its own Python
     (miniproject\\.venv), in its own process, where Tk runs on that
     process's main thread exactly as when you run `python main.py`.
  5. When the gaze app window is closed — or you press Ctrl+C here — the
     gaze app is asked to close the same way as clicking its X button, then
     the dashboard is stopped (terminate, then kill after a grace period).
     On Windows both children are also tied to a Job Object, so even if this
     launcher is killed, the OS stops them too: no orphaned server.

Why two subprocesses instead of importing main.py
  The two programs use different virtual environments (Django lives only in
  gazeassist_web\\.venv; MediaPipe/OpenCV only in miniproject\\.venv), so each
  is started with its own interpreter — exactly like the two-terminal workflow,
  which keeps working unchanged. Inside the gaze app's own process Tkinter is
  on the main thread as required. A subprocess can also be closed gracefully
  from outside (WM_CLOSE -> the app's own _on_close), whereas an imported Tk
  mainloop swallows Ctrl+C inside its callbacks and gives no handle to close it.

No terminal needed (desktop icon / start at login)
  python setup_shortcuts.py            once: puts a GazeAssist icon on the desktop
  python setup_shortcuts.py --autostart  ...and starts GazeAssist when Windows logs in
  The icon runs `pythonw run_all.py --background`: no console windows, logs in
  logs\run_all.log and logs\gaze.log, problems shown in a message box.

Options
  python run_all.py --background                 no console; log files + message boxes
  python run_all.py --port 8001                  use another port
  python run_all.py --host 0.0.0.0               let phones on your Wi-Fi reach the dashboard
  python run_all.py --gaze-script verify_caregiver_link.py   run the link check instead of main.py

No files of either project are modified. Standard library only.
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "gazeassist_web"
GAZE_DIR = ROOT / "gazeassist"
IS_WINDOWS = os.name == "nt"

READY_TIMEOUT_S = 20.0
GAZE_CLOSE_GRACE_S = 10.0     # time for the gaze app to run its own _on_close()
WEB_STOP_GRACE_S = 5.0

_print_lock = threading.Lock()


def log(message: str) -> None:
    with _print_lock:
        print(f"[run_all] {message}", flush=True)


# ──────────────────────────────────────────────────────────────────────
# Finding the right interpreter for each program
# ──────────────────────────────────────────────────────────────────────

def venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def pick_python(candidates: list[Path], must_import: str) -> str:
    """First venv interpreter that exists, else the one running this script."""
    for venv in candidates:
        exe = venv_python(venv)
        if exe.exists():
            return str(exe)
    log(f"note: no virtual environment found for {must_import!r}; using {sys.executable}")
    return sys.executable


# ──────────────────────────────────────────────────────────────────────
# Windows: Job Object so children die with the launcher, and graceful close
# ──────────────────────────────────────────────────────────────────────

class _KillOnCloseJob:
    """Windows Job Object with KILL_ON_JOB_CLOSE: when this launcher exits for
    any reason (even if killed), Windows terminates every process in the job."""

    def __init__(self) -> None:
        self.handle = None
        if not IS_WINDOWS:
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in (
                    "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                    "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC_LIMIT(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class EXTENDED_LIMIT(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", BASIC_LIMIT),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            kernel32.CreateJobObjectW.restype = wintypes.HANDLE
            kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = job
            self._kernel32 = kernel32
        except Exception as exc:  # never block startup over this safety net
            log(f"note: could not create a Windows job object ({exc}); cleanup relies on the launcher")

    def add(self, proc: subprocess.Popen) -> None:
        if self.handle is None:
            return
        if not self._kernel32.AssignProcessToJobObject(self.handle, int(proc._handle)):
            log(f"note: could not attach pid {proc.pid} to the job object")


def _process_tree(pid: int) -> set[int]:
    """`pid` plus all its descendant process IDs (Windows only).

    Needed because a venv's python.exe on Windows is a small redirector that
    starts the real interpreter as a child: the Tk window belongs to that child.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    if not snapshot or snapshot == wintypes.HANDLE(-1).value:
        return {pid}
    children: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    tree, todo = {pid}, [pid]
    while todo:
        for child in children.get(todo.pop(), []):
            if child not in tree:
                tree.add(child)
                todo.append(child)
    return tree


def _post_wm_close(pid: int) -> int:
    """Send WM_CLOSE to the visible top-level windows of `pid` and its child
    processes (Windows only).

    For a Tkinter app this triggers its WM_DELETE_WINDOW handler - the same
    clean shutdown as clicking the window's X. Returns the number of windows.
    """
    if not IS_WINDOWS:
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    WM_CLOSE = 0x0010

    pids = _process_tree(pid)
    found: list[int] = []

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return len(found)


# ──────────────────────────────────────────────────────────────────────
# Dashboard (Django) helpers
# ──────────────────────────────────────────────────────────────────────

def port_in_use(port: int) -> bool:
    """True if something already accepts connections on localhost:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            return True
    # Also try binding: catches a listener on another interface only.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def stream_output(proc: subprocess.Popen, prefix: str) -> None:
    """Copy a child's output to our terminal, one prefixed line at a time."""
    assert proc.stdout is not None
    for line in proc.stdout:
        with _print_lock:
            print(f"{prefix} {line.rstrip()}", flush=True)


def wait_until_ready(url: str, proc: subprocess.Popen, timeout: float) -> bool:
    """Poll `url` until any HTTP response arrives (even 302/404 means it's up)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False  # server exited (error shown in its [web] output)
        try:
            urllib.request.urlopen(url, timeout=1.0).close()
            return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, ConnectionError, socket.timeout, OSError):
            time.sleep(0.3)
    return False


def stop_process(proc: subprocess.Popen, name: str, grace: float) -> None:
    """Terminate, wait `grace` seconds, then kill."""
    if proc.poll() is not None:
        return
    log(f"stopping {name}...")
    try:
        proc.terminate()
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        log(f"{name} did not stop in {grace:.0f} s - killing it")
        proc.kill()
        proc.wait(timeout=5)
    except OSError:
        pass


def close_gaze_app(proc: subprocess.Popen) -> None:
    """Ask the gaze app to close like clicking X; force it only if it hangs."""
    if proc.poll() is not None:
        return
    if _post_wm_close(proc.pid):
        log("asking the gaze app to close (same as clicking its X)...")
        try:
            proc.wait(timeout=GAZE_CLOSE_GRACE_S)
            return
        except subprocess.TimeoutExpired:
            log("gaze app did not close in time")
    stop_process(proc, "gaze app", WEB_STOP_GRACE_S)


# ──────────────────────────────────────────────────────────────────────
# Background mode (desktop icon): log files, message boxes, single instance
# ──────────────────────────────────────────────────────────────────────

LOG_DIR = ROOT / "logs"
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKGROUND = False          # set in main()
_instance_mutex = None      # keeps the single-instance mutex alive


def _open_log(name: str):
    """Open logs/<name> for appending; start fresh when it gets large."""
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / name
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_suffix(".old.log"))
    handle = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    return handle


def notify(title: str, text: str, error: bool = True) -> None:
    """Tell the person at the computer. A message box when there's no terminal."""
    log(f"{title}: {text}")
    if BACKGROUND and IS_WINDOWS:
        import ctypes
        MB_ICONERROR, MB_ICONINFO, MB_TOPMOST = 0x10, 0x40, 0x40000
        ctypes.windll.user32.MessageBoxW(None, text, title, (MB_ICONERROR if error else MB_ICONINFO) | MB_TOPMOST)


def already_running() -> bool:
    """Single instance: True if another run_all.py launcher is running (Windows)."""
    global _instance_mutex
    if not IS_WINDOWS:
        return False
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _instance_mutex = kernel32.CreateMutexW(None, False, "Local\\GazeAssistLauncher")
    return ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS


def is_gazeassist_dashboard(url: str) -> bool:
    """Does the server on this port look like our dashboard (e.g. started in a terminal)?"""
    try:
        with urllib.request.urlopen(f"{url}/accounts/login/", timeout=2.0) as resp:
            return b"GazeAssist" in resp.read(20000)
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    global BACKGROUND
    # Never crash on a character the terminal/log file can't encode.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Start the GazeAssist dashboard and gaze app together.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address the dashboard listens on (0.0.0.0 = reachable from your Wi-Fi)")
    parser.add_argument("--port", type=int, default=8000, help="dashboard port (default 8000)")
    parser.add_argument("--gaze-script", default="main.py",
                        help="script to run in gazeassist/ (default main.py)")
    parser.add_argument("--background", action="store_true",
                        help="no console windows: output goes to logs/, problems show a message box")
    args = parser.parse_args()

    # pythonw (desktop icon) has no console at all: treat it as background mode.
    BACKGROUND = args.background or sys.stdout is None
    gaze_log = None
    if BACKGROUND:
        sys.stdout = sys.stderr = _open_log("run_all.log")
        gaze_log = _open_log("gaze.log")

    if already_running():
        notify("GazeAssist", "GazeAssist is already running.", error=False)
        return 0

    for folder in (WEB_DIR, GAZE_DIR):
        if not folder.is_dir():
            log(f"cannot find {folder} - put run_all.py in the folder that contains gazeassist and gazeassist_web")
            return 2

    gaze_script = Path(args.gaze_script)
    if not gaze_script.is_absolute():
        gaze_script = GAZE_DIR / gaze_script
    if not gaze_script.exists():
        log(f"cannot find {gaze_script}")
        return 2

    web_python = pick_python([WEB_DIR / ".venv"], "django")
    gaze_python = pick_python([GAZE_DIR / ".venv", ROOT / ".venv"], "gaze app")
    local_url = f"http://127.0.0.1:{args.port}"

    # 6. Port already taken? Reuse our own dashboard (e.g. started in a
    # terminal); anything else gets a clear message instead of a silent failure.
    reuse_dashboard = False
    if port_in_use(args.port):
        if is_gazeassist_dashboard(local_url):
            reuse_dashboard = True
            log(f"a GazeAssist dashboard is already running on port {args.port} - using it (it is left running)")
        else:
            notify("GazeAssist",
                   f"Port {args.port} is already used by another program, so the dashboard can't start.\n\n"
                   f"Close that program, or start with another port:  python run_all.py --port {args.port + 1}")
            return 1

    # Children get their own process group on Windows, so Ctrl+C reaches only
    # this launcher, which then shuts them down in the right order. In
    # background mode they also get no console window.
    group_flag = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    if IS_WINDOWS and BACKGROUND:
        group_flag |= subprocess.CREATE_NO_WINDOW
    job = _KillOnCloseJob()

    stop_requested = threading.Event()

    def on_signal(signum, _frame):
        if not stop_requested.is_set():
            log(f"received {signal.Signals(signum).name} - shutting down...")
        stop_requested.set()

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, sig_name):
            signal.signal(getattr(signal, sig_name), on_signal)

    web_env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    # The launcher's own server is local, so point the gaze app at it.
    # (load_dotenv never overrides variables that are already set.)
    gaze_env = dict(os.environ, GAZEASSIST_API_URL=local_url)

    web = None
    gaze = None
    exit_code = 0
    try:
        if not reuse_dashboard:
            # 1. Dashboard in the background, without the autoreloader.
            log(f"starting dashboard on http://{args.host}:{args.port}  ({web_python})")
            web = subprocess.Popen(
                [web_python, "manage.py", "runserver", f"{args.host}:{args.port}", "--noreload"],
                cwd=WEB_DIR, env=web_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=group_flag,
            )
            job.add(web)
            threading.Thread(target=stream_output, args=(web, "[web]"), daemon=True).start()

            # 2. Wait until it really answers.
            if not wait_until_ready(f"{local_url}/accounts/login/", web, READY_TIMEOUT_S):
                if web.poll() is not None:
                    notify("GazeAssist", f"The caregiver dashboard could not start (exit code {web.returncode}).\n\n"
                                         f"Details: {LOG_DIR / 'run_all.log'}" if BACKGROUND else
                                         f"the dashboard exited with code {web.returncode} - see the [web] lines above")
                else:
                    notify("GazeAssist", f"The caregiver dashboard did not answer within {READY_TIMEOUT_S:.0f} s.")
                return 1
            log(f"dashboard is up -> {local_url}/")
        if stop_requested.is_set():
            return 130

        # 3. Gaze app with its own interpreter; Tk runs on its main thread.
        log(f"starting gaze app: {gaze_script.name}  ({gaze_python})")
        gaze = subprocess.Popen(
            [gaze_python, str(gaze_script)],
            cwd=GAZE_DIR, env=gaze_env, creationflags=group_flag,
            stdout=gaze_log, stderr=subprocess.STDOUT if gaze_log else None,
            stdin=subprocess.DEVNULL if BACKGROUND else None,
        )
        job.add(gaze)

        # 4. Run until the gaze app exits or we are asked to stop.
        while not stop_requested.is_set():
            if gaze.poll() is not None:
                exit_code = gaze.returncode
                log(f"gaze app closed (exit code {exit_code})")
                break
            if web is not None and web.poll() is not None:
                log(f"the dashboard stopped unexpectedly (exit code {web.returncode})")
                exit_code = 1
                break
            stop_requested.wait(0.25)
        else:
            exit_code = 130  # stopped with Ctrl+C / signal

    finally:
        if gaze is not None:
            close_gaze_app(gaze)
        if web is not None:
            stop_process(web, "dashboard", WEB_STOP_GRACE_S)
        log("all stopped.")
        if gaze_log is not None:
            gaze_log.close()
    if BACKGROUND and gaze is not None and exit_code not in (0, 130):
        notify("GazeAssist", f"GazeAssist closed with an error (exit code {exit_code}).\n\n"
                             f"Details: {LOG_DIR / 'gaze.log'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
