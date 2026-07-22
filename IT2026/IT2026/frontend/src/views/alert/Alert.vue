<template>
  <div class="app-container">
    <el-row :gutter="20" class="stats-row">
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="7天内告警" :value="stats.total_7days">
            <template #prefix>
              <el-icon style="color: #409eff;"><Bell /></el-icon>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="活跃告警" :value="stats.active">
            <template #prefix>
              <el-icon style="color: #f56c6c;"><Warning /></el-icon>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-title">按严重程度</div>
            <div class="stat-tags">
              <div>
                <el-tag type="danger" size="small">严重</el-tag>
                <div class="stat-number">{{ stats.by_severity.critical || 0 }}</div>
              </div>
              <div>
                <el-tag type="warning" size="small">警告</el-tag>
                <div class="stat-number">{{ stats.by_severity.warning || 0 }}</div>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover" class="stat-card">
          <div class="stat-content">
            <div class="stat-title">按类型统计</div>
            <div class="type-summary">
              <div v-for="(count, type) in stats.by_type" :key="type" class="type-summary-item">
                <el-tag size="small">{{ getTypeText(type) }}</el-tag>
                <div class="type-summary-value">{{ count }}</div>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card>
      <div class="toolbar">
        <div class="toolbar-main">
          <div class="toolbar-filters">
            <el-radio-group v-model="statusFilter" @change="handleFilterChange">
              <el-radio-button value="">全部</el-radio-button>
              <el-radio-button value="active">活跃</el-radio-button>
              <el-radio-button value="resolved">已解决</el-radio-button>
            </el-radio-group>
            <el-select
              v-model="severityFilter"
              placeholder="严重程度"
              clearable
              style="width: 130px"
              @change="handleFilterChange"
            >
              <el-option label="严重" value="critical" />
              <el-option label="警告" value="warning" />
              <el-option label="信息" value="info" />
            </el-select>
            <el-select
              v-model="typeFilter"
              placeholder="告警类型"
              clearable
              style="width: 150px"
              @change="handleFilterChange"
            >
              <el-option
                v-for="option in alertTypeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
            <el-date-picker
              v-model="timeRange"
              type="datetimerange"
              range-separator="至"
              start-placeholder="开始时间"
              end-placeholder="结束时间"
              value-format="YYYY-MM-DD HH:mm:ss"
              format="YYYY-MM-DD HH:mm:ss"
              clearable
              style="width: 360px"
              @change="handleTimeRangeChange"
            />
            <el-input
              v-model="keyword"
              placeholder="主机/IP/告警信息"
              clearable
              style="width: 240px"
              @clear="handleFilterChange"
              @keyup.enter="handleFilterChange"
            />
          </div>
          <div class="toolbar-actions">
            <el-button @click="handleFilterChange">查询</el-button>
            <el-button @click="handleReset">重置</el-button>
            <el-button
              type="warning"
              :loading="resolving"
              :disabled="selectedIds.length === 0"
              @click="handleBatchResolve"
            >
              批量解决
            </el-button>
            <el-button :icon="Download" :loading="exporting" @click="handleExport">导出</el-button>
            <el-button :icon="Refresh" @click="refreshAll" :loading="loading">刷新</el-button>
          </div>
        </div>
        <div class="quick-range-row">
          <span class="quick-range-label">快捷筛选</span>
          <el-check-tag
            v-for="option in quickRangeOptions"
            :key="option.value"
            :checked="activeQuickRange === option.value"
            @change="applyQuickRange(option.value)"
          >
            {{ option.label }}
          </el-check-tag>
        </div>
      </div>

      <el-table
        v-loading="loading"
        :data="alerts"
        row-key="id"
        empty-text="当前筛选条件下暂无告警"
        style="margin-top: 20px;"
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="55" />
        <el-table-column label="严重程度" width="100">
          <template #default="{ row }">
            <el-tag :type="getSeverityType(row.severity)" size="small">
              {{ getSeverityText(row.severity) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small">{{ getTypeText(row.alert_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="主机" width="150">
          <template #default="{ row }">
            <div>{{ row.hostname }}</div>
            <div class="sub-text">{{ row.ip_address }}</div>
          </template>
        </el-table-column>
        <el-table-column label="告警信息" min-width="300" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.message }}
          </template>
        </el-table-column>
        <el-table-column label="当前值" width="110" align="center">
          <template #default="{ row }">
            <span v-if="row.current_value !== null && row.current_value !== undefined">
              {{ formatMetricValue(row) }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'danger' : 'success'" size="small">
              {{ row.status === 'active' ? '活跃' : '已解决' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="告警时间" width="160">
          <template #default="{ row }">
            {{ row.created_at }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="openDetail(row)">
              详情
            </el-button>
            <el-button
              v-if="row.status === 'active'"
              type="warning"
              link
              size="small"
              :disabled="resolving"
              @click="resolveSingleAlert(row)"
            >
              标记已解决
            </el-button>
            <el-button type="primary" link size="small" @click="goToTerminal(row.asset_id)">
              查看终端
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        style="margin-top: 20px; justify-content: flex-end;"
        @current-change="loadAlerts"
        @size-change="handlePageSizeChange"
      />
    </el-card>

    <el-drawer v-model="detailVisible" title="告警详情" size="45%">
      <el-skeleton :loading="detailLoading" animated :rows="8">
        <template #default>
          <el-descriptions
            v-if="detailRecord"
            :column="1"
            border
            size="small"
            class="detail-descriptions"
          >
            <el-descriptions-item label="告警ID">{{ detailRecord.id }}</el-descriptions-item>
            <el-descriptions-item label="主机">{{ detailRecord.hostname }}</el-descriptions-item>
            <el-descriptions-item label="IP地址">{{ detailRecord.ip_address }}</el-descriptions-item>
            <el-descriptions-item label="类型">{{ getTypeText(detailRecord.alert_type) }}</el-descriptions-item>
            <el-descriptions-item label="严重程度">
              {{ getSeverityText(detailRecord.severity) }}
            </el-descriptions-item>
            <el-descriptions-item label="状态">
              {{ detailRecord.status === 'active' ? '活跃' : '已解决' }}
            </el-descriptions-item>
            <el-descriptions-item label="告警信息">{{ detailRecord.message }}</el-descriptions-item>
            <el-descriptions-item label="当前值">
              {{ detailRecord.current_value !== null && detailRecord.current_value !== undefined ? formatMetricValue(detailRecord) : '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="阈值">
              {{ detailRecord.threshold_value ?? '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="首次触发时间">{{ detailRecord.created_at || '-' }}</el-descriptions-item>
            <el-descriptions-item label="最近出现时间">{{ detailRecord.last_seen_at || '-' }}</el-descriptions-item>
            <el-descriptions-item label="解决时间">{{ detailRecord.resolved_at || '-' }}</el-descriptions-item>
            <el-descriptions-item label="解决人">{{ detailRecord.resolved_by || '-' }}</el-descriptions-item>
          </el-descriptions>

          <el-card v-if="detailRecord?.details" shadow="never" class="details-card">
            <template #header>附加详情</template>
            <pre>{{ stringifyDetails(detailRecord.details) }}</pre>
          </el-card>
        </template>
      </el-skeleton>
      <template #footer>
        <div class="drawer-footer">
          <el-button @click="detailVisible = false">关闭</el-button>
          <el-button
            v-if="detailRecord?.status === 'active'"
            type="warning"
            :loading="resolving"
            @click="resolveSingleAlert(detailRecord)"
          >
            标记已解决
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Bell, Warning, Refresh, Download } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import {
  batchResolveAlerts,
  exportAlerts,
  getAlertDetail,
  getAlertList,
  getAlertStats,
  resolveAlertById
} from '@/api/alert'

const router = useRouter()
const loading = ref(false)
const detailLoading = ref(false)
const resolving = ref(false)
const exporting = ref(false)
const detailVisible = ref(false)
const detailRecord = ref(null)
const selectedIds = ref([])
const statusFilter = ref('')
const severityFilter = ref('')
const typeFilter = ref('')
const keyword = ref('')
const timeRange = ref([])
const activeQuickRange = ref('')
const currentPage = ref(1)
const pageSize = ref(50)
const total = ref(0)
const alerts = ref([])
const refreshTimer = ref(null)
const stats = ref({
  total_7days: 0,
  active: 0,
  resolved: 0,
  by_severity: {},
  by_type: {}
})
const alertTypeOptions = [
  { label: 'CPU', value: 'cpu' },
  { label: '内存', value: 'memory' },
  { label: '磁盘', value: 'disk' },
  { label: '离线', value: 'offline' },
  { label: '健康度', value: 'health' },
  { label: '保修', value: 'warranty' }
]
const quickRangeOptions = [
  { label: '今天', value: 'today' },
  { label: '近24小时', value: '24h' },
  { label: '近3天', value: '3d' },
  { label: '近7天', value: '7d' },
  { label: '近30天', value: '30d' }
]

const isCancelError = (error) => error === 'cancel' || error === 'close'

const buildQueryParams = () => {
  const params = {
    page: currentPage.value,
    page_size: pageSize.value
  }

  if (statusFilter.value) params.status = statusFilter.value
  if (severityFilter.value) params.severity = severityFilter.value
  if (typeFilter.value) params.alert_type = typeFilter.value
  if (keyword.value.trim()) params.keyword = keyword.value.trim()
  if (Array.isArray(timeRange.value) && timeRange.value.length === 2) {
    params.start_time = timeRange.value[0]
    params.end_time = timeRange.value[1]
  }

  return params
}

const setTimeRange = (start, end, quickValue = '') => {
  timeRange.value = [
    start.format('YYYY-MM-DD HH:mm:ss'),
    end.format('YYYY-MM-DD HH:mm:ss')
  ]
  activeQuickRange.value = quickValue
}

const applyQuickRange = (rangeKey) => {
  if (activeQuickRange.value === rangeKey) {
    activeQuickRange.value = ''
    timeRange.value = []
    handleFilterChange()
    return
  }

  const now = dayjs()
  switch (rangeKey) {
    case 'today':
      setTimeRange(now.startOf('day'), now.endOf('day'), rangeKey)
      break
    case '24h':
      setTimeRange(now.subtract(24, 'hour'), now, rangeKey)
      break
    case '3d':
      setTimeRange(now.subtract(3, 'day'), now, rangeKey)
      break
    case '7d':
      setTimeRange(now.subtract(7, 'day'), now, rangeKey)
      break
    case '30d':
      setTimeRange(now.subtract(30, 'day'), now, rangeKey)
      break
    default:
      activeQuickRange.value = ''
      timeRange.value = []
      break
  }

  handleFilterChange()
}

const handleTimeRangeChange = (value) => {
  timeRange.value = Array.isArray(value) ? value : []
  activeQuickRange.value = ''
  handleFilterChange()
}

const syncAlertRow = (alertId, patch) => {
  alerts.value = alerts.value.map(item => (
    item.id === alertId ? { ...item, ...patch } : item
  ))
}

const syncResolvedAlert = (alertId) => {
  const resolvedAt = dayjs().format('YYYY-MM-DD HH:mm:ss')
  const patch = {
    status: 'resolved',
    resolved_at: resolvedAt,
    resolved_by: 'console'
  }

  syncAlertRow(alertId, patch)

  if (detailRecord.value?.id === alertId) {
    detailRecord.value = {
      ...detailRecord.value,
      ...patch
    }
  }
}

const reloadDetail = async (alertId) => {
  detailLoading.value = true
  try {
    detailRecord.value = await getAlertDetail(alertId)
  } catch (error) {
    console.error('加载告警详情失败:', error)
    ElMessage.error('加载告警详情失败，请稍后重试')
  } finally {
    detailLoading.value = false
  }
}

const loadStats = async ({ silent = false } = {}) => {
  try {
    stats.value = await getAlertStats()
  } catch (error) {
    console.error('加载统计失败:', error)
    if (!silent) {
      ElMessage.error('加载告警统计失败，请稍后重试')
    }
  }
}

const loadAlerts = async ({ silent = false } = {}) => {
  try {
    loading.value = true
    const response = await getAlertList(buildQueryParams())
    alerts.value = response.data || []
    total.value = response.total || 0
    selectedIds.value = []
  } catch (error) {
    console.error('加载告警失败:', error)
    if (!silent) {
      ElMessage.error('加载告警列表失败，请稍后重试')
    }
  } finally {
    loading.value = false
  }
}

const refreshAll = async ({ silent = false } = {}) => {
  await Promise.all([
    loadStats({ silent }),
    loadAlerts({ silent })
  ])
}

const handleFilterChange = () => {
  currentPage.value = 1
  loadAlerts()
}

const handleReset = () => {
  statusFilter.value = ''
  severityFilter.value = ''
  typeFilter.value = ''
  keyword.value = ''
  timeRange.value = []
  activeQuickRange.value = ''
  currentPage.value = 1
  loadAlerts()
}

const handlePageSizeChange = () => {
  currentPage.value = 1
  loadAlerts()
}

const handleSelectionChange = (selection) => {
  selectedIds.value = selection.map(item => item.id)
}

const confirmResolve = async (ids) => {
  await ElMessageBox.confirm('确认标记选中的告警为已解决？', '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  })

  if (ids.length === 1) {
    return resolveAlertById(ids[0])
  } else {
    return batchResolveAlerts({ ids })
  }
}

const resolveSingleAlert = async (row) => {
  try {
    resolving.value = true
    const response = await confirmResolve([row.id])
    const message = response?.message === 'Alert already resolved'
      ? '该告警已是已解决状态'
      : '告警已标记为已解决'

    syncResolvedAlert(row.id)
    await refreshAll()

    if (detailVisible.value && detailRecord.value?.id === row.id) {
      await reloadDetail(row.id)
    }

    ElMessage.success(message)
  } catch (error) {
    if (!isCancelError(error)) {
      console.error('操作失败:', error)
      ElMessage.error('标记告警失败，请稍后重试')
    }
  } finally {
    resolving.value = false
  }
}

const handleBatchResolve = async () => {
  if (!selectedIds.value.length) return

  try {
    resolving.value = true
    const ids = [...selectedIds.value]
    const response = await confirmResolve(ids)

    const resolvedCount = response?.resolved_count ?? response?.resolved ?? 0
    const alreadyResolved = response?.already_resolved ?? 0
    const missingCount = Array.isArray(response?.missing_ids) ? response.missing_ids.length : 0

    ids.forEach(syncResolvedAlert)
    await refreshAll()

    if (detailVisible.value && ids.includes(detailRecord.value?.id)) {
      await reloadDetail(detailRecord.value.id)
    }

    const parts = []
    if (resolvedCount > 0) parts.push(`新解决 ${resolvedCount} 条`)
    if (alreadyResolved > 0) parts.push(`已是已解决 ${alreadyResolved} 条`)
    if (missingCount > 0) parts.push(`未找到 ${missingCount} 条`)

    ElMessage.success(parts.length ? parts.join('，') : '批量处理完成')
  } catch (error) {
    if (!isCancelError(error)) {
      console.error('批量处理失败:', error)
      ElMessage.error('批量处理失败，请稍后重试')
    }
  } finally {
    resolving.value = false
  }
}

const handleExport = async () => {
  try {
    exporting.value = true
    const { page, page_size, ...params } = buildQueryParams()
    const blob = await exportAlerts(params)
    const downloadUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = `alerts-${dayjs().format('YYYYMMDD-HHmmss')}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(downloadUrl)
    ElMessage.success('告警导出成功')
  } catch (error) {
    console.error('导出失败:', error)
    ElMessage.error('告警导出失败，请稍后重试')
  } finally {
    exporting.value = false
  }
}

const openDetail = async (row) => {
  detailVisible.value = true
  detailRecord.value = row
  await reloadDetail(row.id)
}

const goToTerminal = (assetId) => {
  if (!assetId) {
    ElMessage.warning('该告警未关联终端')
    return
  }
  router.push({ name: 'TerminalDetail', params: { id: assetId } })
}

const formatMetricValue = (row) => {
  const value = Number(row.current_value)
  if (row.alert_type === 'cpu' || row.alert_type === 'memory' || row.alert_type === 'disk') {
    return `${value.toFixed(1)}%`
  }
  if (row.alert_type === 'offline') {
    return `${value.toFixed(1)}s`
  }
  return value.toFixed(1)
}

const stringifyDetails = (details) => {
  return typeof details === 'string' ? details : JSON.stringify(details, null, 2)
}

const getSeverityType = (severity) => {
  const map = { critical: 'danger', error: 'danger', warning: 'warning', info: 'info' }
  return map[severity] || ''
}

const getSeverityText = (severity) => {
  const map = { critical: '严重', error: '错误', warning: '警告', info: '信息' }
  return map[severity] || severity
}

const getTypeText = (type) => {
  const map = {
    cpu: 'CPU',
    memory: '内存',
    disk: '磁盘',
    offline: '离线',
    health: '健康度',
    warranty: '保修'
  }
  return map[type] || type
}

onMounted(() => {
  refreshAll()
  refreshTimer.value = window.setInterval(() => {
    refreshAll({ silent: true })
  }, 30000)
})

onBeforeUnmount(() => {
  if (refreshTimer.value) {
    window.clearInterval(refreshTimer.value)
    refreshTimer.value = null
  }
})
</script>

<style scoped>
.stats-row {
  margin-bottom: 20px;
}

.stat-card {
  height: 100%;
  min-height: 140px;
}

.stat-content {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-height: 100px;
  text-align: center;
}

.stat-title {
  color: #909399;
  font-size: 14px;
  margin-bottom: 10px;
}

.stat-tags {
  display: flex;
  justify-content: space-around;
}

.stat-number {
  font-size: 20px;
  font-weight: bold;
  margin-top: 5px;
}

.type-summary {
  display: flex;
  justify-content: space-around;
  flex-wrap: wrap;
  gap: 10px;
}

.type-summary-item {
  min-width: 60px;
}

.type-summary-value {
  font-size: 16px;
  font-weight: bold;
  margin-top: 4px;
}

.toolbar {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar-main {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.toolbar-filters,
.toolbar-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.quick-range-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}

.quick-range-label {
  color: #606266;
  font-size: 13px;
}

.sub-text {
  font-size: 12px;
  color: #909399;
}

.detail-descriptions {
  margin-bottom: 16px;
}

.details-card pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: Consolas, Monaco, monospace;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>
