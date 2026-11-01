"""Entry point PyInstaller turns into ClipTool-Editor.exe."""
import sys

from cliptool.desktop import main

if __name__ == "__main__":
    sys.exit(main())
