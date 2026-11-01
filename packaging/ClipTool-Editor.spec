# PyInstaller recipe for ClipTool-Editor.exe.  Build with:  pyinstaller packaging/ClipTool-Editor.spec
# Put ffmpeg.exe + ffprobe.exe in packaging/ffmpeg/ first to bundle them (the CI build does this).
import os
import sys

from PyInstaller.utils.hooks import collect_all

HERE = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(HERE)

datas = [
    (os.path.join(ROOT, "cliptool", "editor", "static"), os.path.join("cliptool", "editor", "static")),
    (os.path.join(ROOT, "CHANGELOG.md"), "."),
    (os.path.join(ROOT, "config.example.yaml"), "."),
    (os.path.join(HERE, "cliptool.ico"), "packaging"),
    (os.path.join(HERE, "cliptool.png"), "packaging"),
]
ffmpeg_dir = os.path.join(HERE, "ffmpeg")
if os.path.isdir(ffmpeg_dir):
    datas.append((ffmpeg_dir, "ffmpeg"))

binaries, hiddenimports = [], ["cliptool.editor.server", "cliptool.selftest", "cliptool.desktop"]
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "av", "huggingface_hub", "webview"):
    try:
        d, b, h = collect_all(pkg)
    except Exception as exc:  # optional on some platforms
        print(f"collect_all({pkg}) skipped: {exc}")
        continue
    datas += d
    binaries += b
    hiddenimports += h
if sys.platform == "win32":
    hiddenimports += ["clr", "clr_loader", "webview.platforms.winforms", "webview.platforms.edgechromium"]

a = Analysis(
    [os.path.join(HERE, "desktop_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["cv2", "torch", "transformers", "pyannote", "matplotlib", "tkinter", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClipTool-Editor",
    console=False,
    icon=os.path.join(HERE, "cliptool.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="ClipTool-Editor")
