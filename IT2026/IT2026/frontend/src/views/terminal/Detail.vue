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

    <!-- 实时监控 + 资产档案 -->
    <el-tabs v-model="activeTab" class="zv-detail-tabs">
      <el-tab-pane label="实时监控" name="monitor">
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
      </el-tab-pane>

      <el-tab-pane label="资产档案" name="asset" lazy>
        <AssetInfoPanel :asset-id="currentAssetId" :auto-edit="route.query.edit === 'true' || route.query.edit === '1'" />
      </el-tab-pane>

      <el-tab-pane label="网络" name="network" lazy>
        <NetworkPanel :asset-id="currentAssetId" />
      </el-tab-pane>
    </el-tabs>

    <el-drawer v-model="showRemoteDesktop" size="80%" :with-header="false" destroy-on-close>
      <WebRemoteDesktop
        v-if="showRemoteDesktop"
        :asset-id="currentAssetId"
        :ip-address="currentIpAddress"
        :hostname="currentHostname"
        @close="showRemoteDesktop = false"
      />
    </el-drawer>

    <!-- 安全体检报告 -->
    <el-dialog v-model="scanVisible" title="安全体检报告" width="780px" destroy-on-close>
      <div v-if="scanResult" class="zv-scan">
        <div v-if="scanResult.success === false" class="zv-scan-fail">
          <el-icon :size="22"><WarningFilled /></el-icon>
          <span>{{ scanResult.error || '扫描失败，终端需在线' }}</span>
        </div>
        <template v-else>
          <div class="zv-scan-banner" :class="`is-${scanOverall.level}`">
            <el-icon :size="28"><component :is="scanOverall.icon" /></el-icon>
            <div class="zv-scan-banner-main">
              <div class="zv-scan-banner-title">{{ scanOverall.title }}</div>
              <div class="zv-scan-banner-sub">{{ scanOverall.subtitle }}</div>
            </div>
            <div class="zv-scan-banner-meta">
              <span>扫描时间 {{ scanResult.scan_time || '-' }}</span>
              <span>进程采样 {{ scanResult.process_count ?? '-' }} 个</span>
            </div>
          </div>

          <div class="zv-scan-grid">
            <div v-for="card in scanCards" :key="card.title" class="zv-scan-card" :class="`is-${card.level}`">
              <div class="zv-scan-card-head">
                <el-icon><component :is="card.icon" /></el-icon>
                <span>{{ card.title }}</span>
              </div>
              <div class="zv-scan-card-state">{{ card.state }}</div>
              <div class="zv-scan-card-note">{{ card.note }}</div>
            </div>
          </div>

          <div class="zv-scan-block">
            <div class="zv-scan-block-title">防火墙配置文件</div>
            <div class="zv-scan-profiles">
              <div v-for="p in scanFirewallProfiles" :key="p.key" class="zv-scan-profile">
                <span class="zv-scan-profile-name">{{ p.label }}</span>
                <el-tag :type="p.enabled ? 'success' : 'danger'" size="small" round effect="light">
                  {{ p.enabled ? '已开启' : '已关闭' }}
                </el-tag>
              </div>
              <div v-if="!scanFirewallProfiles.length" class="zv-scan-empty">未获取到配置文件明细，请查看原始数据</div>
            </div>
          </div>

          <div class="zv-scan-block">
            <div class="zv-scan-block-title">开机启动项（{{ scanStartup.length }}）</div>
            <el-table :data="scanStartup" size="small" max-height="200" empty-text="无启动项">
              <el-table-column label="来源" width="100">
                <template #default="{ row }">
                  <el-tag size="small" effect="plain" :type="row.source === 'HKLM' ? 'info' : 'primary'">
                    {{ row.source === 'HKLM' ? '系统级' : '当前用户' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
              <el-table-column prop="value" label="程序命令" min-width="280" show-overflow-tooltip>
                <template #default="{ row }"><span class="zv-mono">{{ row.value }}</span></template>
              </el-table-column>
            </el-table>
          </div>

          <div class="zv-scan-block">
            <div class="zv-scan-block-title">
              网络连接（{{ scanConnections.length }}）· 含外部连接
              <span :class="scanExternalCount ? 'zv-scan-ext-num is-warn' : 'zv-scan-ext-num'">{{ scanExternalCount }}</span> 个
            </div>
            <el-table :data="scanConnections" size="small" max-height="260" empty-text="无活动连接">
              <el-table-column label="远程地址" min-width="200">
                <template #default="{ row }">
                  <span class="zv-mono">{{ row.remote }}</span>
                  <el-tag v-if="isExternalIp(row.remote)" type="warning" size="small" effect="light" style="margin-left: 6px">外网</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="本地地址" min-width="180">
                <template #default="{ row }"><span class="zv-mono">{{ row.local }}</span></template>
              </el-table-column>
              <el-table-column label="状态" width="120">
                <template #default="{ row }">
                  <el-tag size="small" effect="light" :type="connTagType(row.status)">{{ connStatusText(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="pid" label="PID" width="80" />
            </el-table>
          </div>

          <el-collapse class="zv-scan-raw">
            <el-collapse-item title="查看原始数据" name="raw">
              <pre class="zv-scan-pre">{{ scanRawText }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
      <el-empty v-else description="暂无结果" :image-size="70" />
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, VideoPlay, RefreshRight, SwitchButton, Monitor, Goods,
  Box, Cpu, Share, Connection, Search, Key, List, Link,
  CircleCheckFilled, WarningFilled
} from '@element-plus/icons-vue'
import { getAssetDetail } from '@/api/asset'
import { getInstalledSoftware } from '@/api/software'
import { rebootTerminal, shutdownTerminal as shutdownCmd } from '@/api/terminal'
import { remoteScan } from '@/api/security'
import WebRemoteDesktop from '@/components/WebRemoteDesktop.vue'
import AssetInfoPanel from '@/views/asset/components/AssetInfoPanel.vue'
import NetworkPanel from '@/views/terminal/components/NetworkPanel.vue'

const route = useRoute()
const router = useRouter()
const activeTab = ref('monitor')
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

// ===== 安全体检报告 =====
const PROFILE_LABELS = {
  domain: '域网络（公司环境）',
  private: '专用网络（家庭 / 办公室）',
  public: '公用网络（公共场所）',
  '域配置文件': '域网络（公司环境）',
  '专用配置文件': '专用网络（家庭 / 办公室）',
  '公用配置文件': '公用网络（公共场所）',
}

const isPrivateAddr = (addr) => {
  const ip = String(addr || '').replace(/^\[?/, '').split(']')[0].split(':')[0]
  return (
    ip === '::1' || /^fe80:/i.test(ip) || /^f[cd][0-9a-f]{2}:/i.test(ip) ||
    /^(127\.|10\.|192\.168\.|169\.254\.)/.test(ip) ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(ip)
  )
}
const isExternalIp = (addr) => {
  const ip = String(addr || '').split(':')[0]
  return !!ip && !isPrivateAddr(addr)
}
const connStatusText = (s) => ({
  ESTABLISHED: '已建立', LISTEN: '监听中', TIME_WAIT: '等待关闭',
  CLOSE_WAIT: '待关闭', SYN_SENT: '连接中', FIN_WAIT1: '关闭中', FIN_WAIT2: '关闭中',
}[s] || s || '-')
const connTagType = (s) => (s === 'ESTABLISHED' ? 'success' : (['TIME_WAIT', 'CLOSE_WAIT'].includes(s) ? 'info' : 'warning'))

const scanFirewall = computed(() => scanResult.value?.firewall || {})
const scanFirewallProfiles = computed(() => {
  const fw = scanFirewall.value
  let profiles = fw.profiles || {}
  if (!Object.keys(profiles).length && fw.raw) {
    // 兜底：旧版 Agent 解析失败时，从 netsh 原文提取各配置文件状态
    profiles = {}
    const re = /(域配置文件|专用配置文件|公用配置文件|Domain|Private|Public)[\s\S]{0,120}?(?:状态|State)\s*(启用|关闭|ON|OFF)/gi
    let m
    while ((m = re.exec(fw.raw)) !== null) {
      const zhMap = { '域配置文件': 'domain', '专用配置文件': 'private', '公用配置文件': 'public' }
      const key = zhMap[m[1]] || m[1].toLowerCase()
      const stateStr = m[2]
      profiles[key] = (stateStr === '启用' || stateStr === 'ON') ? 'enabled' : 'disabled'
    }
  }
  return Object.entries(profiles).map(([key, val]) => ({
    key,
    label: PROFILE_LABELS[key] || key,
    enabled: val === 'enabled' || val === true || val === 'ON',
  }))
})
const scanStartup = computed(() => scanResult.value?.startup?.startup_items || [])
const scanConnections = computed(() => scanResult.value?.network?.connections || [])
const scanExternalCount = computed(() => scanConnections.value.filter(c => isExternalIp(c.remote)).length)

const scanCards = computed(() => {
  const r = scanResult.value || {}
  const fw = r.firewall || {}
  const usb = r.usb || {}
  const resolvedProfiles = scanFirewallProfiles.value
  const fwKnown = resolvedProfiles.length > 0 || fw.any_enabled !== undefined
  const fwEnabledCount = resolvedProfiles.filter(p => p.enabled).length
  const fwEnabled = resolvedProfiles.length ? fwEnabledCount === resolvedProfiles.length : !!fw.any_enabled
  const fwPartially = resolvedProfiles.length > 0 && fwEnabledCount > 0 && fwEnabledCount < resolvedProfiles.length
  const startupCount = r.startup?.count ?? scanStartup.value.length

  const cards = [
    {
      key: 'firewall',
      title: '防火墙',
      icon: Lock,
      level: !fwKnown ? 'unknown' : (fwEnabled ? 'good' : (fwPartially ? 'warn' : 'bad')),
      state: !fwKnown
        ? '未知'
        : (fwEnabled ? '已开启' : (fwPartially ? `部分开启（${fwEnabledCount}/${resolvedProfiles.length}）` : '已关闭')),
      note: !fwKnown
        ? '未获取到防火墙状态'
        : (fwEnabled
          ? '可有效拦截外部入站访问'
          : (fwPartially ? '部分网络环境下防火墙未生效，存在暴露面' : '终端暴露于网络攻击风险，建议尽快开启')),
    },
    {
      key: 'usb',
      title: 'USB 存储管控',
      icon: Key,
      level: usb.success ? (usb.usb_storage_blocked ? 'good' : 'warn') : 'unknown',
      state: !usb.success ? '未知' : (usb.usb_storage_blocked ? '已禁止' : '未管控'),
      note: !usb.success
        ? (usb.error || '未获取到 USB 管控状态')
        : (usb.usb_storage_blocked ? 'USB 存储设备无法在终端使用' : 'USB 存储可随意拷贝文件，建议在策略中心开启管控'),
    },
    {
      key: 'startup',
      title: '开机启动项',
      icon: List,
      level: startupCount > 10 ? 'warn' : 'good',
      state: `${startupCount} 个`,
      note: startupCount > 10 ? '启动项偏多，可能拖慢开机速度' : '开机自启数量正常',
    },
    {
      key: 'network',
      title: '外部网络连接',
      icon: Link,
      level: scanExternalCount.value > 0 ? 'warn' : 'good',
      state: `${scanExternalCount.value} 个外联`,
      note: scanExternalCount.value
        ? '已标注外网地址，请确认是否为已知业务'
        : '未发现与公网的活动连接',
    },
  ]
  return cards
})

const scanOverall = computed(() => {
  const cards = scanCards.value
  const bad = cards.filter(c => c.level === 'bad')
  const warn = cards.filter(c => c.level === 'warn')
  if (bad.length) {
    return {
      level: 'bad',
      icon: WarningFilled,
      title: `发现 ${bad.length + warn.length} 项安全风险`,
      subtitle: bad.concat(warn).map(c => c.title).join('、') + ' 需要处理',
    }
  }
  if (warn.length) {
    return {
      level: 'warn',
      icon: WarningFilled,
      title: `基本正常，${warn.length} 项建议关注`,
      subtitle: warn.map(c => `${c.title}：${c.note}`).join('；'),
    }
  }
  return {
    level: 'good',
    icon: CircleCheckFilled,
    title: '安全状态良好',
    subtitle: '防火墙已开启、USB 存储已管控，未发现明显风险',
  }
})

const scanRawText = computed(() => {
  const r = scanResult.value || {}
  const fw = r.firewall || {}
  return [
    '【防火墙】', fw.raw || JSON.stringify(fw, null, 2),
    '', '【USB 管控】', JSON.stringify(r.usb ?? {}, null, 2),
    '', '【启动项】', JSON.stringify(r.startup ?? {}, null, 2),
    '', '【网络连接】', JSON.stringify(r.network ?? {}, null, 2),
  ].join('\n')
})

const openRemoteDesktop = () => { showRemoteDesktop.value = true }

// 远控组件所需的终端标识与地址（供工具栏显示）
const currentAssetId = route.params.id
const currentIpAddress = computed(() => detail.value?.asset?.ip_address || '')
const currentHostname = computed(() => detail.value?.asset?.hostname || '')

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

// ===== 安全体检报告 =====
.zv-scan { padding: 0 4px; }

.zv-scan-fail {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 16px; border-radius: $border-radius;
  background: $danger-bg; color: $danger-color; font-size: 13px;
}

.zv-scan-banner {
  display: flex; align-items: center; gap: 14px;
  padding: 18px 20px; border-radius: $border-radius-lg; margin-bottom: 16px;
  &.is-good { background: $success-bg; color: #047857; }
  &.is-warn { background: $warning-bg; color: #b45309; }
  &.is-bad  { background: $danger-bg; color: $danger-color; }
}

.zv-scan-banner-main { flex: 1; min-width: 0; }

.zv-scan-banner-title { font-size: 17px; font-weight: 700; line-height: 1.3; }

.zv-scan-banner-sub {
  font-size: 12px; margin-top: 4px; opacity: 0.85;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.zv-scan-banner-meta {
  display: flex; flex-direction: column; gap: 4px;
  font-size: 12px; text-align: right; opacity: 0.75; white-space: nowrap;
}

.zv-scan-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
  margin-bottom: 18px;
  @media (max-width: 760px) { grid-template-columns: repeat(2, 1fr); }
}

.zv-scan-card {
  padding: 14px 16px; border-radius: $border-radius;
  background: $bg-card; border: 1px solid $border-color-light;
  &.is-good { border-color: rgba(16, 185, 129, 0.35); }
  &.is-warn { border-color: rgba(245, 158, 11, 0.45); }
  &.is-bad  { border-color: rgba(239, 68, 68, 0.45); }
}

.zv-scan-card-head {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: $text-secondary; margin-bottom: 8px;
  .el-icon { font-size: 14px; }
  .is-good & { color: $success-color; }
  .is-warn & { color: $warning-color; }
  .is-bad & { color: $danger-color; }
  .is-unknown & { color: $text-tertiary; }
}

.zv-scan-card-state { font-size: 18px; font-weight: 700; color: $text-primary; }

.zv-scan-card-note {
  font-size: 11px; color: $text-tertiary; margin-top: 4px; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

.zv-scan-block { margin-bottom: 16px; }

.zv-scan-block-title {
  font-size: 13px; font-weight: 600; color: $text-primary; margin-bottom: 8px;
}

.zv-scan-ext-num { font-weight: 700; &.is-warn { color: $warning-color; } }

.zv-scan-profiles {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
  @media (max-width: 700px) { grid-template-columns: 1fr; }
}

.zv-scan-profile {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 10px 14px; border-radius: $border-radius;
  background: $slate-50; font-size: 12px;
}

.zv-scan-profile-name { color: $text-secondary; }

.zv-scan-empty { font-size: 12px; color: $text-tertiary; padding: 8px 0; }

.zv-scan-raw { margin-top: 4px; }

.zv-scan-pre {
  background: $slate-50;
  border-radius: $border-radius;
  padding: 10px 12px;
  font-family: $font-mono;
  font-size: 12px;
  color: $text-primary;
  max-height: 220px;
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
