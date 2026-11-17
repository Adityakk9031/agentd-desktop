"""Entry point of the bundled runtime (agentd.exe inside the desktop installer)."""

import multiprocessing
import sys

from agentd.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
