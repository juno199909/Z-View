<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">日志总览</h2>
        <div class="zv-page-subtitle">系统全量日志 · 实时滚动 {{ stats.total || 0 }} 条</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
        <el-button type="primary" plain :icon="Download" @click="handleExport">导出</el-button>
      </div>
    </div>

    <div class="zv-log-stats">
      <div class="zv-stat-mini"><div class="zv-stat-num">{{ stats.total || 0 }}</div><div class="zv-stat-lbl">日志总量</div></div>
      <div class="zv-stat-mini zv-stat-info"><div class="zv-stat-num">{{ stats.total_24h || 0 }}</div><div class="zv-stat-lbl">24h 新增</div></div>
      <div class="zv-stat-mini zv-stat-danger"><div class="zv-stat-num">{{ stats.error_count || 0 }}</div><div class="zv-stat-lbl">错误</div></div>
      <div class="zv-stat-mini zv-stat-warning"><div class="zv-stat-num">{{ stats.warning_count || 0 }}</div><div class="zv-stat-lbl">警告</div></div>
    </div>

    <div class="zv-card">
      <div class="zv-filter-bar">
        <el-form :inline="true">
          <el-form-item label="级别">
            <el-select v-model="filters.level" placeholder="全部" clearable style="width: 110px" @change="handleFilterChange">
              <el-option label="信息" value="info" />
              <el-option label="警告" value="warning" />
              <el-option label="错误" value="error" />
            </el-select>
          </el-form-item>
          <el-form-item label="来源">
            <el-input v-model="filters.source_type" placeholder="来源" clearable style="width: 140px" @keyup.enter="handleFilterChange" />
          </el-form-item>
          <el-form-item label="模块">
            <el-input v-model="filters.module" placeholder="模块" clearable style="width: 140px" @keyup.enter="handleFilterChange" />
          </el-form-item>
          <el-form-item label="关键字">
            <el-input v-model="filters.keyword" placeholder="主机 / IP / 内容" clearable style="width: 220px" @keyup.enter="handleFilterChange" />
          </el-form-item>
          <el-form-item label="时间">
            <el-date-picker
              v-model="timeRange"
              type="datetimerange"
              range-separator="至"
              start-placeholder="开始"
              end-placeholder="结束"
              value-format="YYYY-MM-DD HH:mm:ss"
              style="width: 360px"
              @change="handleFilterChange"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="handleFilterChange">查询</el-button>
            <el-button :icon="RefreshLeft" @click="handleReset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="tableData">
        <el-table-column label="时间" width="170">
          <template #default="{ row }">
            <span class="zv-mono">{{ formatTime(row.event_time) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="级别" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="getLevelType(row.level)" effect="light">{{ getLevelText(row.level) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="source_type" label="来源" width="110" />
        <el-table-column prop="module" label="模块" width="120" />
        <el-table-column label="主机" width="160">
          <template #default="{ row }">
            <div class="zv-host-cell-mini">
              <span class="zv-host-name">{{ row.hostname || '-' }}</span>
              <span class="zv-host-ip">{{ row.ip_address || '' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="消息" min-width="280" show-overflow-tooltip />
        <el-table-column label="操作" width="100" align="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="openDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无日志" :image-size="80" /></template>
      </el-table>

      <div class="zv-pagination">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :total="pagination.total"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @size-change="loadData"
          @current-change="loadData"
        />
      </div>
    </div>

    <el-drawer v-model="detailVisible" title="日志详情" size="640px" destroy-on-close>
      <div v-if="currentLog" class="zv-log-detail">
        <div class="zv-detail-row"><span class="zv-label">时间</span><span class="zv-mono">{{ formatTime(currentLog.event_time) }}</span></div>
        <div class="zv-detail-row"><span class="zv-label">级别</span><el-tag size="small" :type="getLevelType(currentLog.level)">{{ getLevelText(currentLog.level) }}</el-tag></div>
        <div class="zv-detail-row"><span class="zv-label">来源</span>{{ currentLog.source_type }}</div>
        <div class="zv-detail-row"><span class="zv-label">模块</span>{{ currentLog.module }}</div>
        <div class="zv-detail-row"><span class="zv-label">主机</span>{{ currentLog.hostname || '-' }} <span class="zv-mono">({{ currentLog.ip_address || '-' }})</span></div>
        <div class="zv-detail-row zv-detail-full"><span class="zv-label">消息</span>{{ currentLog.message }}</div>
        <div v-if="currentLog.context" class="zv-detail-row zv-detail-full">
          <span class="zv-label">上下文</span>
          <pre class="zv-pre">{{ JSON.stringify(currentLog.context, null, 2) }}</pre>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search, RefreshLeft, Refresh, Download } from '@element-plus/icons-vue'
import { getLogList, getLogStats, exportLogs } from '@/api/log'
import dayjs from 'dayjs'

const loading = ref(false)
const tableData = ref([])
const stats = ref({})
const currentLog = ref(null)
const detailVisible = ref(false)

const filters = reactive({ level: '', source_type: '', module: '', keyword: '', asset_id: '' })
const timeRange = ref([])
const pagination = reactive({ page: 1, page_size: 50, total: 0 })

const loadStats = async () => {
  try { stats.value = await getLogStats() } catch {}
}

const loadData = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.page_size,
      level: filters.level || undefined,
      source_type: filters.source_type || undefined,
      module: filters.module || undefined,
      keyword: filters.keyword || undefined,
      asset_id: filters.asset_id || undefined,
      start_time: timeRange.value?.[0] || undefined,
      end_time: timeRange.value?.[1] || undefined
    }
    const res = await getLogList(params)
    tableData.value = res.data || []
    pagination.total = res.total || 0
  } catch (error) {
    console.error('加载日志失败:', error)
  } finally {
    loading.value = false
  }
}

const handleFilterChange = () => { pagination.page = 1; loadData() }
const handleReset = () => {
  Object.assign(filters, { level: '', source_type: '', module: '', keyword: '', asset_id: '' })
  timeRange.value = []
  handleFilterChange()
}

const handleExport = async () => {
  try {
    // 导出与列表使用相同的筛选条件，保证所见即所得
    const blob = await exportLogs({
      level: filters.level || undefined,
      source_type: filters.source_type || undefined,
      module: filters.module || undefined,
      asset_id: filters.asset_id || undefined,
      keyword: filters.keyword || undefined,
      start_time: timeRange.value?.[0] || undefined,
      end_time: timeRange.value?.[1] || undefined
    })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `logs-${dayjs().format('YYYYMMDD-HHmmss')}.xlsx`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    ElMessage.error('导出失败')
  }
}

const openDetail = (row) => {
  currentLog.value = row
  detailVisible.value = true
}

const getLevelType = (l) => ({ error: 'danger', warning: 'warning', info: 'info' }[l] || 'info')
const getLevelText = (l) => ({ error: '错误', warning: '警告', info: '信息' }[l] || l)
const formatTime = (v) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'

onMounted(() => { loadStats(); loadData() })
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1600px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-log-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.zv-stat-mini {
  background: $bg-card;
  border: 1px solid $border-color-light;
  border-left: 3px solid $border-color;
  border-radius: $border-radius;
  padding: 14px 18px;
  box-shadow: $shadow-xs;

  &.zv-stat-info    { border-left-color: $info-color; }
  &.zv-stat-danger  { border-left-color: $danger-color; }
  &.zv-stat-warning { border-left-color: $warning-color; }
}

.zv-stat-num {
  font-size: 22px;
  font-weight: 700;
  color: $text-primary;
  font-family: $font-mono;
  line-height: 1;
}

.zv-stat-lbl {
  font-size: 12px;
  color: $text-secondary;
  margin-top: 4px;
}

.zv-card { padding: 0; }

.zv-filter-bar {
  padding: 18px 24px;
  border-bottom: 1px solid $border-color-light;
  background: $slate-50;
}

:deep(.el-form-item) { margin-bottom: 0; margin-right: 12px; }
:deep(.el-input__wrapper),
:deep(.el-select__wrapper) {
  background: $bg-card;
  box-shadow: none;
  border-radius: $border-radius;
  transition: all $transition-base;
  &:hover { box-shadow: 0 0 0 1px $brand-primary-100; }
  &.is-focus { box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.10); }
}

.zv-host-cell-mini { line-height: 1.3; }
.zv-host-name { font-size: 13px; font-weight: 600; color: $text-primary; }
.zv-host-ip { display: block; font-size: 11px; color: $text-tertiary; font-family: $font-mono; margin-top: 2px; }
.zv-mono { font-family: $font-mono; font-size: 12px; color: $text-secondary; }

.zv-pagination {
  padding: 16px 22px;
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid $border-color-light;
}

.zv-log-detail {
  padding: 0 20px;
}

.zv-detail-row {
  display: flex;
  padding: 12px 0;
  border-bottom: 1px solid $border-color-light;
  gap: 16px;

  &.zv-detail-full { flex-direction: column; }
}

.zv-label {
  font-size: 12px;
  color: $text-tertiary;
  width: 60px;
  flex-shrink: 0;
}

.zv-pre {
  background: $slate-50;
  padding: 12px;
  border-radius: $border-radius;
  font-family: $font-mono;
  font-size: 12px;
  color: $text-primary;
  margin-top: 8px;
  overflow-x: auto;
}

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell {
    background: #fafbfc;
    color: $text-secondary;
    font-weight: 600;
    font-size: 12px;
  }
  tr:hover > td.el-table__cell { background: rgba(37, 99, 235, 0.03) !important; }
  td.el-table__cell { border-bottom: 1px solid $slate-100 !important; }
  .el-table__inner-wrapper::before { height: 0; }
}
</style>
