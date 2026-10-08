"""``python -m sgs`` 入口，与 console script ``sgs`` 同源。"""

from sgs.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
