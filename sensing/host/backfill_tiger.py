"""Load labelled recordings made earlier into Tiger Data.

    python backfill_tiger.py data/*_rx*.log

Any recorder's sessions (booth, breathing, zones, webcam-labelled); one log per
board, <session>_rx<board>.log. New recordings upload themselves when they finish;
this is for older ones, or to retry after the database was unreachable.
What goes where: see tiger_upload.py.
"""

from __future__ import annotations

import argparse

from tiger_upload import upload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    upload(parser.parse_args().recordings)


if __name__ == "__main__":
    main()
