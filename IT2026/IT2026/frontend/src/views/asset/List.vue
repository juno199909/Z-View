<template>
  <div class="app-container">
    <!-- 搜索栏 -->
    <el-card class="search-card" shadow="never">
      <el-form :inline="true" :model="searchForm">
        <el-form-item label="资产分组">
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
            <el-option label="降级" value="degraded" />
            <el-option label="未知" value="unknown" />
          </el-select>
        </el-form-item>

        <el-form-item label="关键字">
          <el-input v-model="searchForm.keyword" placeholder="主机名/IP/MAC" clearable style="width: 200px" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :icon="Search" @click="handleSearch">搜索</el-button>
          <el-button :icon="Refresh" @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 操作栏 -->
    <el-card class="toolbar-card" shadow="never">
      <el-row :gutter="10">
        <el-col :span="12">
          <el-button type="primary" :icon="Plus" @click="handleCreate">新增资产</el-button>
          <el-button type="danger" :icon="Delete" :disabled="!selectedIds.length" @click="handleBatchDelete">
            批量删除
          </el-button>
          <el-button :icon="Download" @click="handleExport">导出</el-button>
        </el-col>
        <el-col :span="12" style="text-align: right">
          <el-button :icon="Refresh" circle @click="loadData" />
        </el-col>
      </el-row>
    </el-card>

    <!-- 数据表格 -->
    <el-card class="table-card" shadow="never">
      <el-table
        v-loading="loading"
        :data="tableData"
        stripe
        @selection-change="handleSelectionChange"
      >
        <el-table-column type="selection" width="55" />
        <el-table-column prop="hostname" label="主机名" min-width="150" />
        <el-table-column prop="ip_address" label="IP地址" width="140" />
        <el-table-column prop="mac_address" label="MAC地址" width="150" />

        <el-table-column label="资产分组" width="100">
          <template #default="{ row }">
            <el-tag :type="getTypeTagType(row.asset_type)">
              {{ getTypeLabel(row.asset_type) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="分组" width="120">
          <template #default="{ row }">
            <el-select
              v-model="row.group_id"
              placeholder="选择分组"
              size="small"
              @change="handleGroupChange(row)"
              clearable
            >
              <el-option
                v-for="group in groupList"
                :key="group.id"
                :label="group.name"
                :value="group.id"
              />
            </el-select>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusTagType(row.status)">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="location" label="位置" min-width="150" />

        <el-table-column label="最后在线" width="160">
          <template #default="{ row }">
            {{ formatTime(row.last_seen) }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="handleView(row.id)">详情</el-button>
            <el-button type="warning" link size="small" @click="handleEdit(row.id)">编辑</el-button>
            <el-button type="danger" link size="small" @click="handleDelete(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <el-pagination
        v-model:current-page="pagination.page"
        v-model:page-size="pagination.page_size"
        :total="pagination.total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        @size-change="loadData"
        @current-change="loadData"
        style="margin-top: 20px"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, Plus, Delete, Download } from '@element-plus/icons-vue'
import { getAssetList, deleteAsset, batchDeleteAssets, exportAssets, updateAssetGroup } from '@/api/asset'
import { getGroups } from '@/api/group'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'
import 'dayjs/locale/zh-cn'

dayjs.extend(relativeTime)
dayjs.locale('zh-cn')

const router = useRouter()

const loading = ref(false)
const tableData = ref([])
const selectedIds = ref([])
const groupList = ref([])

const searchForm = reactive({
  asset_type: '',
  status: '',
  keyword: ''
})

const pagination = reactive({
  page: 1,
  page_size: 20,
  total: 0
})

// 加载分组列表
const loadGroups = async () => {
  try {
    const response = await getGroups()
    groupList.value = response.data || []
  } catch (error) {
    console.error('加载分组失败:', error)
  }
}

// 加载数据
const loadData = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.page_size,
      ...searchForm
    }
    const res = await getAssetList(params)
    tableData.value = res.data || []
    pagination.total = res.total || 0
  } catch (error) {
    console.error('加载失败:', error)
  } finally {
    loading.value = false
  }
}

// 更新资产分组
const handleGroupChange = async (row) => {
  try {
    await updateAssetGroup(row.id, row.group_id)
    ElMessage.success('分组更新成功')
  } catch (error) {
    ElMessage.error('分组更新失败')
    loadData() // 重新加载数据恢复原值
  }
}

// 搜索
const handleSearch = () => {
  pagination.page = 1
  loadData()
}

// 重置
const handleReset = () => {
  Object.assign(searchForm, {
    asset_type: '',
    status: '',
    keyword: ''
  })
  handleSearch()
}

// 新增
const handleCreate = () => {
  router.push('/asset/create')
}

// 查看详情
const handleView = (id) => {
  router.push(`/asset/detail/${id}`)
}

// 编辑
const handleEdit = (id) => {
  router.push(`/asset/detail/${id}?edit=true`)
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
    }
  }
}

// 导出
const handleExport = async () => {
  try {
    const params = {}
    if (searchForm.asset_type) {
      params.asset_type = searchForm.asset_type
    }
    if (searchForm.status) {
      params.status = searchForm.status
    }
    if (searchForm.keyword) {
      params.keyword = searchForm.keyword
    }

    const blob = await exportAssets(params)
    const downloadUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = `assets-${dayjs().format('YYYYMMDD-HHmmss')}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(downloadUrl)
    ElMessage.success('导出成功')
  } catch (error) {
    console.error('导出失败:', error)
  }
}

// 选择变化
const handleSelectionChange = (selection) => {
  selectedIds.value = selection.map(item => item.id)
}

// 获取类型标签类型
const getTypeTagType = (type) => {
  const map = {
    server: 'primary',
    switch: 'success',
    router: 'warning',
    pc: 'info',
    unknown: 'info'
  }
  return map[type] || 'info'
}

// 获取类型标签文本
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

// 获取状态标签类型
const getStatusTagType = (status) => {
  const map = {
    online: 'success',
    offline: 'danger',
    degraded: 'warning',
    unknown: 'info'
  }
  return map[status] || 'info'
}

// 获取状态标签文本
const getStatusLabel = (status) => {
  const map = {
    online: '在线',
    offline: '离线',
    degraded: '降级',
    unknown: '未知'
  }
  return map[status] || status
}

// 格式化时间
const formatTime = (time) => {
  if (!time) return '-'
  return dayjs(time).fromNow()
}

onMounted(() => {
  loadGroups()
  loadData()
})
</script>

<style lang="scss" scoped>
.app-container {
  padding: 20px;
}

.search-card,
.toolbar-card,
.table-card {
  margin-bottom: 20px;
}
</style>
