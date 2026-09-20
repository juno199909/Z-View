# -*- coding: utf-8 -*-
<<<<<<< HEAD
"""Z-View Agent 包（V1.8.0 模块化）。

模块边界（渐进迁移自 cmdb_agent_unified_v2.py / cmdb_agent_core.py 两个单体）：
- layout.py   版本目录与 current junction 管理（V1.6.0 迁入）
- hygiene.py  临时文件/进程残留清理（V1.8.0 迁入）
- auth        设备凭据；upgrade 升级状态机；collectors 采集器；
              policy 策略执行；jobs 任务执行端（后续增量）
单体文件保留为各委托层，以兼容存量导入并供 frozen 打包入口。
"""

# Agent 版本号（与 cmdb_agent_core.AGENT_VERSION 同源，发布时 bump）
__version__ = "1.9.55"
=======
"""Z-View Agent 鍖咃紙V1.8.0 妯″潡鍖栵級銆?
妯″潡杈圭晫锛堟笎杩涜縼绉昏嚜 cmdb_agent_unified_v2.py / cmdb_agent_core.py 涓や釜鍗曚綋锛夛細
- layout.py   鐗堟湰鐩綍涓?current junction 绠＄悊锛圴1.6.0 杩佸叆锛?- hygiene.py  涓存椂鏂囦欢/杩涚▼娈嬬暀娓呯悊锛圴1.8.0 杩佸叆锛?
鍚庣画澧為噺锛歛uth锛堣澶囧嚟鎹級銆乽pgrade锛堝崌绾х姸鎬佹満锛夈€乧ollectors锛堥噰闆嗗櫒锛夈€?policy锛堢瓥鐣ユ墽琛岋級銆乯obs锛堜换鍔℃墽琛岀锛夈€傚崟浣撴枃浠朵繚鐣欎负钖勫鎵樺眰浠ュ吋瀹?瀛橀噺瀵煎叆涓?frozen 鎵撳寘鍏ュ彛銆?"""

# Agent 鐗堟湰鍙凤紙涓?cmdb_agent_core.AGENT_VERSION 鍚屾簮锛屽彂甯冩椂 bump锛?__version__ = "1.9.52"
>>>>>>> 5008f5d2d3812fb8acdbedcc26f6d151bad8f58b
