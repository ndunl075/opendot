"""Entry point for the PyInstaller-built daemon sidecar that the desktop app ships (M3 task 3.6)."""

import multiprocessing
import sys

from opendot_core.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
