<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端详情</h2>
        <div class="zv-page-subtitle">{{ detail.asset?.hostname || '-' }} · {{ detail.asset?.ip_address || '-' }}</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="ArrowLeft" @click="$router.back()">返回</el-button>
        <el-button type="primary" :icon="VideoPlay" @click="openRemoteDesktop">远程控制</el-button>
      </div>
    </div>

    <!-- 终端概览 -->
    <div class="zv-card zv-overview">
      <div class="zv-overview-avatar" :style="{ background: getTypeGradient(detail.asset?.asset_type) }">
        <el-icon :size="32"><component :is="getTypeIcon(detail.asset?.asset_type)" /></el-icon>
      </div>
      <div class="zv-overview-info">
        <div class="zv-overview-name">
          {{ detail.asset?.hostname || '加载中' }}
          <span v-if="detail.asset?.status" class="zv-status-chip" :class="`is-${detail.asset.status}`">
            <span class="zv-status-dot" :class="`is-${detail.asset.status}`" />
            {{ getStatusText(detail.asset.status) }}
          </span>
        </div>
        <div class="zv-overview-meta">
          <span class="zv-mono">{{ detail.asset?.ip_address || '-' }}</span>
          <span>{{ detail.asset?.os_type }} {{ detail.asset?.os_version }}</span>
          <span>CPU {{ detail.asset?.cpu_cores }} 核</span>
          <span>{{ detail.asset?.memory_mb ? (detail.asset.memory_mb / 1024).toFixed(1) + ' GB' : '-' }} 内存</span>
          <span v-if="detail.asset?.agent_version">Agent v{{ detail.asset.agent_version }}</span>
        </div>
      </div>
      <div class="zv-overview-actions">
        <el-button :icon="RefreshRight" @click="rebootTerminalCmd" plain>重启</el-button>
        <el-button :icon="SwitchButton" @click="shutdownTerminal" plain type="danger">关机</el-button>
        <el-button :icon="Search" @click="runSecurityScan" plain :loading="scanLoading">安全体检</el-button>
      </div>
    </div>

    <!-- 实时状态 -->
    <div v-if="heartbeat" class="zv-card zv-card-pad">
      <h3 class="zv-section-title">
        <el-icon><Monitor /></el-icon>
        实时状态
      </h3>
      <div class="zv-metric-grid">
        <div class="zv-metric-box">
          <div class="zv-metric-ring" :style="metricRing(heartbeat.cpu_usage, $brand-primary)">
            <svg viewBox="0 0 60 60" class="zv-ring-svg">
              <circle cx="30" cy="30" r="26" fill="none" stroke="#e2e8f0" stroke-width="5" />
              <circle cx="30" cy="30" r="26" fill="none" stroke="#3b82f6" stroke-width="5" stroke-linecap="round"
                :stroke-dasharray="`${(heartbeat.cpu_usage || 0) * 1.63} 163`" transform="rotate(-90 30 30)" />
            </svg>
            <div class="zv-ring-num">{{ heartbeat.cpu_usage || 0 }}%</div>
          </div>
          <div class="zv-metric-label">CPU</div>
        </div>
        <div class="zv-metric-box">
          <div class="zv-metric-ring" :style="metricRing(heartbeat.memory_usage, $success)">
            <svg viewBox="0 0 60 60" class="zv-ring-svg">
              <circle cx="30" cy="30" r="26" fill="none" stroke="#e2e8f0" stroke-width="5" />
              <circle cx="30" cy="30" r="26" fill="none" stroke="#10b981" stroke-width="5" stroke-linecap="round"
                :stroke-dasharray="`${(heartbeat.memory_usage || 0) * 1.63} 163`" transform="rotate(-90 30 30)" />
            </svg>
            <div class="zv-ring-num">{{ heartbeat.memory_usage || 0 }}%</div>
          </div>
          <div class="zv-metric-label">内存</div>
        </div>
        <div class="zv-metric-box">
          <div class="zv-metric-ring" :style="metricRing(heartbeat.disk_usage, $warning)">
            <svg viewBox="0 0 60 60" class="zv-ring-svg">
              <circle cx="30" cy="30" r="26" fill="none" stroke="#e2e8f0" stroke-width="5" />
              <circle cx="30" cy="30" r="26" fill="none" stroke="#f59e0b" stroke-width="5" stroke-linecap="round"
                :stroke-dasharray="`${(heartbeat.disk_usage || 0) * 1.63} 163`" transform="rotate(-90 30 30)" />
            </svg>
            <div class="zv-ring-num">{{ heartbeat.disk_usage || 0 }}%</div>
          </div>
          <div class="zv-metric-label">磁盘</div>
        </div>
      </div>
    </div>

    <!-- 已安装软件 -->
    <div class="zv-card zv-card-pad" v-loading="softwareLoading">
      <h3 class="zv-section-title">
        <el-icon><Goods /></el-icon>
        已安装软件 <span class="zv-soft-count">({{ softwareList.length }})</span>
      </h3>
      <el-table :data="softwareList" :show-header="true" empty-text="暂无软件数据">
        <el-table-column prop="software_name" label="软件" min-width="200" />
        <el-table-column prop="version" label="版本" width="160" />
        <el-table-column prop="vendor" label="厂商" min-width="160" show-overflow-tooltip />
        <el-table-column prop="install_date" label="安装日期" width="140" />
        <el-table-column label="大小" width="100">
          <template #default="{ row }">{{ row.size_mb ? row.size_mb + ' MB' : '-' }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-drawer v-model="showRemoteDesktop" size="80%" :with-header="false" destroy-on-close>
      <WebRemoteDesktop
        v-if="showRemoteDesktop"
        :asset-id="currentAssetId"
        :ip-address="currentIpAddress"
        :hostname="currentHostname"
        @close="showRemoteDesktop = false"
      />
    </el-drawer>

    <!-- 安全体检结果 -->
    <el-dialog v-model="scanVisible" title="安全体检结果" width="640px" destroy-on-close>
      <div v-if="scanResult" class="zv-scan-result">
        <div v-if="scanResult.success === false" class="zv-scan-error">{{ scanResult.error || '扫描失败' }}</div>
        <template v-else>
          <div class="zv-scan-meta">扫描时间：{{ scanResult.scan_time || '-' }} · 进程数：{{ scanResult.process_count ?? '-' }}</div>
          <div class="zv-scan-section">
            <div class="zv-scan-title">防火墙状态</div>
            <pre class="zv-scan-pre">{{ formatScanSection(scanResult.firewall) }}</pre>
          </div>
          <div class="zv-scan-section">
            <div class="zv-scan-title">USB 存储策略</div>
            <pre class="zv-scan-pre">{{ formatScanSection(scanResult.usb) }}</pre>
          </div>
          <div class="zv-scan-section">
            <div class="zv-scan-title">启动项</div>
            <pre class="zv-scan-pre">{{ formatScanSection(scanResult.startup) }}</pre>
          </div>
          <div class="zv-scan-section">
            <div class="zv-scan-title">网络连接</div>
            <pre class="zv-scan-pre">{{ formatScanSection(scanResult.network) }}</pre>
          </div>
        </template>
      </div>
      <el-empty v-else description="暂无结果" :image-size="70" />
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, VideoPlay, RefreshRight, SwitchButton, Monitor, Goods,
  Box, Cpu, Share, Connection, Search
} from '@element-plus/icons-vue'
import { getAssetDetail } from '@/api/asset'
import { getInstalledSoftware } from '@/api/software'
import { rebootTerminal, shutdownTerminal as shutdownCmd } from '@/api/terminal'
import { remoteScan } from '@/api/security'
import WebRemoteDesktop from '@/components/WebRemoteDesktop.vue'

const route = useRoute()
const router = useRouter()
const detail = ref({})
const heartbeat = ref(null)
const softwareList = ref([])
const softwareLoading = ref(false)
const showRemoteDesktop = ref(false)
const scanLoading = ref(false)
const scanVisible = ref(false)
const scanResult = ref(null)

const TYPE_META = {
  server: { icon: Cpu,        gradient: 'linear-gradient(135deg, #3b82f6, #2563eb)' },
  pc:     { icon: Monitor,    gradient: 'linear-gradient(135deg, #10b981, #059669)' },
  switch: { icon: Connection, gradient: 'linear-gradient(135deg, #f59e0b, #d97706)' },
  router: { icon: Share,      gradient: 'linear-gradient(135deg, #8b5cf6, #7c3aed)' }
}
const getTypeIcon = (t) => TYPE_META[t]?.icon || Box
const getTypeGradient = (t) => TYPE_META[t]?.gradient || 'linear-gradient(135deg, #94a3b8, #64748b)'

const getStatusText = (s) => ({ online: '在线', offline: '离线', degraded: '降级', unknown: '未知' }[s] || s)
const metricRing = (val, color) => ({})

const openRemoteDesktop = () => { showRemoteDesktop.value = true }

// 安全体检：下发 security_scan，采集防火墙/USB/启动项/网络连接/进程态势（非病毒扫描）
const runSecurityScan = async () => {
  scanLoading.value = true
  try {
    const res = await remoteScan(route.params.id)
    scanResult.value = res?.result || res?.data || res || null
    scanVisible.value = true
  } catch (error) {
    ElMessage.error('安全体检下发失败（终端需在线）')
  } finally {
    scanLoading.value = false
  }
}

const formatScanSection = (section) => {
  if (section === null || section === undefined) return '-'
  if (typeof section === 'string') return section
  return JSON.stringify(section, null, 2)
}

const loadDetail = async () => {
  try {
    const data = await getAssetDetail(route.params.id)
    detail.value = data || {}
    heartbeat.value = data.heartbeat || null
  } catch (error) {
    ElMessage.error('加载终端详情失败')
  }
}

const loadSoftware = async () => {
  softwareLoading.value = true
  try {
    const res = await getInstalledSoftware(route.params.id, { limit: 200 })
    softwareList.value = res.data || []
  } catch (error) {
    console.error('加载软件列表失败', error)
  } finally {
    softwareLoading.value = false
  }
}

const rebootTerminalCmd = async () => {
  try {
    await ElMessageBox.confirm('确定要重启该终端吗？', '警告', { type: 'warning' })
    await rebootTerminal(route.params.id)
    ElMessage.success('重启指令已下发')
  } catch (e) { if (e !== 'cancel') ElMessage.error('下发失败') }
}

const shutdownTerminal = async () => {
  try {
    await ElMessageBox.confirm('确定要关闭该终端吗？', '警告', { type: 'warning' })
    await shutdownCmd(route.params.id)
    ElMessage.success('关机指令已下发')
  } catch (e) { if (e !== 'cancel') ElMessage.error('下发失败') }
}

onMounted(() => { loadDetail(); loadSoftware() })
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1400px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-overview {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 24px;
  margin-bottom: 16px;
  background: linear-gradient(135deg, $bg-card 0%, $slate-50 100%);
}

.zv-overview-avatar {
  width: 64px; height: 64px; border-radius: 16px;
  display: flex; align-items: center; justify-content: center;
  color: #fff;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.10);
  flex-shrink: 0;
}

.zv-overview-info { flex: 1; min-width: 0; }

.zv-overview-name {
  font-size: 22px; font-weight: 700; color: $text-primary;
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 8px;
}

.zv-overview-meta {
  display: flex; gap: 18px; font-size: 13px; color: $text-secondary;
  flex-wrap: wrap;
  > span { display: flex; align-items: center; }
}

.zv-overview-actions { display: flex; gap: 8px; }

.zv-card-pad { padding: 24px 26px; margin-bottom: 16px; }

.zv-section-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600; color: $text-primary;
  margin: 0 0 18px 0;
  padding-bottom: 12px;
  border-bottom: 1px solid $border-color-light;
  .el-icon { color: $brand-primary; }
}

.zv-soft-count { font-size: 13px; color: $text-tertiary; font-weight: 400; margin-left: 4px; }

.zv-metric-grid {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  @media (max-width: 800px) { grid-template-columns: 1fr; }
}

.zv-metric-box {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  padding: 20px; background: $slate-50; border-radius: $border-radius;
}

.zv-metric-ring { position: relative; width: 100px; height: 100px; }
.zv-ring-svg { width: 100%; height: 100%; }
.zv-ring-num {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px; font-weight: 700; color: $text-primary; font-family: $font-mono;
}

.zv-metric-label { font-size: 13px; color: $text-secondary; }

.zv-status-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 10px; border-radius: $radius-pill;
  font-size: 12px; font-weight: 500;
  background: $slate-50; color: $text-secondary;
  &.is-online  { background: rgba(16, 185, 129, 0.10); color: $success-color; }
  &.is-offline { background: rgba(239, 68, 68, 0.10); color: $danger-color; }
}
.zv-status-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

.zv-mono { font-family: $font-mono; font-size: 13px; }

.zv-scan-result { padding: 0 4px; }
.zv-scan-error { color: $danger-color; font-size: 13px; padding: 8px 0; }
.zv-scan-meta { font-size: 12px; color: $text-tertiary; margin-bottom: 12px; }
.zv-scan-section { margin-bottom: 14px; }
.zv-scan-title { font-size: 13px; font-weight: 600; color: $text-primary; margin-bottom: 6px; }
.zv-scan-pre {
  background: $slate-50;
  border-radius: $border-radius;
  padding: 10px 12px;
  font-family: $font-mono;
  font-size: 12px;
  color: $text-primary;
  max-height: 180px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell { background: #fafbfc; color: $text-secondary; font-weight: 600; font-size: 12px; }
  tr:hover > td.el-table__cell { background: rgba(37, 99, 235, 0.03) !important; }
  td.el-table__cell { border-bottom: 1px solid $slate-100 !important; }
  .el-table__inner-wrapper::before { height: 0; }
}
</style>
