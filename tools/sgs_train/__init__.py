"""训练侧工具（`ai-view` 票 07 起）：离线估计用的采样与拟合。

本包**不进**发行包（与 ``tools/sgs_batch`` / ``tools/ai_eval`` 同层）：它跑进程 / 读 git，
而 ``src/sgs`` 的源码纪律禁这两样。公开入口只从子模块取（如
``sgs_train.likelihood``），本文件不 re-export，避免"包级 API"成为第二个真源。

``PYTHONPATH=tools`` 不可省（与 ``ai_eval`` 同一条）：``.venv`` 是 editable 只指 ``src/``，
漏写会 ``No module named sgs_train``——响亮失败，不是静默打错版本。
"""
