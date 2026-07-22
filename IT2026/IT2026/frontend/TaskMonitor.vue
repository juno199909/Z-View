<template>
  <div class="task-monitor-container">
    <el-card class="header-card">
      <h2>任务监控</h2>
    </el-card>

    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" color="#409EFF"><Document /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.total }}</div>
              <div class="stat-label">总任务数</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" color="#67C23A"><Loading /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.running }}</div>
              <div class="stat-label">运行中</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" color="#67C23A"><CircleCheck /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.completed }}</div>
              <div class="stat-label">已完成</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" color="#F56C6C"><CircleClose /></el-icon>
            <div class="stat-info">
              <div class="stat-value">{{ stats.failed }}</div>
              <div class="stat-label">失败</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 任务列表 -->
    <el-card class="table-card">
      <el-table :data="tasks" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="task_name" label="任务名称" width="250" />
        <el-table-column prop="task_type" label="类型" width="100">
          <template #default="{ row }">
            <el-tag :type="getTaskTypeColor(row.task_type)">
              {{ getTaskTypeLabel(row.task_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="package_display_name" label="软件包" width="200" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="getStatusColor(row.status)">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="200">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :status="getProgressStatus(row.status)"
              :stroke-width="16"
            />
          </template>
        </el-table-column>
        <el-table-column label="执行情况" width="200">
          <template #default="{ row }">
            <div style="font-size: 12px">
              <div>成功: {{ row.success_count }} / {{ row.target_count }}</div>
              <div>失败: {{ row.failed_count }}</div>
              <div>运行: {{ row.running_count }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="100">
          <template #default="{ row }">
            <el-tag :type="getPriorityColor(row.priority)" size="small">
              {{ getPriorityLabel(row.priority) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="viewDetails(row)">
              详情
            </el-button>
            <el-button
              size="small"
              type="danger"
              @click="cancelTask(row)"
              v-if="row.status === 'pending' || row.status === 'running'"
            >
              取消
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-container">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadTasks"
          @current-change="loadTasks"
        />
      </div>
    </el-card>

    <!-- 任务详情对话框 -->
    <el-dialog
      v-model="showDetailsDialog"
      title="任务详情"
      width="900px"
      destroy-on-close
    >
      <el-descriptions :column="2" border v-if="selectedTask">
        <el-descriptions-item label="任务ID">{{ selectedTask.id }}</el-descriptions-item>
        <el-descriptions-item label="任务名称">{{ selectedTask.task_name }}</el-descriptions-item>
        <el-descriptions-item label="任务类型">{{ getTaskTypeLabel(selectedTask.task_type) }}</el-descriptions-item>
        <el-descriptions-item label="软件包">{{ selectedTask.package_display_name }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="getStatusColor(selectedTask.status)">
            {{ getStatusLabel(selectedTask.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="进度">{{ selectedTask.progress }}%</el-descriptions-item>
        <el-descriptions-item label="目标数量">{{ selectedTask.target_count }}</el-descriptions-item>
        <el-descriptions-item label="成功数量">{{ selectedTask.success_count }}</el-descriptions-item>
        <el-descriptions-item label="失败数量">{{ selectedTask.failed_count }}</el-descriptions-item>
        <el-descriptions-item label="运行中">{{ selectedTask.running_count }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ selectedTask.created_at }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ selectedTask.start_time || '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider>执行详情</el-divider>

      <el-table :data="taskResults" v-loading="loadingResults" max-height="400">
        <el-table-column prop="hostname" label="主机名" width="150" />
        <el-table-column prop="ip_address" label="IP地址" width="150" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="getStatusColor(row.status)" size="small">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="200">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :status="getProgressStatus(row.status)"
              :stroke-width="12"
            />
            <div style="font-size: 11px; margin-top: 4px">
              下载: {{ row.download_progress }}% / 安装: {{ row.install_progress }}%
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="start_time" label="开始时间" width="180" />
        <el-table-column prop="end_time" label="结束时间" width="180" />
        <el-table-column prop="duration" label="耗时" width="100">
          <template #default="{ row }">
            {{ row.duration ? row.duration + 's' : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="错误信息" width="200">
          <template #default="{ row }">
            <el-tooltip v-if="row.error_message" :content="row.error_message" placement="top">
              <span class="error-text">{{ row.error_message.substring(0, 30) }}...</span>
            </el-tooltip>
            <span v-else>-</span>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Loading, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import axios from 'axios'

const softwareApi = axios.create({
  baseURL: import.meta.env.VITE_SOFTWARE_API_BASE || '/software-api/api/v1',
  timeout: 30000
})

// 数据
const tasks = ref([])
const taskResults = ref([])
const loading = ref(false)
const loadingResults = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 统计
const stats = reactive({
  total: 0,
  running: 0,
  completed: 0,
  failed: 0
})

// 对话框
const showDetailsDialog = ref(false)
const selectedTask = ref(null)

// 自动刷新
let refreshTimer = null

// 加载任务列表
const loadTasks = async () => {
  loading.value = true
  try {
    const response = await softwareApi.get('/software/tasks', {
      params: {
        page: currentPage.value,
        page_size: pageSize.value
      }
    })
    tasks.value = response.data.data
    total.value = response.data.total

    // 更新统计
    stats.total = response.data.total
    stats.running = tasks.value.filter(t => t.status === 'running').length
    stats.completed = tasks.value.filter(t => t.status === 'completed').length
    stats.failed = tasks.value.filter(t => t.status === 'failed').length
  } catch (error) {
    ElMessage.error('加载任务列表失败：' + error.message)
  } finally {
    loading.value = false
  }
}

// 查看详情
const viewDetails = async (task) => {
  selectedTask.value = task
  showDetailsDialog.value = true
  loadingResults.value = true

  try {
    const response = await softwareApi.get(`/software/tasks/${task.id}`)
    selectedTask.value = response.data
    taskResults.value = response.data.results || []
  } catch (error) {
    ElMessage.error('加载任务详情失败：' + error.message)
  } finally {
    loadingResults.value = false
  }
}

// 取消任务
const cancelTask = async (task) => {
  try {
    await ElMessageBox.confirm(`确定取消任务 ${task.task_name}？`, '提示', {
      type: 'warning'
    })

    console.info('[TaskMonitor] 请求取消任务', {
      taskId: task.id,
      taskName: task.task_name
    })
    const response = await softwareApi.put(`/software/tasks/${task.id}/cancel`)
    ElMessage.success(response.data?.message || '任务取消成功')
    await loadTasks()

    // 如果详情窗口正在显示同一个任务，顺手刷新一次详情，避免界面滞后
    if (showDetailsDialog.value && selectedTask.value?.id === task.id) {
      await viewDetails(task)
    }
  } catch (error) {
    if (error?.message === 'cancel' || error?.message === 'close') {
      return
    }

    console.error('[TaskMonitor] 取消任务失败', {
      taskId: task.id,
      error
    })
    ElMessage.error('取消任务失败：' + (error.response?.data?.detail || error.message))
  }
}

// 辅助函数
const getTaskTypeLabel = (type) => {
  const labels = { install: '安装', uninstall: '卸载', upgrade: '升级', check: '检查' }
  return labels[type] || type
}

const getTaskTypeColor = (type) => {
  const colors = { install: 'success', uninstall: 'danger', upgrade: 'warning', check: 'info' }
  return colors[type] || 'info'
}

const getStatusLabel = (status) => {
  const labels = {
    pending: '等待中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    downloading: '下载中',
    installing: '安装中',
    success: '成功'
  }
  return labels[status] || status
}

const getStatusColor = (status) => {
  const colors = {
    pending: 'info',
    running: 'primary',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info',
    downloading: 'primary',
    installing: 'warning',
    success: 'success'
  }
  return colors[status] || 'info'
}

const getProgressStatus = (status) => {
  if (status === 'completed' || status === 'success') return 'success'
  if (status === 'failed') return 'exception'
  return undefined
}

const getPriorityLabel = (priority) => {
  const labels = { low: '低', normal: '普通', high: '高', urgent: '紧急' }
  return labels[priority] || priority
}

const getPriorityColor = (priority) => {
  const colors = { low: 'info', normal: '', high: 'warning', urgent: 'danger' }
  return colors[priority] || ''
}

// 启动自动刷新
const startAutoRefresh = () => {
  refreshTimer = setInterval(() => {
    loadTasks()
  }, 10000) // 每10秒刷新一次
}

// 停止自动刷新
const stopAutoRefresh = () => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

onMounted(() => {
  loadTasks()
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<style scoped>
.task-monitor-container {
  padding: 20px;
}

.header-card {
  margin-bottom: 20px;
}

.header-card h2 {
  margin: 0;
}

.stats-row {
  margin-bottom: 20px;
}

.stat-card {
  height: 100px;
}

.stat-content {
  display: flex;
  align-items: center;
  height: 100%;
}

.stat-icon {
  font-size: 48px;
  margin-right: 20px;
}

.stat-info {
  flex: 1;
}

.stat-value {
  font-size: 32px;
  font-weight: bold;
  line-height: 1;
  margin-bottom: 8px;
}

.stat-label {
  font-size: 14px;
  color: #909399;
}

.table-card {
  margin-bottom: 20px;
}

.pagination-container {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

.error-text {
  color: #F56C6C;
  cursor: pointer;
}

.error-text:hover {
  text-decoration: underline;
}
</style>
