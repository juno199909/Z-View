<template>
  <div class="log-page app-container">
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="日志总量" :value="stats.total" />
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="24小时新增" :value="stats.total_24h" />
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="错误日志" :value="stats.error_count" />
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover" class="stat-card">
          <el-statistic title="告警/警告" :value="stats.warning_count" />
        </el-card>
      </el-col>
    </el-row>

    <el-card class="table-card">
      <div class="toolbar">
        <div class="toolbar-filters">
          <el-select v-model="filters.source_type" clearable placeholder="来源" style="width: 140px" @change="handleFilterChange">
            <el-option v-for="option in sourceTypeOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-select v-model="filters.module" clearable placeholder="模块" style="width: 160px" @change="handleFilterChange">
            <el-option v-for="option in moduleOptions" :key="option.value" :label="option.label" :value="option.value" />
          </el-select>
          <el-select v-model="filters.level" clearable placeholder="级别" style="width: 120px" @change="handleFilterChange">
            <el-option label="信息" value="info" />
            <el-option label="警告" value="warning" />
            <el-option label="错误" value="error" />
          </el-select>
          <el-input
            v-model="filters.asset_id"
            placeholder="资产ID"
            clearable
            style="width: 120px"
            @clear="handleFilterChange"
            @keyup.enter="handleFilterChange"
          />
          <el-input
            v-model="filters.keyword"
            placeholder="主机 / IP / 内容"
            clearable
            style="width: 220px"
            @clear="handleFilterChange"
            @keyup.enter="handleFilterChange"
          />
          <el-date-picker
            v-model="timeRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            value-format="YYYY-MM-DD HH:mm:ss"
            @change="handleFilterChange"
          />
        </div>
        <div class="toolbar-actions">
          <el-switch v-model="autoRefresh" inline-prompt active-text="自动刷新" inactive-text="手动" />
          <el-button @click="handleFilterChange">查询</el-button>
          <el-button :icon="Refresh" :loading="loading" @click="refreshAll">刷新</el-button>
        </div>
      </div>

      <div class="summary-tags">
        <div class="summary-group">
          <span class="summary-label">按级别</span>
          <el-tag v-for="(count, level) in stats.by_level" :key="level" :type="getLevelTagType(level)" effect="plain">
            {{ getLevelText(level) }} {{ count }}
          </el-tag>
        </div>
        <div class="summary-group">
          <span class="summary-label">按模块</span>
          <el-tag v-for="(count, module) in stats.by_module" :key="module" effect="plain">
            {{ formatModuleText(module) }} {{ count }}
          </el-tag>
        </div>
      </div>

      <el-table :data="logs" v-loading="loading" row-key="id" style="width: 100%">
        <el-table-column prop="event_time" label="时间" width="168" />
        <el-table-column label="来源 / 模块" width="170">
          <template #default="{ row }">
            <div>{{ formatSourceText(row.source_type) }}</div>
            <div class="muted-text">{{ formatModuleText(row.module) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="主机" width="170">
          <template #default="{ row }">
            <div>{{ row.hostname || '-' }}</div>
            <div class="muted-text">{{ row.ip_address || '-' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="级别" width="90">
          <template #default="{ row }">
            <el-tag :type="getLevelTagType(row.level)" size="small">{{ getLevelText(row.level) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="结果" width="120">
          <template #default="{ row }">
            <el-tag :type="getResultTagType(row.result)" size="small">{{ row.result || '-' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="action" label="动作" width="130" show-overflow-tooltip />
        <el-table-column label="标题 / 内容" min-width="340" show-overflow-tooltip>
          <template #default="{ row }">
            <div class="log-title">{{ row.title || row.action }}</div>
            <div class="log-message">{{ row.message }}</div>
          </template>
        </el-table-column>
        <el-table-column label="操作员" width="110">
          <template #default="{ row }">
            {{ row.operator_name || '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination-container">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[20, 50, 100, 200]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @current-change="loadLogs"
          @size-change="handlePageSizeChange"
        />
      </div>
    </el-card>

    <el-drawer v-model="detailVisible" title="日志详情" size="55%">
      <template v-if="selectedLog">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="时间">{{ selectedLog.event_time }}</el-descriptions-item>
          <el-descriptions-item label="来源">{{ formatSourceText(selectedLog.source_type) }}</el-descriptions-item>
          <el-descriptions-item label="模块">{{ formatModuleText(selectedLog.module) }}</el-descriptions-item>
          <el-descriptions-item label="动作">{{ selectedLog.action || '-' }}</el-descriptions-item>
          <el-descriptions-item label="级别">
            <el-tag :type="getLevelTagType(selectedLog.level)" size="small">{{ getLevelText(selectedLog.level) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="结果">{{ selectedLog.result || '-' }}</el-descriptions-item>
          <el-descriptions-item label="主机">{{ selectedLog.hostname || '-' }}</el-descriptions-item>
          <el-descriptions-item label="IP">{{ selectedLog.ip_address || '-' }}</el-descriptions-item>
          <el-descriptions-item label="会话ID">{{ selectedLog.session_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="资产ID">{{ selectedLog.asset_id || '-' }}</el-descriptions-item>
          <el-descriptions-item label="操作员">{{ selectedLog.operator_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="分类">{{ selectedLog.category || '-' }}</el-descriptions-item>
        </el-descriptions>

        <el-card class="detail-card" shadow="never">
          <template #header>消息</template>
          <div class="detail-text">{{ selectedLog.message || '-' }}</div>
        </el-card>

        <el-card v-if="selectedLog.details" class="detail-card" shadow="never">
          <template #header>详情</template>
          <pre class="code-block">{{ formatJson(selectedLog.details) }}</pre>
        </el-card>

        <el-card v-if="selectedLog.stdout_log" class="detail-card" shadow="never">
          <template #header>标准输出</template>
          <pre class="code-block">{{ selectedLog.stdout_log }}</pre>
        </el-card>

        <el-card v-if="selectedLog.stderr_log" class="detail-card" shadow="never">
          <template #header>错误输出</template>
          <pre class="code-block error-block">{{ selectedLog.stderr_log }}</pre>
        </el-card>
      </template>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { getLogList, getLogStats } from '@/api/log'

const loading = ref(false)
const logs = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(50)
const autoRefresh = ref(true)
const refreshTimer = ref(null)
const timeRange = ref([])
const detailVisible = ref(false)
const selectedLog = ref(null)

const filters = reactive({
  source_type: '',
  module: '',
  level: '',
  asset_id: '',
  keyword: ''
})

const stats = ref({
  total: 0,
  total_24h: 0,
  total_7days: 0,
  error_count: 0,
  warning_count: 0,
  by_level: {},
  by_module: {},
  by_source_type: {}
})

const sourceTypeOptions = [
  { label: 'Agent', value: 'agent' },
  { label: '平台', value: 'platform' },
  { label: '告警中心', value: 'alert' },
  { label: '软件任务', value: 'software_task' },
  { label: '策略日志', value: 'policy_log' },
  { label: '审计日志', value: 'software_audit' }
]

const moduleOptions = computed(() => {
  const statModules = Object.keys(stats.value.by_module || {})
  const merged = ['agent', 'auth', 'remote_command', 'remote_desktop', 'software_task', 'software_management', 'software_policy', 'alert_center']
  return [...new Set([...merged, ...statModules])]
    .filter(Boolean)
    .map(item => ({ label: formatModuleText(item), value: item }))
})

const buildQueryParams = () => {
  const params = {
    page: currentPage.value,
    page_size: pageSize.value
  }
  if (filters.source_type) params.source_type = filters.source_type
  if (filters.module) params.module = filters.module
  if (filters.level) params.level = filters.level
  if (filters.asset_id !== '') params.asset_id = Number(filters.asset_id)
  if (filters.keyword.trim()) params.keyword = filters.keyword.trim()
  if (timeRange.value?.length === 2) {
    params.start_time = timeRange.value[0]
    params.end_time = timeRange.value[1]
  }
  return params
}

const loadStats = async () => {
  const params = {}
  if (timeRange.value?.length === 2) {
    params.start_time = timeRange.value[0]
    params.end_time = timeRange.value[1]
  }
  stats.value = await getLogStats(params)
}

const loadLogs = async () => {
  loading.value = true
  try {
    const response = await getLogList(buildQueryParams())
    logs.value = response.data || []
    total.value = response.total || 0
  } finally {
    loading.value = false
  }
}

const refreshAll = async () => {
  await Promise.all([loadStats(), loadLogs()])
}

const handleFilterChange = () => {
  currentPage.value = 1
  refreshAll()
}

const handlePageSizeChange = () => {
  currentPage.value = 1
  loadLogs()
}

const openDetail = row => {
  selectedLog.value = row
  detailVisible.value = true
}

const formatSourceText = value => {
  return sourceTypeOptions.find(item => item.value === value)?.label || value || '-'
}

const formatModuleText = value => {
  const mapping = {
    agent: 'Agent',
    auth: '登录鉴权',
    remote_command: '远程命令',
    remote_desktop: '远程桌面',
    software_task: '软件任务',
    software_management: '软件管理',
    software_policy: '软件策略',
    alert_center: '告警中心'
  }
  return mapping[value] || value || '-'
}

const getLevelText = value => {
  return {
    info: '信息',
    warning: '警告',
    error: '错误'
  }[value] || value || '-'
}

const getLevelTagType = value => {
  return {
    info: 'info',
    warning: 'warning',
    error: 'danger'
  }[value] || 'info'
}

const getResultTagType = value => {
  if (!value) return 'info'
  if (['success', 'approved', 'started', 'closed', 'resolved'].includes(value)) return 'success'
  if (['failed', 'rejected', 'error'].includes(value)) return 'danger'
  if (['warning', 'timeout', 'blocked', 'pending', 'partial', 'cancelled'].includes(value)) return 'warning'
  return 'info'
}

const formatJson = value => {
  if (typeof value === 'string') {
    return value
  }
  return JSON.stringify(value, null, 2)
}

const startRefreshTimer = () => {
  if (refreshTimer.value) {
    clearInterval(refreshTimer.value)
  }
  if (autoRefresh.value) {
    refreshTimer.value = setInterval(() => {
      refreshAll()
    }, 15000)
  }
}

watch(autoRefresh, () => {
  startRefreshTimer()
})

onMounted(() => {
  refreshAll()
  startRefreshTimer()
})

onBeforeUnmount(() => {
  if (refreshTimer.value) {
    clearInterval(refreshTimer.value)
    refreshTimer.value = null
  }
})
</script>

<style lang="scss" scoped>
.log-page {
  padding: 20px;
}

.stats-row {
  margin-bottom: 20px;
}

.stat-card {
  min-height: 110px;
}

.table-card {
  border-radius: 12px;
}

.toolbar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.toolbar-filters,
.toolbar-actions,
.summary-group {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.summary-tags {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 16px;
}

.summary-label {
  color: #606266;
  font-size: 13px;
}

.muted-text {
  color: #909399;
  font-size: 12px;
}

.log-title {
  font-weight: 600;
  color: #303133;
  margin-bottom: 4px;
}

.log-message {
  color: #606266;
  line-height: 1.5;
}

.pagination-container {
  display: flex;
  justify-content: flex-end;
  margin-top: 20px;
}

.detail-card {
  margin-top: 16px;
}

.detail-text,
.code-block {
  white-space: pre-wrap;
  word-break: break-word;
  margin: 0;
  font-family: Consolas, 'Courier New', monospace;
  line-height: 1.6;
}

.code-block {
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 10px;
  padding: 14px;
  overflow: auto;
}

.error-block {
  color: #fecaca;
}

@media (max-width: 768px) {
  .log-page {
    padding: 12px;
  }

  .toolbar {
    flex-direction: column;
    align-items: stretch;
  }

  .toolbar-actions {
    justify-content: flex-end;
  }
}
</style>
