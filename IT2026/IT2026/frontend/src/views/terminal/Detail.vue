<template>
  <div class="terminal-detail">
    <el-page-header @back="goBack" title="返回">
      <template #content>
        <span class="page-title">终端详情</span>
      </template>
    </el-page-header>

    <!-- Web远程桌面组件 -->
    <WebRemoteDesktop
      :key="remoteDesktopKey"
      v-if="remoteDesktopMounted"
      :visible="showRemoteDesktop"
      :asset-id="route.params.id"
      :ip-address="detail.asset?.ip_address"
      :hostname="detail.asset?.hostname"
      @update:visible="handleRemoteDesktopVisibleChange"
      @close="handleRemoteDesktopClosed"
    />

    <!-- 远程Shell组件 -->
    <RemoteShell
      v-model="showShell"
      :asset-id="route.params.id"
      :ip-address="detail.asset?.ip_address"
      :hostname="detail.asset?.hostname"
    />

    <el-card class="box-card" v-loading="loading">
      <!-- 基本信息 -->
      <template #header>
        <div class="card-header">
          <span>
            <el-icon><Monitor /></el-icon>
            {{ detail.asset?.hostname || '未知设备' }}
          </span>
          <el-tag :type="getStatusType(detail.asset?.status)">
            {{ getStatusText(detail.asset?.status) }}
          </el-tag>
        </div>
      </template>

      <!-- 系统信息 -->
      <el-row :gutter="20" style="margin-bottom: 20px;">
        <el-col :span="12">
          <div class="info-section">
            <h3><el-icon><Platform /></el-icon> 系统信息</h3>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="主机名">
                {{ detail.asset?.hostname || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="IP地址">
                {{ detail.asset?.ip_address || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="MAC地址">
                {{ detail.asset?.mac_address || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="操作系统">
                {{ detail.asset?.os_type || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="系统版本">
                {{ detail.asset?.os_version || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="默认网关">
                {{ detail.asset?.gateway || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="DNS服务器" :span="2">
                <div v-if="parseDnsServers(detail.asset?.dns_servers).length > 0">
                  <el-tag v-for="(dns, index) in parseDnsServers(detail.asset?.dns_servers)" :key="index" size="small" style="margin-right: 5px;">
                    {{ dns }}
                  </el-tag>
                </div>
                <span v-else>-</span>
              </el-descriptions-item>
              <el-descriptions-item label="最后在线">
                {{ detail.asset?.last_seen || '-' }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-col>

        <el-col :span="12">
          <div class="info-section">
            <h3><el-icon><Cpu /></el-icon> 硬件信息</h3>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="CPU核心">
                {{ detail.asset?.cpu_cores || '-' }} 核
              </el-descriptions-item>
              <el-descriptions-item label="内存容量">
                {{ detail.asset?.memory_mb ? (detail.asset.memory_mb / 1024).toFixed(2) + ' GB' : '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="磁盘容量">
                {{ detail.asset?.disk_gb || '-' }} GB
              </el-descriptions-item>
              <el-descriptions-item label="制造商">
                {{ detail.asset?.manufacturer || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="型号">
                {{ detail.asset?.model || '-' }}
              </el-descriptions-item>
              <el-descriptions-item label="序列号">
                {{ detail.asset?.serial_number || '-' }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-col>
      </el-row>

      <!-- 实时状态 - 只在设备在线且有心跳数据时显示 -->
      <div class="info-section" v-if="detail.asset?.status === 'online' && detail.heartbeat">
        <h3><el-icon><DataLine /></el-icon> 实时状态</h3>
        <el-row :gutter="20">
          <el-col :span="8">
            <div class="stat-card">
              <div class="stat-label">CPU使用率</div>
              <el-progress
                :percentage="detail.heartbeat.cpu_usage || 0"
                :color="getProgressColor(detail.heartbeat.cpu_usage)"
              />
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-card">
              <div class="stat-label">内存使用率</div>
              <el-progress
                :percentage="detail.heartbeat.memory_usage || 0"
                :color="getProgressColor(detail.heartbeat.memory_usage)"
              />
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-card">
              <div class="stat-label">磁盘使用率</div>
              <el-progress
                :percentage="detail.heartbeat.disk_usage || 0"
                :color="getProgressColor(detail.heartbeat.disk_usage)"
              />
            </div>
          </el-col>
        </el-row>

        <!-- 分盘符磁盘信息 -->
        <div v-if="detail.heartbeat.disk_info && detail.heartbeat.disk_info.length > 0" style="margin-top: 20px;">
          <h4 style="margin-bottom: 10px; color: #606266;">磁盘分区详情</h4>
          <el-table :data="detail.heartbeat.disk_info" stripe size="small">
            <el-table-column prop="drive" label="盘符" width="100" />
            <el-table-column prop="fstype" label="文件系统" width="120" />
            <el-table-column label="总容量" width="120">
              <template #default="scope">
                {{ scope.row.total }} GB
              </template>
            </el-table-column>
            <el-table-column label="已使用" width="120">
              <template #default="scope">
                {{ scope.row.used }} GB
              </template>
            </el-table-column>
            <el-table-column label="可用" width="120">
              <template #default="scope">
                {{ scope.row.free }} GB
              </template>
            </el-table-column>
            <el-table-column label="使用率" width="200">
              <template #default="scope">
                <el-progress
                  :percentage="scope.row.percent"
                  :color="getProgressColor(scope.row.percent)"
                />
              </template>
            </el-table-column>
          </el-table>
        </div>

        <el-row :gutter="20" style="margin-top: 15px;">
          <el-col :span="12">
            <div class="info-item">
              <span class="label">登录用户:</span>
              <span class="value">{{ detail.heartbeat.logged_users || '-' }}</span>
            </div>
          </el-col>
          <el-col :span="12">
            <div class="info-item">
              <span class="label">进程数:</span>
              <span class="value">{{ detail.heartbeat.process_count || '-' }}</span>
            </div>
          </el-col>
        </el-row>
      </div>

      <!-- 软件清单 -->
      <div class="info-section">
        <h3>
          <el-icon><List /></el-icon>
          已安装软件 ({{ detail.software_list?.length || 0 }})
        </h3>

        <!-- 搜索框 -->
        <div style="margin-bottom: 15px; display: flex; gap: 10px; align-items: center;">
          <el-input
            v-model="softwareKeyword"
            placeholder="搜索软件名称、厂商、版本..."
            clearable
            style="width: 400px;"
            @clear="softwareKeyword = ''"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>

          <el-tag v-if="softwareKeyword" type="info">
            找到 {{ filteredSoftwareList.length }} 个结果
          </el-tag>
        </div>

        <!-- 软件表格 -->
        <el-table
          :data="filteredSoftwareList"
          stripe
          style="width: 100%"
          max-height="500"
          :default-sort="{ prop: 'software_name', order: 'ascending' }"
        >
          <el-table-column
            prop="software_name"
            label="软件名称"
            min-width="250"
            sortable
          >
            <template #default="scope">
              <span v-html="highlightKeyword(scope.row.software_name)"></span>
            </template>
          </el-table-column>
          <el-table-column
            prop="version"
            label="版本"
            width="150"
            sortable
          >
            <template #default="scope">
              <span v-html="highlightKeyword(scope.row.version)"></span>
            </template>
          </el-table-column>
          <el-table-column
            prop="vendor"
            label="厂商"
            width="180"
            show-overflow-tooltip
            sortable
          >
            <template #default="scope">
              <span v-html="highlightKeyword(scope.row.vendor)"></span>
            </template>
          </el-table-column>
          <el-table-column
            prop="install_date"
            label="安装日期"
            width="120"
            sortable
          />
        </el-table>

        <!-- 无结果提示 -->
        <el-empty
          v-if="softwareKeyword && filteredSoftwareList.length === 0"
          description="未找到匹配的软件"
          :image-size="80"
        />
      </div>

      <!-- 远程控制 -->
      <div class="info-section">
        <h3><el-icon><Operation /></el-icon> 远程控制</h3>

        <el-alert
          title="Web远程桌面"
          type="success"
          :closable="false"
          style="margin-bottom: 15px;"
        >
          <template #default>
            <div style="line-height: 1.8;">
              <p>✅ 基于WebSocket的实时远程桌面</p>
              <p>• 在浏览器中直接远程控制终端</p>
              <p>• 对方不锁屏，可以看到你的操作</p>
              <p>• 实时传输屏幕画面</p>
              <p style="color: #E6A23C; margin-top: 10px;">
                ⚠️ 连接会先经过平台代理，再转发到目标终端当前用户会话的远控组件
              </p>
            </div>
          </template>
        </el-alert>

        <el-row :gutter="15">
          <el-col :span="24">
            <el-card shadow="hover" class="remote-card">
              <template #header>
                <div class="card-title">
                  <el-icon color="#409EFF"><Monitor /></el-icon>
                  <span>Web远程桌面</span>
                </div>
              </template>
              <div class="card-content">
                <p>浏览器内直接远程控制，无需安装客户端</p>
                <p>目标: {{ detail.asset?.ip_address }}（经平台代理转发）</p>
                <p class="status" style="color: #67C23A;">⚡ 支持鼠标键盘控制，高清流畅</p>
              </div>
              <template #footer>
                <el-button type="primary" size="small" @click="openWebRemote" :icon="Monitor">
                  启动远程桌面
                </el-button>
              </template>
            </el-card>
          </el-col>
        </el-row>

        <el-divider />

        <div style="margin-top: 20px;">
          <h4 style="margin-bottom: 15px; color: #606266;">终端操作</h4>
          <el-space wrap>
            <el-button type="success" :icon="Setting" @click="remoteShell">
              远程Shell
            </el-button>
            <el-button type="info" :icon="Download" @click="collectInfo">
              收集信息
            </el-button>
          </el-space>
        </div>
      </div>

      <!-- 心跳历史 -->
      <div class="info-section" v-if="detail.heartbeat_history && detail.heartbeat_history.length > 0">
        <h3><el-icon><Timer /></el-icon> 心跳历史</h3>
        <el-table :data="detail.heartbeat_history" stripe>
          <el-table-column prop="heartbeat_time" label="时间" width="180" />
          <el-table-column label="CPU使用率" width="150">
            <template #default="scope">
              {{ scope.row.cpu_usage?.toFixed(1) || '-' }}%
            </template>
          </el-table-column>
          <el-table-column label="内存使用率" width="150">
            <template #default="scope">
              {{ scope.row.memory_usage?.toFixed(1) || '-' }}%
            </template>
          </el-table-column>
          <el-table-column label="磁盘使用率" width="150">
            <template #default="scope">
              {{ scope.row.disk_usage?.toFixed(1) || '-' }}%
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  Monitor, Platform, Cpu, DataLine, List, Search,
  Operation, Setting, Download, Timer
} from '@element-plus/icons-vue'
import { getAssetDetail, triggerAssetReport } from '@/api/asset'
import WebRemoteDesktop from '@/components/WebRemoteDesktop.vue'
import RemoteShell from '@/components/RemoteShell.vue'

const route = useRoute()
const router = useRouter()

const loading = ref(false)
const detail = ref({})
const softwareKeyword = ref('')
const remoteDesktopMounted = ref(false)
const showRemoteDesktop = ref(false)
const remoteDesktopKey = ref(0)
const showShell = ref(false)
let remoteDesktopUnmountTimer = null
let pendingRemoteDesktopOpen = false

const clearRemoteDesktopUnmountTimer = () => {
  if (remoteDesktopUnmountTimer) {
    clearTimeout(remoteDesktopUnmountTimer)
    remoteDesktopUnmountTimer = null
  }
}

const scheduleRemoteDesktopUnmount = () => {
  clearRemoteDesktopUnmountTimer()
  remoteDesktopUnmountTimer = setTimeout(() => {
    if (!showRemoteDesktop.value && !pendingRemoteDesktopOpen) {
      console.info('[remote-desktop][detail] force unmount fallback')
      remoteDesktopMounted.value = false
    }
  }, 600)
}

const mountRemoteDesktop = () => {
  console.info('[remote-desktop][detail] mount instance:', route.params.id, detail.value.asset?.ip_address)
  remoteDesktopKey.value += 1
  remoteDesktopMounted.value = true
  showRemoteDesktop.value = true
}

const handleRemoteDesktopVisibleChange = (visible) => {
  console.info('[remote-desktop][detail] visible change:', visible)
  showRemoteDesktop.value = visible
  if (visible) {
    clearRemoteDesktopUnmountTimer()
    return
  }
  if (!remoteDesktopMounted.value) {
    return
  }
  scheduleRemoteDesktopUnmount()
}

const handleRemoteDesktopClosed = () => {
  console.info('[remote-desktop][detail] closed event')
  clearRemoteDesktopUnmountTimer()
  showRemoteDesktop.value = false
  const shouldReopen = pendingRemoteDesktopOpen
  pendingRemoteDesktopOpen = false
  if (shouldReopen) {
    mountRemoteDesktop()
    return
  }
  remoteDesktopMounted.value = false
}

// 过滤软件列表（增强搜索）
const filteredSoftwareList = computed(() => {
  if (!detail.value.software_list) return []
  if (!softwareKeyword.value) return detail.value.software_list

  const keyword = softwareKeyword.value.toLowerCase()

  return detail.value.software_list.filter(software => {
    const name = (software.software_name || '').toLowerCase()
    const version = (software.version || '').toLowerCase()
    const vendor = (software.vendor || '').toLowerCase()

    // 搜索名称、版本、厂商
    return name.includes(keyword) ||
           version.includes(keyword) ||
           vendor.includes(keyword)
  })
})

// 高亮关键词
const highlightKeyword = (text) => {
  if (!text || !softwareKeyword.value) return text

  const keyword = softwareKeyword.value
  const regex = new RegExp(`(${keyword})`, 'gi')

  return text.replace(regex, '<span style="background: #ffe58f; color: #d48806; padding: 0 2px;">$1</span>')
}

// 加载终端详情
const loadDetail = async () => {
  loading.value = true
  try {
    const assetId = route.params.id
    const res = await getAssetDetail(assetId)
    detail.value = res
  } catch (error) {
    console.error('加载失败:', error)
    ElMessage.error('加载终端详情失败')
  } finally {
    loading.value = false
  }
}

// 返回
const goBack = () => {
  router.back()
}

// 状态显示
const getStatusType = (status) => {
  const map = {
    online: 'success',
    offline: 'danger',
    unknown: 'info'
  }
  return map[status] || 'info'
}

const getStatusText = (status) => {
  const map = {
    online: '在线',
    offline: '离线',
    unknown: '未知'
  }
  return map[status] || '未知'
}

// 进度条颜色
const getProgressColor = (percentage) => {
  if (percentage >= 90) return '#F56C6C'
  if (percentage >= 70) return '#E6A23C'
  return '#67C23A'
}

// 远程控制功能
const openWebRemote = () => {
  if (!detail.value.asset?.ip_address) {
    ElMessage.error('IP地址不可用')
    return
  }
  console.info('[remote-desktop][detail] open request:', route.params.id, detail.value.asset?.ip_address)
  clearRemoteDesktopUnmountTimer()
  pendingRemoteDesktopOpen = true

  if (remoteDesktopMounted.value || showRemoteDesktop.value) {
    console.info('[remote-desktop][detail] queue reopen after close:', route.params.id, detail.value.asset?.ip_address)
    showRemoteDesktop.value = false
    return
  }

  pendingRemoteDesktopOpen = false
  mountRemoteDesktop()
}

const parseDnsServers = (dnsServers) => {
  if (!dnsServers) return []
  try {
    return typeof dnsServers === 'string' ? JSON.parse(dnsServers) : dnsServers
  } catch {
    return []
  }
}

const remoteDesktop = () => {
  // RDP功能已移除，仅保留Web远程桌面
  ElMessage.info('请使用Web远程桌面功能')
}

const remoteShell = () => {
  console.log('打开远程Shell')
  showShell.value = true
}

const collectInfo = async () => {
  const assetId = route.params.id
  if (!assetId) {
    ElMessage.error('资产ID不可用')
    return
  }

  try {
    ElMessage({
      message: '正在触发Agent收集信息...',
      type: 'info',
      duration: 2000
    })

    const response = await triggerAssetReport(assetId)

    if (response?.success || response?.message || response?.asset_id) {
      ElMessage.success('信息收集完成！Agent已上报最新数据')
      setTimeout(() => {
        loadDetail()
      }, 2000)
    } else {
      ElMessage.warning('触发收集成功，但Agent响应异常')
    }
  } catch (error) {
    console.error('触发收集失败:', error)
    if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
      ElMessage.error('连接Agent超时，请确认设备在线且Agent正在运行')
    } else if (error.response?.status === 404) {
      ElMessage.error('Agent版本过旧，不支持立即收集功能')
    } else if (error.response?.status === 409) {
      ElMessage.error(error.response?.data?.detail || '目标终端当前不可执行该操作')
    } else {
      ElMessage.error('触发失败：平台代理无法连接到Agent')
    }
  }
}

onMounted(() => {
  loadDetail()
})
</script>

<style scoped>
.terminal-detail {
  padding: 20px;
}

.page-title {
  font-size: 18px;
  font-weight: bold;
}

.box-card {
  margin-top: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 16px;
  font-weight: bold;
}

.info-section {
  margin-bottom: 30px;
}

.info-section h3 {
  font-size: 16px;
  margin-bottom: 15px;
  color: #409EFF;
  display: flex;
  align-items: center;
  gap: 8px;
}

.stat-card {
  padding: 15px;
  background: #f5f7fa;
  border-radius: 4px;
}

.stat-label {
  margin-bottom: 10px;
  color: #606266;
  font-size: 14px;
}

.info-item {
  padding: 10px;
  background: #f5f7fa;
  border-radius: 4px;
}

.info-item .label {
  color: #909399;
  margin-right: 10px;
}

.info-item .value {
  color: #303133;
  font-weight: bold;
}

.remote-card {
  height: 100%;
  cursor: pointer;
  transition: transform 0.3s;
}

.remote-card:hover {
  transform: translateY(-5px);
}

.remote-card .card-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: bold;
}

.remote-card .card-content {
  min-height: 80px;
  padding: 10px 0;
}

.remote-card .card-content p {
  margin: 5px 0;
  color: #606266;
  font-size: 13px;
}

.remote-card .status {
  color: #909399;
  font-size: 12px;
}

.remote-card .device-code {
  color: #409EFF;
  font-size: 12px;
  font-weight: bold;
}
</style>
