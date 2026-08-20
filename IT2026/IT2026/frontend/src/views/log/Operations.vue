<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">操作日志</h2>
        <div class="zv-page-subtitle">终端运维平台的操作审计 · 登录 / 远程运维 / 安全操作</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
      </div>
    </div>

    <div class="zv-card">
      <div class="zv-filter-bar">
        <el-form :inline="true">
          <el-form-item label="模块">
            <el-select v-model="filters.module" placeholder="全部" clearable style="width: 150px" @change="handleFilterChange">
              <el-option label="登录认证" value="auth" />
              <el-option label="远程运维" value="remote-desktop" />
              <el-option label="安全管理" value="security" />
            </el-select>
          </el-form-item>
          <el-form-item label="操作人">
            <el-input v-model="filters.operator" placeholder="操作人" clearable style="width: 140px" @keyup.enter="handleFilterChange" />
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
        <el-table-column label="操作人" width="130">
          <template #default="{ row }">
            <span>{{ row.operator_name || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="module" label="模块" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ getModuleText(row.module) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="action" label="动作" width="130" />
        <el-table-column label="结果" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="getResultType(row.result)">{{ getResultText(row.result) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="目标主机" width="160">
          <template #default="{ row }">
            <div class="zv-host-cell-mini">
              <span class="zv-host-name">{{ row.hostname || '-' }}</span>
              <span class="zv-host-ip">{{ row.ip_address || '' }}</span>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="message" label="内容" min-width="260" show-overflow-tooltip />
        <template #empty><el-empty description="暂无操作日志" :image-size="80" /></template>
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
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Search, RefreshLeft, Refresh } from '@element-plus/icons-vue'
import { getLogList } from '@/api/log'
import dayjs from 'dayjs'

const loading = ref(false)
const tableData = ref([])
const filters = reactive({ module: '', operator: '', keyword: '' })
const timeRange = ref([])
const pagination = reactive({ page: 1, page_size: 50, total: 0 })

const loadData = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.page_size,
      source_type: 'platform',
      module: filters.module || undefined,
      keyword: filters.keyword || undefined,
      operator: filters.operator || undefined,
      start_time: timeRange.value?.[0] || undefined,
      end_time: timeRange.value?.[1] || undefined
    }
    const res = await getLogList(params)
    tableData.value = res.data || []
    pagination.total = res.total || 0
  } catch (error) {
    console.error('加载操作日志失败:', error)
  } finally {
    loading.value = false
  }
}

const handleFilterChange = () => { pagination.page = 1; loadData() }
const handleReset = () => {
  Object.assign(filters, { module: '', operator: '', keyword: '' })
  timeRange.value = []
  handleFilterChange()
}

const getModuleText = (m) => ({ auth: '登录认证', 'remote-desktop': '远程运维', security: '安全管理' }[m] || m)
const getResultType = (r) => ({ success: 'success', denied: 'danger', failed: 'danger' }[r] || (r === 'active' ? 'warning' : 'info'))
const getResultText = (r) => ({ success: '成功', denied: '拒绝', failed: '失败' }[r] || (r === 'active' ? '已触发' : (r || '-')))
const formatTime = (v) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'

onMounted(loadData)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1600px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }
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
}
</style>
