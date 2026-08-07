"""Entry point for the bundled app (and for `python run_annotator.py`).

PyInstaller needs a plain script to start from; everything else lives in the
`cfu_annotator/` package next to this file.
"""

import multiprocessing
import sys

from cfu_annotator.main import main

if __name__ == "__main__":
    # Required in a frozen build: torch's DataLoader and anything else that
    # spawns a process would otherwise relaunch the whole GUI instead.
    multiprocessing.freeze_support()
    sys.exit(main())
