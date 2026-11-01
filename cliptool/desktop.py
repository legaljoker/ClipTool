"""ClipTool Editor as a desktop program (this is what ClipTool-Editor.exe runs).

It starts the editor's local server and shows it in its own window (Microsoft
WebView2, built into Windows 10/11). If that window can't be created, the editor
opens in the default web browser instead.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path


def resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def data_home() -> Path:
    """Projects, settings and logs: CLIPTOOL_HOME, or Documents\\ClipTool for the installed app."""
    if os.environ.get("CLIPTOOL_HOME"):
        return Path(os.environ["CLIPTOOL_HOME"])
    if getattr(sys, "frozen", False):
        docs = Path.home() / "Documents"
        return (docs if docs.is_dir() else Path.home()) / "ClipTool"
    return Path(__file__).resolve().parent.parent


def setup_environment() -> Path:
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["CLIPTOOL_HOME"] = str(home)
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for folder in (Path(sys.executable).parent / "ffmpeg", resource_dir() / "ffmpeg"):
        if (folder / exe).exists():  # the ffmpeg that ships with the app
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            break
    os.environ.setdefault("HF_HOME", str(home / "models"))  # speech models download here
    if os.name == "nt" and getattr(sys, "frozen", False):
        # A windowed app would flash a black console window for every ffmpeg call.
        original = subprocess.Popen.__init__

        def no_console(self, *args, **kwargs):
            kwargs.setdefault("creationflags", 0x08000000)  # CREATE_NO_WINDOW
            original(self, *args, **kwargs)

        subprocess.Popen.__init__ = no_console
    return home


def focus_page(win, log) -> None:
    """Give keyboard focus to the web page inside the window (Windows / WebView2)."""
    if os.name != "nt":
        return
    try:
        from System import Action  # pythonnet, bundled with pywebview on Windows

        form = win.native

        def activate():
            form.Activate()
            view = getattr(form, "webview", None) or getattr(getattr(form, "browser", None), "webview", None)
            if view is not None:
                view.Focus()

        form.Invoke(Action(activate))
    except Exception as exc:
        log.debug("could not focus the page: %s", exc)


def message_box(text: str, title: str = "ClipTool Editor") -> None:
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)
    else:
        print(f"{title}: {text}")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    home = setup_environment()
    from cliptool.config import load_config
    from cliptool.logger import get_logger, setup_logging

    cfg = load_config()
    setup_logging(cfg.resolve_dir("log_dir"))
    log = get_logger("desktop")
    if "--selftest" in argv:
        from cliptool.selftest import run
        return run(cfg, with_whisper="--with-whisper" in argv)

    from cliptool.editor.server import make_server
    from cliptool.ffmpeg_utils import FFmpegError, ffmpeg_bin
    try:
        ffmpeg_bin()
    except FFmpegError as exc:
        message_box(str(exc))
        return 2
    server = make_server(cfg, 8765)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("ClipTool Editor started (%s); data folder %s", url, home)
    try:
        import webview

        webview.settings["ALLOW_DOWNLOADS"] = True
        # Not maximized at creation: WebView2 starts asynchronously, and a window that opens
        # maximized could show the page without ever giving it keyboard focus (typing did
        # nothing until the window was re-focused). Maximize and focus once the page is loaded.
        win = webview.create_window("ClipTool Editor", url, width=1500, height=950, min_size=(1100, 680),
                                    background_color="#12131a", text_select=True)
        if not os.environ.get("CLIPTOOL_NO_FOCUS_FIX"):
            focused = []

            def on_loaded():
                if focused:
                    return
                focused.append(True)
                try:
                    win.maximize()
                except Exception as exc:
                    log.debug("maximize failed: %s", exc)
                focus_page(win, log)

            win.events.loaded += on_loaded
        icon = resource_dir() / "packaging" / "cliptool.ico"
        webview.start(private_mode=False, storage_path=str(home / "webview"),
                      icon=str(icon) if icon.exists() else None)
    except Exception as exc:  # no WebView2: fall back to the normal browser
        log.warning("App window unavailable (%s); opening the browser instead.", exc)
        log.debug(traceback.format_exc())
        webbrowser.open(url)
        message_box("ClipTool Editor is open in your web browser.\n\n"
                    "Keep this message open while you edit. Click OK to close ClipTool.")
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
