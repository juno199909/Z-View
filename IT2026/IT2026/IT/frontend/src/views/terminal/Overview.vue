<template>
  <div class="app-container">
    <!-- Web远程桌面组件 -->
    <WebRemoteDesktop
      v-if="showRemoteDesktop"
      :asset-id="currentAssetId"
      :ip-address="currentIpAddress"
      :hostname="currentHostname"
      @close="showRemoteDesktop = false"
    />

    <!-- 终端状态统计 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card online">
          <div class="stat-content">
            <div class="stat-icon">
              <el-icon :size="32"><CircleCheck /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.online }}</div>
              <div class="stat-label">在线终端</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card offline">
          <div class="stat-content">
            <div class="stat-icon">
              <el-icon :size="32"><CircleClose /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.offline }}</div>
              <div class="stat-label">离线终端</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card warning">
          <div class="stat-content">
            <div class="stat-icon">
              <el-icon :size="32"><Warning /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.risk }}</div>
              <div class="stat-label">风险终端</div>
            </div>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="12" :sm="6">
        <el-card shadow="hover" class="stat-card info">
          <div class="stat-content">
            <div class="stat-icon">
              <el-icon :size="32"><Monitor /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats.total }}</div>
              <div class="stat-label">终端总数</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 搜索和筛选 -->
    <el-card class="search-card" shadow="never">
      <el-form :inline="true" :model="searchForm">
        <el-form-item label="资产类型">
          <el-select v-model="searchForm.asset_type" placeholder="全部" clearable style="width: 150px">
            <el-option label="服务器" value="server" />
            <el-option label="交换机" value="switch" />
            <el-option label="路由器" value="router" />
            <el-option label="PC终端" value="pc" />
            <el-option label="未知" value="unknown" />
          </el-select>
        </el-form-item>

        <el-form-item label="状态">
          <el-select v-model="searchForm.status" placeholder="全部" clearable style="width: 120px">
            <el-option label="在线" value="online" />
            <el-option label="离线" value="offline" />
          </el-select>
        </el-form-item>

        <el-form-item label="分组">
          <el-select v-model="searchForm.group_id" placeholder="全部" clearable style="width: 150px">
            <el-option
              v-for="group in groups"
              :key="group.id"
              :label="group.name"
              :value="group.id"
            />
          </el-select>
        </el-form-item>

        <el-form-item label="关键字">
          <el-input
            v-model="searchForm.keyword"
            placeholder="主机名/IP/MAC"
            clearable
            style="width: 200px"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" @click="loadData">搜索</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 终端列表 -->
    <el-card shadow="never">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>终端列表 ({{ pagination.total }})</span>
          <div>
            <el-button
              type="danger"
              :disabled="selectedIds.length === 0"
              @click="handleBatchDelete"
            >
              批量删除
            </el-button>
            <el-button :icon="Download" @click="handleExport">导出</el-button>
            <el-button type="primary" :icon="Refresh" @click="loadData">刷新</el-button>
          </div>
        </div>
      </template>

      <div class="terminal-table-wrapper">
        <el-table
          class="terminal-table"
          :data="tableData"
          v-loading="loading"
          stripe
          border
          table-layout="fixed"
          :fit="false"
          @header-dragend="handleHeaderDragend"
          @selection-change="handleSelectionChange"
        >
          <el-table-column type="selection" width="55" />
          <el-table-column
            column-key="hostname"
            prop="hostname"
            label="主机名"
            :width="columnWidths.hostname"
            show-overflow-tooltip
          />
          <el-table-column
            column-key="ip_address"
            prop="ip_address"
            label="IP地址"
            :width="columnWidths.ip_address"
            show-overflow-tooltip
          />
          <el-table-column
            column-key="mac_address"
            prop="mac_address"
            label="MAC地址"
            :width="columnWidths.mac_address"
            show-overflow-tooltip
          />

          <el-table-column column-key="asset_type" label="资产类型" :width="columnWidths.asset_type">
            <template #default="{ row }">
              <el-tag :type="getTypeTagType(row.asset_type)">
                {{ getTypeLabel(row.asset_type) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column column-key="group_name" label="分组" :width="columnWidths.group_name">
            <template #default="{ row }">
              <el-select
                v-model="row.group_id"
                placeholder="选择分组"
                size="small"
                @change="handleGroupChange(row)"
                clearable
              >
                <el-option
                  v-for="group in groups"
                  :key="group.id"
                  :label="group.name"
                  :value="group.id"
                />
              </el-select>
            </template>
          </el-table-column>

          <el-table-column column-key="status" label="状态" :width="columnWidths.status">
            <template #default="{ row }">
              <el-tag :type="getStatusType(row.status)" size="small">
                {{ getStatusText(row.status) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column column-key="agent_install_status" label="Agent安装" :width="columnWidths.agent_install_status">
            <template #default="{ row }">
              <el-tag :type="getAgentInstallStatusType(row.agent_install_status)" size="small">
                {{ getAgentInstallStatusText(row.agent_install_status) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column column-key="os_type" label="系统" :width="columnWidths.os_type" show-overflow-tooltip>
            <template #default="{ row }">
              {{ row.os_type || '-' }}
            </template>
          </el-table-column>

          <el-table-column column-key="health" label="健康度" :width="columnWidths.health" align="center">
            <template #default="{ row }">
              <div v-if="row.status === 'online' && getHealthScore(row) !== null">
                <el-tag :type="getHealthLevel(getHealthScore(row)).type" size="small">
                  {{ getHealthLevel(getHealthScore(row)).text }}
                </el-tag>
                <div style="font-size: 12px; color: #909399; margin-top: 2px;">
                  {{ getHealthScore(row) }}分
                </div>
              </div>
              <span v-else style="color: #999;">-</span>
            </template>
          </el-table-column>

          <el-table-column column-key="cpu_usage" label="CPU" :width="columnWidths.cpu_usage">
            <template #default="{ row }">
              <el-progress
                v-if="row.status === 'online' && row.cpu_usage !== undefined"
                :percentage="row.cpu_usage"
                :color="getProgressColor(row.cpu_usage)"
                :stroke-width="8"
              />
              <span v-else style="color: #999;">-</span>
            </template>
          </el-table-column>

          <el-table-column column-key="memory_usage" label="内存" :width="columnWidths.memory_usage">
            <template #default="{ row }">
              <el-progress
                v-if="row.status === 'online' && row.memory_usage !== undefined"
                :percentage="row.memory_usage"
                :color="getProgressColor(row.memory_usage)"
                :stroke-width="8"
              />
              <span v-else style="color: #999;">-</span>
            </template>
          </el-table-column>

          <el-table-column column-key="last_seen" label="最后心跳" :width="columnWidths.last_seen">
            <template #default="{ row }">
              {{ formatTime(row.last_seen) }}
            </template>
          </el-table-column>

          <el-table-column column-key="actions" label="操作" :width="columnWidths.actions" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" link size="small" @click="viewDetail(row)">详情</el-button>
              <el-button
                v-if="row.status === 'online'"
                type="success"
                link
                size="small"
                @click="openRemoteDesktop(row)"
              >
                远程
              </el-button>
              <el-button type="danger" link size="small" @click="handleDelete(row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 分页 -->
      <div style="margin-top: 20px; display: flex; justify-content: flex-end;">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :page-sizes="[10, 20, 50, 100]"
          :total="pagination.total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadData"
          @current-change="loadData"
        />
      </div>

      <el-empty v-if="!tableData.length && !loading" description="暂无终端数据" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CircleCheck, CircleClose, Warning, Monitor, Refresh, Download } from '@element-plus/icons-vue'
import { getAssetList, getAssetStats, deleteAsset, batchDeleteAssets, exportAssets, updateAsset } from '@/api/asset'
import { getGroups } from '@/api/group'
import WebRemoteDesktop from '@/components/WebRemoteDesktop.vue'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

const router = useRouter()
const loading = ref(false)
const tableData = ref([])
const groups = ref([])
const selectedIds = ref([])
const COLUMN_WIDTH_STORAGE_KEY = 'terminal-overview-column-widths'
const DEFAULT_COLUMN_WIDTHS = Object.freeze({
  hostname: 180,
  ip_address: 150,
  mac_address: 170,
  asset_type: 110,
  group_name: 160,
  status: 100,
  agent_install_status: 120,
  os_type: 200,
  health: 110,
  cpu_usage: 120,
  memory_usage: 120,
  last_seen: 150,
  actions: 240
})

const getInitialColumnWidths = () => {
  if (typeof window === 'undefined') {
    return { ...DEFAULT_COLUMN_WIDTHS }
  }

  try {
    const savedWidths = JSON.parse(window.localStorage.getItem(COLUMN_WIDTH_STORAGE_KEY) || '{}')
    const normalizedWidths = { ...DEFAULT_COLUMN_WIDTHS }

    Object.keys(DEFAULT_COLUMN_WIDTHS).forEach((key) => {
      const value = Number(savedWidths[key])
      if (Number.isFinite(value) && value >= 80) {
        normalizedWidths[key] = Math.round(value)
      }
    })

    return normalizedWidths
  } catch (error) {
    console.warn('读取终端列表列宽配置失败，已恢复默认配置:', error)
    return { ...DEFAULT_COLUMN_WIDTHS }
  }
}

const columnWidths = ref(getInitialColumnWidths())

// 远程桌面相关
const showRemoteDesktop = ref(false)
const currentAssetId = ref(null)
const currentIpAddress = ref('')
const currentHostname = ref('')

const searchForm = ref({
  asset_type: '',
  status: '',
  group_id: null,
  keyword: ''
})

const pagination = ref({
  page: 1,
  page_size: 20,
  total: 0
})

const stats = ref({
  total: 0,
  online: 0,
  offline: 0,
  risk: 0
})

// 健康度计算
const getHealthScore = (terminal) => {
  if (terminal.status !== 'online') return 0

  // 如果没有监控数据，返回null表示无法评分
  if (terminal.cpu_usage === undefined && terminal.memory_usage === undefined && terminal.disk_usage === undefined) {
    return null
  }

  let score = 0

  // CPU使用率 (20分) - 如果没有数据，给满分
  if (terminal.cpu_usage !== undefined && terminal.cpu_usage !== null) {
    if (terminal.cpu_usage < 70) score += 20
    else if (terminal.cpu_usage < 80) score += 10
    else if (terminal.cpu_usage < 90) score += 5
  } else {
    score += 20 // 无数据默认满分
  }

  // 内存使用率 (20分) - 如果没有数据，给满分
  if (terminal.memory_usage !== undefined && terminal.memory_usage !== null) {
    if (terminal.memory_usage < 80) score += 20
    else if (terminal.memory_usage < 90) score += 10
    else if (terminal.memory_usage < 95) score += 5
  } else {
    score += 20 // 无数据默认满分
  }

  // 磁盘使用率 (20分) - 如果没有数据，给满分
  if (terminal.disk_usage !== undefined && terminal.disk_usage !== null) {
    if (terminal.disk_usage < 85) score += 20
    else if (terminal.disk_usage < 90) score += 10
    else if (terminal.disk_usage < 95) score += 5
  } else {
    score += 20 // 无数据默认满分
  }

  // 在线状态 (40分)
  if (terminal.status === 'online') score += 40

  return score
}

const getHealthLevel = (score) => {
  if (score >= 90) return { text: '优秀', type: 'success', icon: '⭐⭐⭐⭐⭐' }
  if (score >= 75) return { text: '良好', type: '', icon: '⭐⭐⭐⭐' }
  if (score >= 60) return { text: '一般', type: 'warning', icon: '⭐⭐⭐' }
  if (score >= 40) return { text: '需关注', type: 'warning', icon: '⭐⭐' }
  return { text: '异常', type: 'danger', icon: '⭐' }
}

const buildSearchParams = () => ({
  asset_type: searchForm.value.asset_type,
  status: searchForm.value.status,
  group_id: searchForm.value.group_id,
  keyword: searchForm.value.keyword
})

// 加载数据
const loadData = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.value.page,
      page_size: pagination.value.page_size,
      ...buildSearchParams()
    }

    const [listRes, statsRes] = await Promise.all([
      getAssetList(params),
      getAssetStats(buildSearchParams())
    ])

    tableData.value = listRes.data || []
    pagination.value.total = listRes.total || 0
    stats.value = {
      total: statsRes.total || 0,
      online: statsRes.online || 0,
      offline: statsRes.offline || 0,
      risk: statsRes.risk || 0
    }
  } catch (error) {
    console.error('加载失败:', error)
    ElMessage.error('加载失败')
  } finally {
    loading.value = false
  }
}

// 加载分组
const loadGroups = async () => {
  try {
    const res = await getGroups()
    groups.value = res.data || []
  } catch (error) {
    console.error('加载分组失败:', error)
  }
}

// 重置搜索
const handleReset = () => {
  searchForm.value = {
    asset_type: '',
    status: '',
    group_id: null,
    keyword: ''
  }
  pagination.value.page = 1
  loadData()
}

// 分组变更
const handleGroupChange = async (row) => {
  try {
    await updateAsset(row.id, {
      group_id: row.group_id || null
    })
    ElMessage.success('分组更新成功')
    // 重新加载数据以保持同步
    await loadData()
  } catch (error) {
    console.error('分组更新失败:', error)
    ElMessage.error('分组更新失败')
    // 失败时也重新加载，恢复原来的值
    await loadData()
  }
}

// 选择变更
const handleSelectionChange = (selection) => {
  selectedIds.value = selection.map(item => item.id)
}

const persistColumnWidths = () => {
  if (typeof window === 'undefined') return

  try {
    window.localStorage.setItem(COLUMN_WIDTH_STORAGE_KEY, JSON.stringify(columnWidths.value))
  } catch (error) {
    console.warn('保存终端列表列宽配置失败:', error)
  }
}

const handleHeaderDragend = (newWidth, _oldWidth, column) => {
  const columnKey = column?.columnKey
  if (!columnKey || !(columnKey in columnWidths.value)) return

  columnWidths.value = {
    ...columnWidths.value,
    [columnKey]: Math.max(80, Math.round(newWidth))
  }
  persistColumnWidths()
}

// 删除
const handleDelete = async (id) => {
  try {
    await ElMessageBox.confirm('确定要删除这条资产吗？', '提示', {
      type: 'warning'
    })
    await deleteAsset(id)
    ElMessage.success('删除成功')
    loadData()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除失败:', error)
      ElMessage.error('删除失败')
    }
  }
}

// 批量删除
const handleBatchDelete = async () => {
  try {
    await ElMessageBox.confirm(`确定要删除选中的 ${selectedIds.value.length} 条资产吗？`, '提示', {
      type: 'warning'
    })
    await batchDeleteAssets(selectedIds.value)
    ElMessage.success('删除成功')
    selectedIds.value = []
    loadData()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除失败:', error)
      ElMessage.error('删除失败')
    }
  }
}

const handleExport = async () => {
  try {
    const params = {}
    if (searchForm.value.asset_type) params.asset_type = searchForm.value.asset_type
    if (searchForm.value.status) params.status = searchForm.value.status
    if (searchForm.value.group_id !== null && searchForm.value.group_id !== undefined) {
      params.group_id = searchForm.value.group_id
    }
    if (searchForm.value.keyword) params.keyword = searchForm.value.keyword

    const blob = await exportAssets(params)
    const downloadUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = `terminals-${dayjs().format('YYYYMMDD-HHmmss')}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(downloadUrl)
    ElMessage.success('终端列表导出成功')
  } catch (error) {
    console.error('导出失败:', error)
    ElMessage.error('终端列表导出失败')
  }
}

// 查看详情
const viewDetail = (row) => {
  router.push(`/terminal/detail/${row.id}`)
}

// 打开远程桌面
const openRemoteDesktop = (row) => {
  currentAssetId.value = row.id
  currentIpAddress.value = row.ip_address
  currentHostname.value = row.hostname
  showRemoteDesktop.value = true
}

// 工具函数
const getTypeLabel = (type) => {
  const map = {
    server: '服务器',
    switch: '交换机',
    router: '路由器',
    pc: 'PC终端',
    unknown: '未知'
  }
  return map[type] || type
}

const getTypeTagType = (type) => {
  const map = {
    server: 'danger',
    switch: 'warning',
    router: 'warning',
    pc: 'info',
    unknown: 'info'
  }
  return map[type] || 'info'
}

const getStatusType = (status) => {
  return status === 'online' ? 'success' : 'info'
}

const getStatusText = (status) => {
  return status === 'online' ? '在线' : '离线'
}

const getAgentInstallStatusType = (status) => {
  const map = {
    installed: 'success',
    not_installed: 'info'
  }
  return map[status] || 'warning'
}

const getAgentInstallStatusText = (status) => {
  const map = {
    installed: '已安装',
    not_installed: '未安装'
  }
  return map[status] || '未知'
}

const getProgressColor = (percentage) => {
  if (percentage < 70) return '#67c23a'
  if (percentage < 85) return '#e6a23c'
  return '#f56c6c'
}

const formatTime = (time) => {
  if (!time) return '-'
  return dayjs(time).fromNow()
}

onMounted(() => {
  loadData()
  loadGroups()
})
</script>

<style scoped>
.app-container {
  padding: 20px;
}

.stats-row {
  margin-bottom: 20px;
}

.stat-card {
  cursor: pointer;
  transition: all 0.3s;
}

.stat-card:hover {
  transform: translateY(-5px);
}

.stat-content {
  display: flex;
  align-items: center;
  gap: 15px;
}

.stat-icon {
  flex-shrink: 0;
}

.stat-info {
  flex: 1;
}

.stat-value {
  font-size: 28px;
  font-weight: bold;
  margin-bottom: 5px;
}

.stat-label {
  font-size: 14px;
  color: #666;
}

.stat-card.online .stat-value {
  color: #67c23a;
}

.stat-card.offline .stat-value {
  color: #909399;
}

.stat-card.warning .stat-value {
  color: #e6a23c;
}

.stat-card.info .stat-value {
  color: #409eff;
}

.search-card {
  margin-bottom: 20px;
}

.terminal-table-wrapper {
  overflow-x: auto;
}

:deep(.terminal-table) {
  min-width: 1600px;
}

:deep(.terminal-table .el-table__header-wrapper th) {
  user-select: none;
}

:deep(.terminal-table .el-table__cell .cell) {
  white-space: nowrap;
}

:deep(.terminal-table .el-table__column-resize-proxy) {
  width: 2px;
  background-color: #409eff;
}
</style>
