<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端日志</h2>
        <div class="zv-page-subtitle">共 {{ pagination.total }} 条告警 · 待处理 {{ stats.unresolved || 0 }} 条</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
        <el-button type="primary" :icon="Check" :disabled="!selectedIds.length" @click="handleBatchResolve">批量解决</el-button>
      </div>
    </div>

    <!-- 告警统计 -->
    <div class="zv-alert-stats">
      <div class="zv-stat-mini zv-stat-danger">
        <div class="zv-stat-num">{{ stats.by_severity?.critical || 0 }}</div>
        <div class="zv-stat-lbl">严重</div>
      </div>
      <div class="zv-stat-mini zv-stat-warning">
        <div class="zv-stat-num">{{ (stats.by_severity?.high || 0) + (stats.by_severity?.medium || 0) }}</div>
        <div class="zv-stat-lbl">高危/中等</div>
      </div>
      <div class="zv-stat-mini zv-stat-info">
        <div class="zv-stat-num">{{ stats.active || 0 }}</div>
        <div class="zv-stat-lbl">未解决</div>
      </div>
      <div class="zv-stat-mini zv-stat-success">
        <div class="zv-stat-num">{{ stats.resolved || 0 }}</div>
        <div class="zv-stat-lbl">已解决</div>
      </div>
    </div>

    <div class="zv-card">
      <div class="zv-filter-bar">
        <el-form :inline="true" :model="searchForm">
          <el-form-item label="级别">
            <el-select v-model="searchForm.severity" placeholder="全部" clearable style="width: 110px">
              <el-option label="严重" value="critical" />
              <el-option label="高危" value="high" />
              <el-option label="中等" value="medium" />
              <el-option label="低危" value="low" />
              <el-option label="提示" value="info" />
            </el-select>
          </el-form-item>
          <el-form-item label="状态">
            <el-select v-model="searchForm.status" placeholder="全部" clearable style="width: 110px">
              <el-option label="未解决" value="active" />
              <el-option label="已解决" value="resolved" />
            </el-select>
          </el-form-item>
          <el-form-item label="类型">
            <el-select v-model="searchForm.alert_type" placeholder="全部" clearable style="width: 110px">
              <el-option label="离线" value="offline" />
              <el-option label="CPU" value="cpu" />
              <el-option label="内存" value="memory" />
              <el-option label="磁盘" value="disk" />
              <el-option label="进程" value="process" />
            </el-select>
          </el-form-item>
          <el-form-item label="来源主机">
            <el-input v-model="searchForm.hostname" placeholder="主机名 / IP" clearable style="width: 150px" @keyup.enter="handleSearch" />
          </el-form-item>
          <el-form-item label="关键字">
            <el-input v-model="searchForm.keyword" placeholder="搜索告警内容" clearable style="width: 180px" @keyup.enter="handleSearch" />
          </el-form-item>
          <el-form-item label="时间">
            <el-date-picker
              v-model="timeRange"
              type="datetimerange"
              range-separator="至"
              start-placeholder="开始"
              end-placeholder="结束"
              value-format="YYYY-MM-DD HH:mm:ss"
              style="width: 340px"
              @change="handleSearch"
            />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :icon="Search" @click="handleSearch">查询</el-button>
            <el-button :icon="RefreshLeft" @click="handleReset">重置</el-button>
          </el-form-item>
        </el-form>
      </div>

      <el-table v-loading="loading" :data="tableData" @selection-change="handleSelectionChange">
        <el-table-column type="selection" width="48" />
        <el-table-column label="级别" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="getLevelType(row.severity)" effect="dark">{{ getLevelText(row.severity) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="告警内容" min-width="260" show-overflow-tooltip />
        <el-table-column label="来源" width="160">
          <template #default="{ row }">
            <span class="zv-source">{{ row.hostname || row.source || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <span v-if="row.status === 'resolved'" class="zv-alert-resolved">已解决</span>
            <span v-else class="zv-alert-unresolved">未解决</span>
          </template>
        </el-table-column>
        <el-table-column label="触发时间" width="170">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="140" fixed="right" align="right">
          <template #default="{ row }">
            <el-button v-if="row.status !== 'resolved'" text type="primary" size="small" @click="handleResolve(row.id)">解决</el-button>
            <el-button v-else text type="info" size="small" disabled>已处理</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无告警数据" :image-size="80" />
        </template>
      </el-table>

      <div class="zv-pagination">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :total="pagination.total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @size-change="loadData"
          @current-change="loadData"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, RefreshLeft, Refresh, Check } from '@element-plus/icons-vue'
import { getAlertList, getAlertStats, resolveAlertById, batchResolveAlerts } from '@/api/alert'
import dayjs from 'dayjs'

const loading = ref(false)
const tableData = ref([])
const selectedIds = ref([])
const stats = ref({ critical: 0, warning: 0, info: 0, resolved: 0, unresolved: 0 })

const searchForm = reactive({ severity: '', status: '', alert_type: '', hostname: '', keyword: '' })
const timeRange = ref([])
const pagination = reactive({ page: 1, page_size: 20, total: 0 })

const loadStats = async () => {
  try {
    const data = await getAlertStats()
    stats.value = data || stats.value
  } catch {}
}

const loadData = async () => {
  loading.value = true
  try {
    const res = await getAlertList({
      page: pagination.page,
      page_size: pagination.page_size,
      severity: searchForm.severity || undefined,
      status: searchForm.status || undefined,
      alert_type: searchForm.alert_type || undefined,
      hostname: searchForm.hostname || undefined,
      keyword: searchForm.keyword || undefined,
      start_time: timeRange.value?.[0] || undefined,
      end_time: timeRange.value?.[1] || undefined
    })
    tableData.value = res.data || []
    pagination.total = res.total || 0
  } catch (error) {
    console.error('加载告警失败:', error)
  } finally {
    loading.value = false
  }
}

const handleSelectionChange = (rows) => { selectedIds.value = rows.map(r => r.id) }
const handleSearch = () => { pagination.page = 1; loadData() }
const handleReset = () => {
  Object.assign(searchForm, { severity: '', status: '', alert_type: '', hostname: '', keyword: '' })
  timeRange.value = []
  handleSearch()
}

const handleResolve = async (id) => {
  try {
    await resolveAlertById(id)
    ElMessage.success('告警已解决')
    loadData()
  } catch (error) {
    ElMessage.error('解决失败')
  }
}

const handleBatchResolve = async () => {
  if (!selectedIds.value.length) return
  try {
    await ElMessageBox.confirm(`确定解决选中的 ${selectedIds.value.length} 条告警吗？`, '提示', { type: 'warning' })
    await batchResolveAlerts(selectedIds.value)
    selectedIds.value = []
    ElMessage.success('批量解决成功')
    loadData()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error('批量解决失败')
  }
}

const getLevelType = (l) => ({ critical: 'danger', high: 'danger', medium: 'warning', low: 'info', info: 'info' }[l] || 'info')
const getLevelText = (l) => ({ critical: '严重', high: '高危', medium: '中等', low: '低危', info: '提示' }[l] || (l || '-'))
const formatTime = (v) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'

onMounted(() => { loadStats(); loadData() })
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1600px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-alert-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.zv-stat-mini {
  background: $bg-card;
  border: 1px solid $border-color-light;
  border-left: 3px solid;
  border-radius: $border-radius;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 14px;
  box-shadow: $shadow-xs;

  &.zv-stat-danger  { border-left-color: $danger-color; }
  &.zv-stat-warning { border-left-color: $warning-color; }
  &.zv-stat-info    { border-left-color: $info-color; }
  &.zv-stat-success { border-left-color: $success-color; }
}

.zv-stat-num {
  font-size: 24px;
  font-weight: 700;
  color: $text-primary;
  font-family: $font-mono;
  line-height: 1;
}

.zv-stat-lbl {
  font-size: 13px;
  color: $text-secondary;
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

.zv-source {
  font-family: $font-mono;
  font-size: 12px;
  color: $text-secondary;
}

.zv-alert-resolved {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: $success-color;
  font-weight: 500;
  &::before { content: '✓'; font-weight: 700; }
}

.zv-alert-unresolved {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: $warning-color;
  font-weight: 500;
  &::before {
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: $warning-color;
    box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.18);
  }
}

.zv-pagination {
  padding: 16px 22px;
  display: flex;
  justify-content: flex-end;
  border-top: 1px solid $border-color-light;
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
