<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端概览</h2>
        <div class="zv-page-subtitle">在线 {{ stats.online || 0 }} · 离线 {{ stats.offline || 0 }} · 风险 {{ stats.risk || 0 }}</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
        <el-input v-model="search" placeholder="搜索主机名 / IP" clearable :prefix-icon="'Search'" style="width: 240px" />
      </div>
    </div>

    <!-- 4 个统计卡 -->
    <div class="zv-stats-grid">
      <div class="zv-stat-card zv-stat-success">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><CircleCheck /></el-icon></div>
        </div>
        <div class="zv-stat-value">{{ stats.online || 0 }}</div>
        <div class="zv-stat-label">在线终端</div>
      </div>
      <div class="zv-stat-card zv-stat-danger">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><CircleClose /></el-icon></div>
        </div>
        <div class="zv-stat-value">{{ stats.offline || 0 }}</div>
        <div class="zv-stat-label">离线终端</div>
      </div>
      <div class="zv-stat-card zv-stat-warning">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><Warning /></el-icon></div>
        </div>
        <div class="zv-stat-value">{{ stats.risk || 0 }}</div>
        <div class="zv-stat-label">风险终端</div>
      </div>
      <div class="zv-stat-card zv-stat-info">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><Monitor /></el-icon></div>
        </div>
        <div class="zv-stat-value">{{ stats.by_type?.server || 0 }}</div>
        <div class="zv-stat-label">服务器</div>
      </div>
    </div>

    <!-- 终端列表 -->
    <div class="zv-card">
      <div class="zv-card-head">
        <div>
          <div class="zv-card-title">终端列表</div>
          <div class="zv-card-subtitle">共 {{ pagination.total }} 台 · 点击行进入实时监控</div>
        </div>
      </div>
      <el-table v-loading="loading" :data="filteredTerminals" @row-dblclick="openTerminal" :row-style="{ cursor: 'pointer' }">
        <el-table-column label="主机" min-width="220">
          <template #default="{ row }">
            <div class="zv-host-cell">
              <div class="zv-host-avatar" :style="{ background: getTypeGradient(row.asset_type) }">
                <el-icon :size="18"><component :is="getTypeIcon(row.asset_type)" /></el-icon>
              </div>
              <div>
                <div class="zv-host-name">{{ row.hostname || '-' }}</div>
                <div class="zv-host-ip">{{ row.ip_address || '-' }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span class="zv-status-chip" :class="`is-${row.status || 'unknown'}`">
              <span class="zv-status-dot" :class="`is-${row.status || 'unknown'}`" />
              {{ getStatusText(row.status) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="CPU" width="100">
          <template #default="{ row }">
            <div class="zv-metric-mini">
              <div class="zv-metric-bar"><span :style="{ width: (row.cpu_usage || 0) + '%' }" /></div>
              <span class="zv-metric-num">{{ row.cpu_usage || 0 }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="内存" width="100">
          <template #default="{ row }">
            <div class="zv-metric-mini">
              <div class="zv-metric-bar"><span :style="{ width: (row.memory_usage || 0) + '%' }" /></div>
              <span class="zv-metric-num">{{ row.memory_usage || 0 }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="磁盘" width="100">
          <template #default="{ row }">
            <div class="zv-metric-mini">
              <div class="zv-metric-bar"><span :style="{ width: (row.disk_usage || 0) + '%' }" /></div>
              <span class="zv-metric-num">{{ row.disk_usage || 0 }}%</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="os_type" label="系统" min-width="120" />
        <el-table-column label="Agent版本" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.agent_version" size="small" effect="plain" type="info">v{{ row.agent_version }}</el-tag>
            <span v-else class="zv-mono">-</span>
          </template>
        </el-table-column>
        <el-table-column label="最后心跳" width="170">
          <template #default="{ row }">
            <span class="zv-mono">{{ formatTime(row.last_seen) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" :icon="View" @click="openTerminal(row)">监控</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无终端" :image-size="80" /></template>
      </el-table>
      <div class="zv-pagination">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :total="pagination.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @size-change="loadData"
          @current-change="loadData"
        />
      </div>
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
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Refresh, CircleCheck, CircleClose, Warning, Monitor, View,
  Box, Cpu, Share, Connection
} from '@element-plus/icons-vue'
import { getAssetList, getAssetStats } from '@/api/asset'
import WebRemoteDesktop from '@/components/WebRemoteDesktop.vue'
import dayjs from 'dayjs'

const router = useRouter()
const loading = ref(false)
const terminals = ref([])
const stats = ref({})
const search = ref('')
const showRemoteDesktop = ref(false)
const currentAssetId = ref(null)
const currentIpAddress = ref('')
const currentHostname = ref('')

const pagination = reactive({ page: 1, page_size: 20, total: 0 })

const TYPE_META = {
  server: { icon: Cpu,        gradient: 'linear-gradient(135deg, #3b82f6, #2563eb)' },
  pc:     { icon: Monitor,    gradient: 'linear-gradient(135deg, #10b981, #059669)' },
  switch: { icon: Connection, gradient: 'linear-gradient(135deg, #f59e0b, #d97706)' },
  router: { icon: Share,      gradient: 'linear-gradient(135deg, #8b5cf6, #7c3aed)' }
}
const getTypeIcon = (t) => TYPE_META[t]?.icon || Box
const getTypeGradient = (t) => TYPE_META[t]?.gradient || 'linear-gradient(135deg, #94a3b8, #64748b)'

const getStatusText = (s) => ({ online: '在线', offline: '离线', degraded: '降级', unknown: '未知' }[s] || s)
const formatTime = (v) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'

const filteredTerminals = computed(() => {
  if (!search.value) return terminals.value
  const q = search.value.toLowerCase()
  return terminals.value.filter(t =>
    (t.hostname && t.hostname.toLowerCase().includes(q)) ||
    (t.ip_address && t.ip_address.includes(q))
  )
})

const openTerminal = (row) => {
  currentAssetId.value = row.id
  currentIpAddress.value = row.ip_address
  currentHostname.value = row.hostname
  showRemoteDesktop.value = true
}

const loadStats = async () => {
  try {
    const data = await getAssetStats()
    stats.value = data || {}
  } catch {}
}

const loadData = async () => {
  loading.value = true
  try {
    const res = await getAssetList({ page: pagination.page, page_size: pagination.page_size })
    terminals.value = res.data || []
    pagination.total = res.total || 0
  } catch (error) {
    console.error('加载终端失败:', error)
  } finally {
    loading.value = false
  }
}

onMounted(() => { loadStats(); loadData() })
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1600px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; align-items: center; }

// 统计卡（与 dashboard 一致）
.zv-stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
  @media (max-width: 1100px) { grid-template-columns: repeat(2, 1fr); }
}

.zv-stat-card {
  --card-gradient: #{$brand-primary};
  position: relative;
  background: $bg-card;
  border-radius: $border-radius-lg;
  padding: 22px;
  box-shadow: $shadow-sm;
  border: 1px solid $border-color-light;
  overflow: hidden;
  &::before { content: ''; position: absolute; inset: 0; background: var(--card-gradient); opacity: 0.04; pointer-events: none; }
  &.zv-stat-success { --card-gradient: linear-gradient(135deg, #10b981, #059669); }
  &.zv-stat-danger  { --card-gradient: linear-gradient(135deg, #ef4444, #dc2626); }
  &.zv-stat-warning { --card-gradient: linear-gradient(135deg, #f59e0b, #d97706); }
  &.zv-stat-info    { --card-gradient: linear-gradient(135deg, #6366f1, #4f46e5); }
}

.zv-stat-head { display: flex; justify-content: flex-end; margin-bottom: 12px; position: relative; z-index: 1; }

.zv-stat-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: var(--card-gradient);
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.12);
}

.zv-stat-value {
  font-size: 30px;
  font-weight: 700;
  color: $text-primary;
  line-height: 1.1;
  position: relative;
  z-index: 1;
  font-family: $font-mono;
}

.zv-stat-label { font-size: 13px; color: $text-secondary; margin-top: 4px; position: relative; z-index: 1; }

.zv-card { padding: 0; }

.zv-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 22px;
  border-bottom: 1px solid $border-color-light;
  background: $slate-50;
}

.zv-card-title { font-size: 15px; font-weight: 600; color: $text-primary; }
.zv-card-subtitle { font-size: 12px; color: $text-tertiary; margin-top: 2px; }

// 表格
.zv-host-cell { display: flex; align-items: center; gap: 10px; }
.zv-host-avatar {
  width: 36px; height: 36px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  color: #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.08);
  flex-shrink: 0;
}
.zv-host-name { font-size: 13px; font-weight: 600; color: $text-primary; line-height: 1.2; }
.zv-host-ip { font-size: 12px; color: $text-tertiary; font-family: $font-mono; margin-top: 2px; }

.zv-status-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 10px; border-radius: $radius-pill;
  font-size: 12px; font-weight: 500;
  background: $slate-50; color: $text-secondary;
  &.is-online  { background: rgba(16, 185, 129, 0.10); color: $success-color; }
  &.is-offline { background: rgba(239, 68, 68, 0.10); color: $danger-color; }
  &.is-warning { background: rgba(245, 158, 11, 0.10); color: $warning-color; }
  &.is-unknown { background: $slate-100; color: $text-tertiary; }
}
.zv-status-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

.zv-metric-mini { display: flex; align-items: center; gap: 8px; }
.zv-metric-bar {
  width: 50px; height: 6px; background: $slate-100; border-radius: $radius-pill; overflow: hidden;
  > span { display: block; height: 100%; background: $brand-primary; border-radius: $radius-pill; }
}
.zv-metric-num { font-size: 12px; color: $text-secondary; font-family: $font-mono; min-width: 32px; }

.zv-mono { font-family: $font-mono; font-size: 12px; color: $text-secondary; }

.zv-pagination { padding: 16px 22px; display: flex; justify-content: flex-end; border-top: 1px solid $border-color-light; }

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell { background: #fafbfc; color: $text-secondary; font-weight: 600; font-size: 12px; }
  tr:hover > td.el-table__cell { background: rgba(37, 99, 235, 0.03) !important; }
  td.el-table__cell { border-bottom: 1px solid $slate-100 !important; }
  .el-table__inner-wrapper::before { height: 0; }
}
</style>
