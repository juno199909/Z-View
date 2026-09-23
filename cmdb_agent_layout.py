# -*- coding: utf-8 -*-
"""兼容 shim：实现已迁入 zvagent.layout（V1.8.0 包化）。

保留本模块以兼容存量导入（updater、cmdb_agent_unified_v2、
cmdb_agent_core 的函数内懒加载）。新代码请直接 import zvagent.layout。

注意：运行期路径沙箱/测试需 patch zvagent.layout 的模块属性，
patch 本 shim 的重导出绑定不影响 zvagent.layout 内部函数的全局引用。
"""
from zvagent.layout import (  # noqa: F401
    SERVICE_NAME,
    PROGRAM_FILES_ROOT,
    PROGRAM_DATA_ROOT,
    VERSIONS_DIR,
    CURRENT_LINK,
    UPDATER_DIR,
    STAGING_DIR,
    CURRENT_VERSION_FILE,
    UPGRADE_HEALTH_FILE,
    UPGRADE_TASK_FILE,
    UPGRADE_LOCK_PATH,
    UPGRADE_STATE_PATH,
    UPGRADE_BAT_RESULT_PATH,
    is_v2_layout,
    active_version,
    write_current_version,
    active_exe,
    version_dir,
    service_query_running,
    service_stop,
    service_start,
    flip_current_junction,
    install_version_from_staging,
    prune_old_versions,
    write_upgrade_health,
    read_upgrade_health,
)
