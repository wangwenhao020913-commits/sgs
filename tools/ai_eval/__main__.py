"""``python -m ai_eval``：薄启动器（与 ``tools/batch_games.py`` 同姿态）。

``PYTHONPATH=tools`` 是调用前提（``.venv`` 是 editable 只指 ``src/``）；漏写在前一行就
``No module named ai_eval`` 失败，不会静默跑到别的版本上。
"""

from __future__ import annotations

from .cli import main

raise SystemExit(main())
