# -*- coding: utf-8 -*-
"""兼容垫片：终端安全管理 API 已迁至 zvplatform/routers/security.py（#16 模块化，2026-09-20）。

历史提交中的本文件（1010 行单体的全部实现）由 zvplatform/routers/security.py 逐字接替，
DB 接入统一走 zvplatform.db。新代码请直接从新模块导入。
"""

from zvplatform.routers.security import (  # noqa: F401
    router,
    ensure_security_tables,
    fmt_dt,
    get_db,
    mount_security_api,
)
