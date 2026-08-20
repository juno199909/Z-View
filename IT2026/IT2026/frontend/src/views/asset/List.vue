<template>
  <div class="zv-page">
    <!-- 页面头 -->
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端列表</h2>
        <div class="zv-page-subtitle">共 {{ pagination.total }} 台设备 · 在线 {{ stats.online || 0 }} 台</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Download" plain @click="handleExport">导出</el-button>
        <el-button type="primary" :icon="Plus" @click="handleCreate">新增资产</el-button>
      </div>
    </div>

    <!-- 搜索条 -->
    <div class="zv-card zv-filter-bar">
      <el-form :inline="true" :model="searchForm" class="zv-filter-form">
        <el-form-item label="资产类型">
          <el-select v-model="searchForm.asset_type" placeholder="全部" clearable style="width: 140px">
            <el-option label="服务器" value="server" />
            <el-option label="交换机" value="switch" />
            <el-option label="路由器" value="router" />
            <el-option label="PC 终端" value="pc" />
          </el-select>
        </el-form-item>

        <el-form-item label="运行状态">
          <el-select v-model="searchForm.status" placeholder="全部" clearable style="width: 120px">
            <el-option label="在线" value="online" />
            <el-option label="离线" value="offline" />
            <el-option label="未知" value="unknown" />
          </el-select>
        </el-form-item>

        <el-form-item label="所属分组">
          <el-select v-model="searchForm.group_id" placeholder="全部" clearable filterable style="width: 180px">
            <el-option v-for="g in groupList" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>

        <el-form-item label="关键字">
          <el-input v-model="searchForm.keyword" placeholder="主机名 / IP / MAC" clearable style="width: 220px" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :icon="Search" @click="handleSearch">查询</el-button>
          <el-button :icon="RefreshLeft" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <!-- 表格 -->
    <div class="zv-card zv-table-wrap">
      <div class="zv-table-toolbar">
        <div class="zv-table-info">
          <el-icon :size="14"><InfoFilled /></el-icon>
          <span v-if="selectedIds.length > 0">已选 <strong>{{ selectedIds.length }}</strong> 条</span>
          <span v-else>双击行查看详情</span>
        </div>
        <div class="zv-table-actions">
          <el-button type="danger" plain :icon="Delete" :disabled="!selectedIds.length" @click="handleBatchDelete">
            批量删除
          </el-button>
          <el-button :icon="Refresh" circle plain @click="loadData" />
        </div>
      </div>

      <el-table
        v-loading="loading"
        :data="tableData"
        @selection-change="handleSelectionChange"
        @row-dblclick="(row) => handleView(row.id)"
        :row-style="{ cursor: 'pointer' }"
      >
        <el-table-column type="selection" width="48" />
        <el-table-column label="主机名" min-width="180">
          <template #default="{ row }">
            <div class="zv-host-cell">
              <div class="zv-host-avatar" :style="{ background: getTypeGradient(row.asset_type) }">
                <el-icon :size="18"><component :is="getTypeIcon(row.asset_type)" /></el-icon>
              </div>
              <div class="zv-host-info">
                <div class="zv-host-name">{{ row.hostname || '-' }}</div>
                <div class="zv-host-ip">{{ row.ip_address || '-' }}</div>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="mac_address" label="MAC" width="160">
          <template #default="{ row }">
            <span class="zv-mono">{{ row.mac_address || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="light" :style="{ borderColor: getTypeColor(row.asset_type), color: getTypeColor(row.asset_type), background: getTypeBg(row.asset_type) }">
              {{ getTypeLabel(row.asset_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="分组" min-width="180">
          <template #default="{ row }">
            <el-select
              v-model="row.group_id"
              placeholder="未分组"
              size="small"
              clearable
              filterable
              @change="(val) => handleGroupChange(row, val)"
            >
              <el-option v-for="g in groupList" :key="g.id" :label="g.name" :value="g.id" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="zv-status-chip" :class="`is-${row.status || 'unknown'}`">
              <span class="zv-status-dot" :class="`is-${row.status || 'unknown'}`" />
              {{ getStatusLabel(row.status) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="location" label="位置" min-width="140" show-overflow-tooltip />
        <el-table-column label="最后在线" width="170">
          <template #default="{ row }">
            <span class="zv-time">{{ formatTime(row.last_seen) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right" align="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" :icon="View" @click="handleView(row.id)">详情</el-button>
            <el-button text type="primary" size="small" :icon="Edit" @click="handleEdit(row.id)">编辑</el-button>
            <el-button text type="danger" size="small" :icon="Delete" @click="handleDelete(row.id)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无资产数据" :image-size="100">
            <el-button type="primary" @click="handleCreate">新增资产</el-button>
          </el-empty>
        </template>
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
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Search, RefreshLeft, Plus, Delete, Refresh, Download,
  View, Edit, InfoFilled,
  Monitor, Box, Connection, Share, Cpu
} from '@element-plus/icons-vue'
import { batchDeleteAssets, deleteAsset, exportAssets, getAssetList, updateAssetGroup, getAssetStats } from '@/api/asset'
import { getGroups } from '@/api/group'
import dayjs from 'dayjs'

const router = useRouter()
const loading = ref(false)
const tableData = ref([])
const selectedIds = ref([])
const groupList = ref([])
const stats = ref({ online: 0, total: 0 })

const searchForm = reactive({
  asset_type: '',
  status: '',
  group_id: null,
  keyword: ''
})

const pagination = reactive({
  page: 1,
  page_size: 20,
  total: 0
})

const loadGroups = async () => {
  try {
    const res = await getGroups()
    groupList.value = res.data || []
  } catch (error) {
    console.error('加载分组失败:', error)
  }
}

const loadStats = async () => {
  try {
    const data = await getAssetStats()
    stats.value = { online: data.online || 0, total: data.total || 0 }
  } catch {}
}

const loadData = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.page_size,
      asset_type: searchForm.asset_type || undefined,
      status: searchForm.status || undefined,
      group_id: searchForm.group_id || undefined,
      keyword: searchForm.keyword || undefined
    }
    const res = await getAssetList(params)
    tableData.value = res.data || []
    pagination.total = res.total || 0
  } catch (error) {
    console.error('加载资产失败:', error)
  } finally {
    loading.value = false
  }
}

const handleGroupChange = async (row, val) => {
  try {
    await updateAssetGroup(row.id, val)
    ElMessage.success('分组更新成功')
  } catch (error) {
    console.error('分组更新失败:', error)
    ElMessage.error('分组更新失败')
    loadData()
  }
}

const handleSelectionChange = (rows) => {
  selectedIds.value = rows.map(item => item.id)
}

const handleSearch = () => {
  pagination.page = 1
  loadData()
}

const handleReset = () => {
  Object.assign(searchForm, { asset_type: '', status: '', group_id: null, keyword: '' })
  handleSearch()
}

const handleCreate = () => router.push('/asset/create')
const handleView = (id) => router.push(`/asset/detail/${id}`)
const handleEdit = (id) => router.push(`/asset/detail/${id}?edit=true`)

const handleDelete = async (id) => {
  try {
    await ElMessageBox.confirm('确定删除这条资产吗？', '提示', { type: 'warning' })
    await deleteAsset(id)
    ElMessage.success('删除成功')
    loadData()
  } catch (error) {
    if (error !== 'cancel') console.error('删除失败:', error)
  }
}

const handleBatchDelete = async () => {
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${selectedIds.value.length} 条资产吗？`, '提示', { type: 'warning' })
    await batchDeleteAssets(selectedIds.value)
    selectedIds.value = []
    ElMessage.success('删除成功')
    loadData()
  } catch (error) {
    if (error !== 'cancel') console.error('批量删除失败:', error)
  }
}

const handleExport = async () => {
  try {
    const blob = await exportAssets({
      asset_type: searchForm.asset_type || undefined,
      status: searchForm.status || undefined,
      group_id: searchForm.group_id || undefined,
      keyword: searchForm.keyword || undefined
    })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `assets-${dayjs().format('YYYYMMDD-HHmmss')}.xlsx`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
  } catch (error) {
    console.error('导出失败:', error)
  }
}

const TYPE_META = {
  server: { label: '服务器', icon: Cpu, color: '#3b82f6', bg: 'rgba(59,130,246,0.10)' },
  pc:     { label: 'PC 终端', icon: Monitor, color: '#10b981', bg: 'rgba(16,185,129,0.10)' },
  switch: { label: '交换机', icon: Connection, color: '#f59e0b', bg: 'rgba(245,158,11,0.10)' },
  router: { label: '路由器', icon: Share, color: '#8b5cf6', bg: 'rgba(139,92,246,0.10)' }
}
const getTypeLabel = (t) => TYPE_META[t]?.label || '未知'
const getTypeIcon = (t) => TYPE_META[t]?.icon || Box
const getTypeColor = (t) => TYPE_META[t]?.color || '#94a3b8'
const getTypeBg = (t) => TYPE_META[t]?.bg || 'rgba(148,163,184,0.10)'
const getTypeGradient = (t) => {
  const map = {
    server: 'linear-gradient(135deg, #3b82f6, #2563eb)',
    pc:     'linear-gradient(135deg, #10b981, #059669)',
    switch: 'linear-gradient(135deg, #f59e0b, #d97706)',
    router: 'linear-gradient(135deg, #8b5cf6, #7c3aed)'
  }
  return map[t] || 'linear-gradient(135deg, #94a3b8, #64748b)'
}

const STATUS_LABEL = { online: '在线', offline: '离线', degraded: '降级', unknown: '未知' }
const getStatusLabel = (s) => STATUS_LABEL[s] || '未知'

const formatTime = (v) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'

onMounted(async () => {
  await loadGroups()
  await loadStats()
  await loadData()
})
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page {
  padding: $content-padding;
  max-width: 1600px;
  margin: 0 auto;
}

.zv-page-actions {
  display: flex;
  gap: 10px;
}

.zv-filter-bar {
  padding: 20px 24px;
  margin-bottom: 16px;
}

.zv-filter-form {
  :deep(.el-form-item) {
    margin-bottom: 0;
    margin-right: 16px;
  }
  :deep(.el-form-item__label) {
    color: $text-secondary;
    font-weight: 500;
  }
  :deep(.el-input__wrapper),
  :deep(.el-select__wrapper) {
    background: $slate-50;
    box-shadow: none;
    border-radius: $border-radius;
    transition: all $transition-base;
    &:hover { background: $bg-card; box-shadow: 0 0 0 1px $brand-primary-100; }
    &.is-focus { background: $bg-card; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.10); }
  }
}

.zv-table-wrap {
  padding: 0;
  overflow: hidden;
}

.zv-table-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 22px;
  border-bottom: 1px solid $border-color-light;
  background: $slate-50;
}

.zv-table-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: $text-secondary;
  .el-icon { color: $text-tertiary; }
  strong { color: $brand-primary; font-weight: 600; }
}

.zv-table-actions {
  display: flex;
  gap: 8px;
}

// ---- 单元格 ----
.zv-host-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.zv-host-avatar {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
  flex-shrink: 0;
}

.zv-host-info {
  min-width: 0;
}

.zv-host-name {
  font-size: 13px;
  font-weight: 600;
  color: $text-primary;
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.zv-host-ip {
  font-size: 12px;
  color: $text-tertiary;
  margin-top: 2px;
  font-family: $font-mono;
}

.zv-mono {
  font-family: $font-mono;
  font-size: 12px;
  color: $text-secondary;
}

.zv-time {
  font-size: 12px;
  color: $text-secondary;
  font-family: $font-mono;
}

.zv-status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: $radius-pill;
  font-size: 12px;
  font-weight: 500;
  background: $slate-50;
  color: $text-secondary;

  &.is-online  { background: rgba(16, 185, 129, 0.10); color: $success-color; }
  &.is-offline { background: rgba(239, 68, 68, 0.10); color: $danger-color; }
  &.is-warning { background: rgba(245, 158, 11, 0.10); color: $warning-color; }
  &.is-unknown { background: $slate-100; color: $text-tertiary; }
}

.zv-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
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
    letter-spacing: 0.3px;
  }
  tr {
    transition: background $transition-fast;
  }
  tr:hover > td.el-table__cell {
    background: rgba(37, 99, 235, 0.03) !important;
  }
  td.el-table__cell {
    border-bottom: 1px solid $slate-100 !important;
  }
  .el-table__inner-wrapper::before {
    height: 0;
  }
}
</style>
