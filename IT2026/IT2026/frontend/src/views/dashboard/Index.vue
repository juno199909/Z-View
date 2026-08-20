<template>
  <div class="zv-dashboard">
    <!-- 欢迎条 -->
    <div class="zv-welcome">
      <div>
        <h2 class="zv-welcome-title">{{ greeting }}，{{ userName }} 👋</h2>
        <p class="zv-welcome-sub">这是 Z-View 运维平台 · 实时掌握企业终端健康度</p>
      </div>
      <div class="zv-welcome-actions">
        <el-button class="zv-action-btn" :icon="Operation" plain @click="$router.push('/automation')">批量操作</el-button>
        <el-button class="zv-action-btn" :icon="Plus" type="primary" @click="$router.push('/asset/create')">新增资产</el-button>
      </div>
    </div>

    <!-- 统计卡（4 个 + 1 横向大卡） -->
    <div class="zv-stats-grid">
      <div class="zv-stat-card zv-stat-primary">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><Box /></el-icon></div>
        </div>
        <div class="zv-stat-value">{{ stats.total || 0 }}</div>
        <div class="zv-stat-label">总资产数</div>
        <div class="zv-stat-spark" />
      </div>

      <div class="zv-stat-card zv-stat-success">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><CircleCheck /></el-icon></div>
          <div class="zv-stat-trend up">{{ onlineRate }}% 在线</div>
        </div>
        <div class="zv-stat-value">{{ stats.online || 0 }}</div>
        <div class="zv-stat-label">在线设备</div>
        <div class="zv-stat-spark" />
      </div>

      <div class="zv-stat-card zv-stat-warning">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><Warning /></el-icon></div>
          <div class="zv-stat-trend down">需关注</div>
        </div>
        <div class="zv-stat-value">{{ (stats.risk || 0) + (stats.unknown || 0) }}</div>
        <div class="zv-stat-label">风险 / 未知状态</div>
        <div class="zv-stat-spark" />
      </div>

      <div class="zv-stat-card zv-stat-info">
        <div class="zv-stat-head">
          <div class="zv-stat-icon"><el-icon :size="22"><Monitor /></el-icon></div>
          <div class="zv-stat-trend up">在线</div>
        </div>
        <div class="zv-stat-value">{{ stats.server || 0 }}</div>
        <div class="zv-stat-label">服务器数</div>
        <div class="zv-stat-spark" />
      </div>
    </div>

    <!-- 中间内容：左 8 列 / 右 4 列 -->
    <el-row :gutter="20" class="zv-row">
      <!-- 左 -->
      <el-col :xs="24" :md="16">
        <!-- 在线率圆环 + 趋势 -->
        <div class="zv-card zv-card-pad">
          <div class="zv-card-head">
            <div>
              <div class="zv-card-title">设备健康概览</div>
              <div class="zv-card-subtitle">最近 24 小时 · 实时刷新</div>
            </div>
            <el-button text type="primary" @click="$router.push('/asset/list')">
              查看全部
              <el-icon :size="14"><ArrowRight /></el-icon>
            </el-button>
          </div>

          <div class="zv-health-row">
            <div class="zv-health-circle">
              <svg viewBox="0 0 120 120" class="zv-health-svg">
                <circle cx="60" cy="60" r="52" fill="none" stroke="#e2e8f0" stroke-width="12" />
                <circle
                  cx="60"
                  cy="60"
                  r="52"
                  fill="none"
                  :stroke="onlineRateColor"
                  stroke-width="12"
                  stroke-linecap="round"
                  :stroke-dasharray="`${onlineRate * 3.27} 327`"
                  transform="rotate(-90 60 60)"
                />
              </svg>
              <div class="zv-health-value">
                <div class="zv-health-pct">{{ onlineRate }}%</div>
                <div class="zv-health-pct-label">在线率</div>
              </div>
            </div>

            <div class="zv-health-legend">
              <div class="zv-legend-item">
                <div class="zv-legend-dot is-success" />
                <div class="zv-legend-info">
                  <div class="zv-legend-label">在线</div>
                  <div class="zv-legend-value">{{ stats.online || 0 }} 台</div>
                </div>
              </div>
              <div class="zv-legend-item">
                <div class="zv-legend-dot is-danger" />
                <div class="zv-legend-info">
                  <div class="zv-legend-label">离线</div>
                  <div class="zv-legend-value">{{ stats.offline || 0 }} 台</div>
                </div>
              </div>
              <div class="zv-legend-item">
                <div class="zv-legend-dot is-warning" />
                <div class="zv-legend-info">
                  <div class="zv-legend-label">降级</div>
                  <div class="zv-legend-value">{{ stats.degraded || 0 }} 台</div>
                </div>
              </div>
              <div class="zv-legend-item">
                <div class="zv-legend-dot is-unknown" />
                <div class="zv-legend-info">
                  <div class="zv-legend-label">未知</div>
                  <div class="zv-legend-value">{{ stats.unknown || 0 }} 台</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 最近资产 -->
        <div class="zv-card zv-card-pad">
          <div class="zv-card-head">
            <div>
              <div class="zv-card-title">最近资产</div>
              <div class="zv-card-subtitle">最新 5 台</div>
            </div>
            <el-button text type="primary" @click="$router.push('/asset/list')">
              查看全部
              <el-icon :size="14"><ArrowRight /></el-icon>
            </el-button>
          </div>

          <el-table :data="recentAssets" v-loading="loading" :show-header="true" class="zv-table-mini">
            <el-table-column label="" width="50">
              <template #default="{ row }">
                <span class="zv-status-dot" :class="`is-${row.status || 'unknown'}`" />
              </template>
            </el-table-column>
            <el-table-column prop="hostname" label="主机名" min-width="140">
              <template #default="{ row }">
                <span class="zv-table-hostname">{{ row.hostname }}</span>
                <span class="zv-table-ip">{{ row.ip_address }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="os_type" label="系统" min-width="100">
              <template #default="{ row }">
                <el-tag size="small" type="info" effect="plain">{{ row.os_type || '未知' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="getStatusTagType(row.status)" effect="light">
                  {{ getStatusName(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="80" align="right">
              <template #default="{ row }">
                <el-button text type="primary" size="small" @click="viewAsset(row.id)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>

      <!-- 右 -->
      <el-col :xs="24" :md="8">
        <!-- 资产分组分布 -->
        <div class="zv-card zv-card-pad">
          <div class="zv-card-head">
            <div>
              <div class="zv-card-title">分组分布</div>
              <div class="zv-card-subtitle">按业务分组</div>
            </div>
          </div>
          <div class="zv-group-list">
            <div v-for="(value, key) in stats.byGroup" :key="key" class="zv-group-item">
              <div class="zv-group-info">
                <div class="zv-group-dot" :style="{ background: getGroupColor(key) }" />
                <span class="zv-group-name">{{ key }}</span>
              </div>
              <div class="zv-group-count">
                <span class="zv-group-num">{{ value }}</span>
                <span class="zv-group-pct">{{ getPercentage(value) }}%</span>
              </div>
            </div>
            <el-empty v-if="!Object.keys(stats.byGroup || {}).length" description="暂无数据" :image-size="80" />
          </div>
        </div>

        <!-- 软件 Top 5 -->
        <div class="zv-card zv-card-pad zv-card-mt">
          <div class="zv-card-head">
            <div>
              <div class="zv-card-title">热门软件</div>
              <div class="zv-card-subtitle">Top 5 装机量</div>
            </div>
            <el-button text type="primary" @click="$router.push('/terminal/software-center')">
              <el-icon :size="14"><ArrowRight /></el-icon>
            </el-button>
          </div>
          <div class="zv-software-list">
            <div v-for="(item, idx) in topSoftware.slice(0, 5)" :key="item.software_name + idx" class="zv-software-item">
              <div class="zv-software-rank" :class="`rank-${idx + 1}`">{{ idx + 1 }}</div>
              <div class="zv-software-info">
                <div class="zv-software-name">{{ item.software_name }}</div>
                <div class="zv-software-vendor">{{ item.vendor || '未知' }}</div>
              </div>
              <div class="zv-software-count">{{ item.install_count }}</div>
            </div>
            <el-empty v-if="!topSoftware.length && !softwareLoading" description="暂无软件数据" :image-size="80" />
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import {
  Box, CircleCheck, Monitor, Warning, ArrowRight, Operation, Plus
} from '@element-plus/icons-vue'
import { getAssetStats, getAssetList, getSoftwareStats } from '@/api/asset'
import { getStoredAuthUsername } from '@/api/auth'

const router = useRouter()
const loading = ref(false)
const softwareLoading = ref(false)
const stats = ref({
  total: 0,
  online: 0,
  offline: 0,
  unknown: 0,
  degraded: 0,
  server: 0,
  byType: {},
  byGroup: {}
})
const recentAssets = ref([])
const topSoftware = ref([])
let refreshTimer = null

const userName = computed(() => getStoredAuthUsername() || '管理员')
const hour = new Date().getHours()
const greeting = computed(() => {
  if (hour < 6) return '夜深了'
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

const onlineRate = computed(() => {
  if (stats.value.total === 0) return 0
  return Math.round((stats.value.online / stats.value.total) * 100)
})

const onlineRateColor = computed(() => {
  const r = onlineRate.value
  if (r >= 90) return '#10b981'
  if (r >= 70) return '#3b82f6'
  if (r >= 50) return '#f59e0b'
  return '#ef4444'
})

const loadStats = async () => {
  try {
    const data = await getAssetStats()
    stats.value = {
      total: data.total || 0,
      online: data.online || 0,
      offline: data.offline || 0,
      unknown: data.unknown || 0,
      degraded: data.degraded || 0,
      server: (data.by_type && data.by_type.server) || 0,
      byType: data.by_type || {},
      byGroup: data.by_group || {}
    }
  } catch (error) {
    console.error('加载统计失败:', error)
  }
}

const loadRecentAssets = async () => {
  loading.value = true
  try {
    const res = await getAssetList({ page: 1, page_size: 5 })
    recentAssets.value = res.data || []
  } catch (error) {
    console.error('加载资产失败:', error)
  } finally {
    loading.value = false
  }
}

const loadSoftwareStats = async () => {
  softwareLoading.value = true
  try {
    const res = await getSoftwareStats({ limit: 5 })
    const rows = Array.isArray(res?.data) ? res.data : []
    topSoftware.value = rows
  } catch (error) {
    console.error('加载软件统计失败:', error)
    topSoftware.value = []
  } finally {
    softwareLoading.value = false
  }
}

const getGroupColor = (groupName) => {
  const palette = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#ef4444', '#6366f1']
  const seed = String(groupName || '')
  let hash = 0
  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash + seed.charCodeAt(i)) % palette.length
  }
  return palette[hash]
}

const getPercentage = (value) => {
  if (stats.value.total === 0) return 0
  return Math.round((value / stats.value.total) * 100)
}

const getStatusTagType = (status) => {
  const map = { online: 'success', offline: 'danger', degraded: 'warning', unknown: 'info' }
  return map[status] || 'info'
}

const getStatusName = (status) => {
  const map = { online: '在线', offline: '离线', degraded: '降级', unknown: '未知' }
  return map[status] || status
}

const viewAsset = (id) => {
  router.push(`/asset/detail/${id}`)
}

onMounted(() => {
  loadStats()
  loadRecentAssets()
  loadSoftwareStats()
  refreshTimer = setInterval(() => {
    loadStats()
    loadRecentAssets()
    loadSoftwareStats()
  }, 30000)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-dashboard {
  padding: $content-padding;
  max-width: 1600px;
  margin: 0 auto;
}

// ---- 欢迎条 ----
.zv-welcome {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding: 24px 28px;
  background: linear-gradient(135deg, #1e40af 0%, #2563eb 50%, #3b82f6 100%);
  border-radius: $border-radius-lg;
  color: #fff;
  position: relative;
  overflow: hidden;
  box-shadow: 0 4px 20px rgba(37, 99, 235, 0.18);

  &::before {
    content: '';
    position: absolute;
    top: -50%;
    right: -10%;
    width: 300px;
    height: 300px;
    background: radial-gradient(circle, rgba(255,255,255,0.12) 0%, transparent 70%);
    border-radius: 50%;
  }
  &::after {
    content: '';
    position: absolute;
    bottom: -60%;
    right: 20%;
    width: 250px;
    height: 250px;
    background: radial-gradient(circle, rgba(255,255,255,0.08) 0%, transparent 70%);
    border-radius: 50%;
  }
}

.zv-welcome-title {
  font-size: 22px;
  font-weight: 600;
  margin: 0 0 4px 0;
  position: relative;
  z-index: 1;
}

.zv-welcome-sub {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.85);
  margin: 0;
  position: relative;
  z-index: 1;
}

.zv-welcome-actions {
  display: flex;
  gap: 12px;
  position: relative;
  z-index: 1;
}

.zv-action-btn {
  background: rgba(255, 255, 255, 0.15) !important;
  border: 1px solid rgba(255, 255, 255, 0.25) !important;
  color: #fff !important;
  backdrop-filter: blur(10px);

  &:hover {
    background: rgba(255, 255, 255, 0.25) !important;
    border-color: rgba(255, 255, 255, 0.4) !important;
  }
}

// ---- 统计卡 ----
.zv-stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;

  @media (max-width: 1100px) {
    grid-template-columns: repeat(2, 1fr);
  }
  @media (max-width: 600px) {
    grid-template-columns: 1fr;
  }
}

.zv-stat-card {
  position: relative;
  background: $bg-card;
  border-radius: $border-radius-lg;
  padding: 22px;
  box-shadow: $shadow-sm;
  border: 1px solid $border-color-light;
  overflow: hidden;
  transition: all $transition-base;

  &:hover {
    transform: translateY(-2px);
    box-shadow: $shadow-md;
  }

  &::before {
    content: '';
    position: absolute;
    inset: 0;
    background: var(--card-gradient);
    opacity: 0.04;
    pointer-events: none;
  }
}

.zv-stat-primary { --card-gradient: linear-gradient(135deg, #3b82f6, #2563eb); }
.zv-stat-success { --card-gradient: linear-gradient(135deg, #10b981, #059669); }
.zv-stat-warning { --card-gradient: linear-gradient(135deg, #f59e0b, #d97706); }
.zv-stat-info    { --card-gradient: linear-gradient(135deg, #6366f1, #4f46e5); }

.zv-stat-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  position: relative;
  z-index: 1;
}

.zv-stat-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: var(--card-gradient);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}

.zv-stat-trend {
  font-size: 12px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: $radius-pill;

  &.up {
    color: $success-color;
    background: $success-bg;
  }
  &.down {
    color: $warning-color;
    background: $warning-bg;
  }
}

.zv-stat-value {
  font-size: 32px;
  font-weight: 700;
  color: $text-primary;
  line-height: 1.1;
  letter-spacing: -0.5px;
  position: relative;
  z-index: 1;
}

.zv-stat-label {
  font-size: 13px;
  color: $text-secondary;
  margin-top: 6px;
  position: relative;
  z-index: 1;
}

.zv-stat-spark {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 32px;
  background: var(--card-gradient);
  opacity: 0.05;
  clip-path: polygon(0 70%, 15% 50%, 30% 65%, 50% 30%, 70% 50%, 85% 20%, 100% 40%, 100% 100%, 0 100%);
}

// ---- 卡片通用 ----
.zv-card {
  background: $bg-card;
  border-radius: $border-radius-lg;
  border: 1px solid $border-color-light;
  box-shadow: $shadow-sm;
  transition: all $transition-base;
}

.zv-card-pad {
  padding: 20px 22px;
}

.zv-card-mt {
  margin-top: 20px;
}

.zv-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 18px;
}

.zv-card-title {
  font-size: 16px;
  font-weight: 600;
  color: $text-primary;
}

.zv-card-subtitle {
  font-size: 12px;
  color: $text-tertiary;
  margin-top: 2px;
}

.zv-row {
  margin-bottom: 0;
}

// ---- 健康圆环 ----
.zv-health-row {
  display: flex;
  align-items: center;
  gap: 32px;
  padding: 8px 0;
}

.zv-health-circle {
  position: relative;
  width: 140px;
  height: 140px;
  flex-shrink: 0;
}

.zv-health-svg {
  width: 100%;
  height: 100%;
  transform: rotate(0);
  transition: all 0.6s ease;
}

.zv-health-value {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

.zv-health-pct {
  font-size: 28px;
  font-weight: 700;
  color: $text-primary;
  line-height: 1;
}

.zv-health-pct-label {
  font-size: 11px;
  color: $text-tertiary;
  margin-top: 4px;
}

.zv-health-legend {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.zv-legend-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.zv-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;

  &.is-success { background: $success-color; }
  &.is-danger  { background: $danger-color; }
  &.is-warning { background: $warning-color; }
  &.is-unknown { background: $text-tertiary; }
}

.zv-legend-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex: 1;
  border-bottom: 1px dashed $border-color;
  padding-bottom: 6px;
}

.zv-legend-label {
  font-size: 13px;
  color: $text-secondary;
}

.zv-legend-value {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
}

// ---- 最近资产表 ----
.zv-table-mini {
  :deep(.el-table__row) {
    height: 56px;
  }
  :deep(.cell) {
    padding: 0 !important;
  }
  :deep(td.el-table__cell) {
    border-bottom: 1px solid $border-color-light !important;
  }
}

.zv-table-hostname {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
  line-height: 1.2;
}

.zv-table-ip {
  display: block;
  font-size: 12px;
  color: $text-tertiary;
  margin-top: 2px;
  font-family: $font-mono;
}

// ---- 分组分布 ----
.zv-group-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.zv-group-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-radius: $border-radius;
  background: $slate-50;
  transition: all $transition-base;

  &:hover {
    background: $brand-primary-50;
  }
}

.zv-group-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.zv-group-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.zv-group-name {
  font-size: 13px;
  color: $text-primary;
  font-weight: 500;
}

.zv-group-count {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.zv-group-num {
  font-size: 18px;
  font-weight: 700;
  color: $text-primary;
}

.zv-group-pct {
  font-size: 12px;
  color: $text-tertiary;
}

// ---- 热门软件 ----
.zv-software-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.zv-software-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px;
  border-radius: $border-radius;
  transition: all $transition-base;

  &:hover {
    background: $slate-50;
  }
}

.zv-software-rank {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 700;
  color: $text-tertiary;
  background: $slate-100;

  &.rank-1 { background: linear-gradient(135deg, #fbbf24, #f59e0b); color: #fff; }
  &.rank-2 { background: linear-gradient(135deg, #cbd5e1, #94a3b8); color: #fff; }
  &.rank-3 { background: linear-gradient(135deg, #fdba74, #f97316); color: #fff; }
}

.zv-software-info {
  flex: 1;
  min-width: 0;
}

.zv-software-name {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.zv-software-vendor {
  font-size: 11px;
  color: $text-tertiary;
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.zv-software-count {
  font-size: 14px;
  font-weight: 700;
  color: $brand-primary;
  font-family: $font-mono;
}
</style>
