<template>
  <div class="zv-net">
    <div v-if="live === null && loading" class="zv-net-loading" v-loading="loading" element-loading-text="加载网络状态..." style="min-height: 220px" />
    <el-empty v-else-if="!live" description="暂无网络数据（需终端在线并升级至支持网络监控的 Agent 版本）" :image-size="80" />
    <template v-else>
      <!-- 状态 + 实时流量 + 质量 -->
      <el-row :gutter="16" class="zv-net-top">
        <el-col :xs="24" :sm="8">
          <div class="zv-net-card">
            <div class="zv-net-card-title">网络状态</div>
            <div class="zv-net-status">
              <span class="zv-net-dot" :class="primaryUp ? 'is-up' : 'is-down'" />
              <span class="zv-net-status-text">{{ primaryUp ? 'Connected' : 'Disconnected' }}</span>
            </div>
            <div class="zv-net-status-detail">
              <div class="zv-net-iface">{{ primary?.interface_name || primaryInterfaceLive?.interface_name || '-' }}</div>
              <div class="zv-net-muted">{{ primary?.description || '-' }}</div>
              <div class="zv-net-kv"><span>IPv4</span><span class="zv-mono">{{ primary?.ipv4 || '-' }}</span></div>
              <div class="zv-net-kv"><span>网关</span><span class="zv-mono">{{ primary?.gateway || '-' }}</span></div>
              <div class="zv-net-kv"><span>链路</span><span>{{ formatLinkSpeed(primary?.link_speed) }}</span></div>
            </div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="8">
          <div class="zv-net-card">
            <div class="zv-net-card-title">实时流量</div>
            <div class="zv-net-speed">
              <div class="zv-net-speed-item">
                <el-icon class="is-down"><Download /></el-icon>
                <div>
                  <div class="zv-net-speed-num">{{ formatSpeed(live.rx_rate) }}</div>
                  <div class="zv-net-speed-label">下载</div>
                </div>
              </div>
              <div class="zv-net-speed-item">
                <el-icon class="is-up"><Upload /></el-icon>
                <div>
                  <div class="zv-net-speed-num">{{ formatSpeed(live.tx_rate) }}</div>
                  <div class="zv-net-speed-label">上传</div>
                </div>
              </div>
            </div>
            <div class="zv-net-kv"><span>累计下载</span><span>{{ formatBytes(primaryLive?.rx_bytes) }}</span></div>
            <div class="zv-net-kv"><span>累计上传</span><span>{{ formatBytes(primaryLive?.tx_bytes) }}</span></div>
            <div class="zv-net-muted" style="margin-top: 6px">数据时间 {{ live.reported_at || '-' }}<template v-if="live.stale">（已过期）</template></div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="8">
          <div class="zv-net-card">
            <div class="zv-net-card-title">网络质量</div>
            <div class="zv-net-kv"><span>网关 RTT</span><span>{{ formatRtt(quality.gateway_rtt_ms) }}</span></div>
            <div class="zv-net-kv"><span>DNS 响应</span><span>{{ formatRtt(quality.dns_rtt_ms) }}</span></div>
            <div class="zv-net-kv"><span>Internet RTT</span><span>{{ formatRtt(quality.internet_rtt_ms) }}</span></div>
            <div class="zv-net-kv">
              <span>丢包率</span>
              <span :class="qualityHighLoss ? 'zv-net-loss-warn' : ''">{{ lossText }}</span>
            </div>
          </div>
        </el-col>
      </el-row>

      <!-- 流量趋势 -->
      <div class="zv-net-card zv-net-chart-card">
        <div class="zv-net-chart-head">
          <div class="zv-net-card-title" style="margin-bottom: 0">流量趋势</div>
          <el-radio-group v-model="range" size="small" @change="loadHistory">
            <el-radio-button value="5m">5 分钟</el-radio-button>
            <el-radio-button value="15m">15 分钟</el-radio-button>
            <el-radio-button value="1h">1 小时</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="historySeries.length" class="zv-net-chart">
          <VChart :option="trendOption" autoresize />
        </div>
        <el-empty v-else description="暂无历史数据（每分钟聚合一次，需终端持续在线）" :image-size="60" />
      </div>

      <!-- 全部网卡（高级信息） -->
      <div class="zv-net-card">
        <div class="zv-net-card-title">网卡列表（{{ interfaces.length }}）</div>
        <el-table :data="interfaces" size="small" max-height="260">
          <el-table-column prop="interface_name" label="名称" min-width="150" show-overflow-tooltip />
          <el-table-column label="类型" width="90">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.is_primary ? 'primary' : 'info'">{{ ifaceTypeText(row.interface_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <span class="zv-net-dot-sm" :class="row.status === 'up' ? 'is-up' : 'is-down'" />
              {{ row.status === 'up' ? '已连接' : '断开' }}
            </template>
          </el-table-column>
          <el-table-column prop="ipv4" label="IPv4" min-width="130">
            <template #default="{ row }"><span class="zv-mono">{{ row.ipv4 || '-' }}</span></template>
          </el-table-column>
          <el-table-column prop="gateway" label="网关" min-width="130">
            <template #default="{ row }"><span class="zv-mono">{{ row.gateway || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="链路" width="100">
            <template #default="{ row }">{{ formatLinkSpeed(row.link_speed) }}</template>
          </el-table-column>
          <el-table-column prop="mac_address" label="MAC" min-width="150">
            <template #default="{ row }"><span class="zv-mono">{{ row.mac_address || '-' }}</span></template>
          </el-table-column>
        </el-table>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Download, Upload } from '@element-plus/icons-vue'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { getAssetNetwork, getAssetNetworkHistory } from '@/api/network'

use([CanvasRenderer, LineChart, GridComponent, LegendComponent, TooltipComponent])

const props = defineProps({
  assetId: { type: [String, Number], required: true },
})

const live = ref(null)
const historySeries = ref([])
const range = ref('15m')
const loading = ref(false)
let pollTimer = null

const quality = computed(() => live.value?.quality || {})
const interfaces = computed(() => live.value?.interfaces || [])
const primaryUp = computed(() => {
  const primary = interfaces.value.find(i => i.is_primary) ||
    interfaces.value.find(i => i.interface_name === live.value?.primary_interface)
  return primary ? primary.status === 'up' : false
})
const primary = computed(() =>
  interfaces.value.find(i => i.is_primary) ||
  interfaces.value.find(i => i.interface_name === live.value?.primary_interface) ||
  null
)
const primaryInterfaceLive = computed(() => {
  const list = live.value?.interfaces_live || []
  const name = live.value?.primary_interface
  return list.find(i => i.interface_name === name) || null
})
const primaryLive = computed(() => primaryInterfaceLive.value || primary.value || {})
const qualityHighLoss = computed(() => Number(quality.value.packet_loss_pct || 0) >= 10)
const lossText = computed(() => {
  const v = quality.value.packet_loss_pct
  return v === null || v === undefined ? '-' : `${v}%`
})

const formatSpeed = (bytesPerSec) => {
  const v = Number(bytesPerSec)
  if (!Number.isFinite(v)) return '-'
  if (v >= 1024 * 1024) return `${(v / 1024 / 1024).toFixed(1)} MB/s`
  if (v >= 1024) return `${(v / 1024).toFixed(1)} KB/s`
  return `${Math.round(v)} B/s`
}
const formatBytes = (bytes) => {
  const v = Number(bytes)
  if (!Number.isFinite(v)) return '-'
  if (v >= 1024 ** 3) return `${(v / 1024 ** 3).toFixed(1)} GB`
  if (v >= 1024 ** 2) return `${(v / 1024 ** 2).toFixed(1)} MB`
  if (v >= 1024) return `${(v / 1024).toFixed(1)} KB`
  return `${v} B`
}
const formatLinkSpeed = (mbps) => {
  const v = Number(mbps)
  if (!Number.isFinite(v) || v <= 0) return '-'
  if (v >= 1000) return `${(v / 1000).toFixed(v % 1000 ? 1 : 0)} Gbps`
  return `${v} Mbps`
}
const formatRtt = (ms) => (Number.isFinite(Number(ms)) && ms !== null ? `${ms} ms` : '-')
const ifaceTypeText = (t) => ({ ethernet: '以太网', wifi: 'Wi-Fi', vpn: 'VPN', virtual: '虚拟', other: '其他' }[t] || t || '-')

const trendUnit = computed(() => {
  let maxV = 0
  for (const p of historySeries.value) {
    maxV = Math.max(maxV, Number(p.rx_rate) || 0, Number(p.tx_rate) || 0)
  }
  if (maxV >= 1024 * 1024) return { name: 'MB/s', div: 1024 * 1024, digits: 2 }
  if (maxV >= 1024) return { name: 'KB/s', div: 1024, digits: 1 }
  return { name: 'B/s', div: 1, digits: 0 }
})

const trendOption = computed(() => {
  const unit = trendUnit.value
  const conv = (v) => +(((v || 0) / unit.div).toFixed(unit.digits))
  return {
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => `${v} ${unit.name}`,
    },
    legend: { data: ['下载', '上传'], top: 0, textStyle: { fontSize: 12 } },
    grid: { left: 64, right: 16, top: 32, bottom: 28 },
    xAxis: {
      type: 'category',
      data: historySeries.value.map(p => p.ts),
      axisLabel: { fontSize: 11, interval: Math.max(0, Math.floor(historySeries.value.length / 8)) },
      axisLine: { lineStyle: { color: '#cbd5e1' } },
    },
    yAxis: {
      type: 'value',
      name: unit.name,
      axisLabel: { fontSize: 11 },
      splitLine: { lineStyle: { color: '#f1f5f9' } },
    },
    series: [
      {
        name: '下载',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: historySeries.value.map(p => conv(p.rx_rate)),
        lineStyle: { width: 2, color: '#3b82f6' },
        areaStyle: { color: 'rgba(59, 130, 246, 0.10)' },
      },
      {
        name: '上传',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: historySeries.value.map(p => conv(p.tx_rate)),
        lineStyle: { width: 2, color: '#10b981' },
        areaStyle: { color: 'rgba(16, 185, 129, 0.10)' },
      },
    ],
  }
})

const loadLive = async () => {
  try {
    live.value = await getAssetNetwork(props.assetId)
  } catch (error) {
    live.value = live.value === null ? null : live.value
  }
}
const loadHistory = async () => {
  try {
    const res = await getAssetNetworkHistory(props.assetId, range.value)
    historySeries.value = res?.series || []
  } catch (error) {
    historySeries.value = []
  }
}

onMounted(() => {
  loading.value = true
  Promise.all([loadLive(), loadHistory()]).finally(() => { loading.value = false })
  pollTimer = setInterval(loadLive, 10000)
})
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
})
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-net { min-height: 300px; }

.zv-net-top { margin-bottom: 16px; }

.zv-net-card {
  background: $bg-card;
  border: 1px solid $border-color-light;
  border-radius: $border-radius;
  padding: 18px 20px;
  margin-bottom: 16px;
}

.zv-net-card-title {
  font-size: 14px; font-weight: 600; color: $text-primary;
  margin-bottom: 14px;
}

.zv-net-status { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.zv-net-status-text { font-size: 20px; font-weight: 700; color: $text-primary; }

.zv-net-dot, .zv-net-dot-sm {
  display: inline-block; border-radius: 50%; background: $text-disabled;
  &.is-up { background: $success-color; box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.15); }
  &.is-down { background: $danger-color; box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.15); }
}
.zv-net-dot { width: 10px; height: 10px; }
.zv-net-dot-sm { width: 7px; height: 7px; margin-right: 4px; }

.zv-net-iface { font-size: 16px; font-weight: 600; color: $text-primary; margin-bottom: 2px; }
.zv-net-muted { font-size: 12px; color: $text-tertiary; }

.zv-net-kv {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 13px; color: $text-secondary;
  padding: 5px 0;
  border-bottom: 1px dashed $border-color-light;
  &:last-child { border-bottom: 0; }
}

.zv-net-speed {
  display: flex; gap: 24px; margin-bottom: 12px;
}
.zv-net-speed-item {
  display: flex; align-items: center; gap: 10px;
  .el-icon { font-size: 22px; }
  .is-down { color: $brand-primary; }
  .is-up { color: $success-color; }
}
.zv-net-speed-num {
  font-family: $font-mono; font-size: 20px; font-weight: 700; color: $text-primary;
  line-height: 1.2;
}
.zv-net-speed-label { font-size: 12px; color: $text-tertiary; }

.zv-net-loss-warn { color: $danger-color; font-weight: 600; }

.zv-net-chart-card { padding-bottom: 8px; }
.zv-net-chart-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px;
}
.zv-net-chart { height: 280px; width: 100%; }
</style>
