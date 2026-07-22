<template>
  <div class="task-management">
    <!-- 统计卡片 -->
    <el-row :gutter="20" class="stats-row">
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-content">
            <el-icon class="stat-icon" color="#409EFF" :size="40"><Document /></el-icon>
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
            <el-icon class="stat-icon" color="#67C23A" :size="40"><Loading /></el-icon>
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
            <el-icon class="stat-icon" color="#67C23A" :size="40"><CircleCheck /></el-icon>
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
            <el-icon class="stat-icon" color="#F56C6C" :size="40"><CircleClose /></el-icon>
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
      <div style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
        <h3 style="margin: 0;">任务列表</h3>
        <div>
          <el-button type="primary" icon="Refresh" @click="loadTasks" :loading="loading">
            刷新
          </el-button>
          <el-tag type="info" style="margin-left: 10px;">每10秒自动刷新</el-tag>
        </div>
      </div>

      <el-table :data="tasks" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="70" />
        <el-table-column prop="task_name" label="任务名称" width="220" show-overflow-tooltip />
        <el-table-column prop="task_type" label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="getTaskTypeColor(row.task_type)" size="small">
              {{ getTaskTypeLabel(row.task_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="package_display_name" label="软件包" width="180" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusColor(row.status)" size="small">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="180">
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :status="getProgressStatus(row.status)"
            />
          </template>
        </el-table-column>
        <el-table-column label="执行情况" width="160">
          <template #default="{ row }">
            <div style="font-size: 12px; line-height: 1.6;">
              <div>成功: {{ row.success_count }} / {{ row.target_count }}</div>
              <div>失败: {{ row.failed_count }} / 运行: {{ row.running_count }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="viewDetails(row)">
              详情
            </el-button>
            <el-button size="small" type="danger" @click="deleteTask(row)">
              删除
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
          layout="total, sizes, prev, pager, next"
          @size-change="loadTasks"
          @current-change="loadTasks"
        />
      </div>
    </el-card>

    <!-- 任务详情对话框 -->
    <el-dialog v-model="showDetailsDialog" title="任务详情" width="900px" destroy-on-close>
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
        <el-descriptions-item label="创建时间">{{ selectedTask.created_at }}</el-descriptions-item>
        <el-descriptions-item label="开始时间">{{ selectedTask.start_time || '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider>执行详情</el-divider>

      <el-table :data="taskResults" v-loading="loadingResults" max-height="400">
        <el-table-column prop="hostname" label="主机名" width="150" />
        <el-table-column prop="ip_address" label="IP地址" width="150" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getStatusColor(row.status)" size="small">
              {{ getStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="120">
          <template #default="{ row }">
            <el-progress :percentage="row.progress" :status="getProgressStatus(row.status)" />
          </template>
        </el-table-column>
        <el-table-column prop="duration" label="耗时" width="80">
          <template #default="{ row }">
            {{ row.duration ? row.duration + 's' : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="错误信息" min-width="200">
          <template #default="{ row }">
            <el-tooltip v-if="row.error_message" :content="row.error_message" placement="top">
              <span class="error-text">{{ row.error_message.substring(0, 40) }}...</span>
            </el-tooltip>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="日志" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" link type="primary" @click="openResultLog(row)">
              查看
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>

    <el-dialog v-model="showResultLogDialog" title="执行日志" width="900px" destroy-on-close>
      <template v-if="selectedResult">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="主机名">{{ selectedResult.hostname || '-' }}</el-descriptions-item>
          <el-descriptions-item label="IP地址">{{ selectedResult.ip_address || '-' }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="getStatusColor(selectedResult.status)">
              {{ getStatusLabel(selectedResult.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="耗时">
            {{ selectedResult.duration ? selectedResult.duration + 's' : '-' }}
          </el-descriptions-item>
        </el-descriptions>

        <el-card class="log-card" shadow="never">
          <template #header>标准输出</template>
          <pre class="log-block">{{ selectedResult.stdout_log || '暂无标准输出' }}</pre>
        </el-card>

        <el-card class="log-card" shadow="never">
          <template #header>错误输出</template>
          <pre class="log-block error-log-block">{{ selectedResult.stderr_log || selectedResult.error_message || '暂无错误输出' }}</pre>
        </el-card>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Document, Loading, CircleCheck, CircleClose } from '@element-plus/icons-vue'
import {
  getSoftwareTasks,
  getSoftwareTaskStats,
  getSoftwareTaskDetail,
  deleteSoftwareTask
} from '@/api/software'

const tasks = ref([])
const taskResults = ref([])
const loading = ref(false)
const loadingResults = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

const stats = reactive({
  total: 0,
  running: 0,
  completed: 0,
  failed: 0
})

const showDetailsDialog = ref(false)
const showResultLogDialog = ref(false)
const selectedTask = ref(null)
const selectedResult = ref(null)

let refreshTimer = null

const applyTaskStats = (taskStats) => {
  stats.total = taskStats.total || 0
  stats.running = taskStats.running || 0
  stats.completed = taskStats.completed || 0
  stats.failed = taskStats.failed || 0
}

const loadTasks = async () => {
  loading.value = true
  try {
    const [taskResponse, taskStats] = await Promise.all([
      getSoftwareTasks({
        page: currentPage.value,
        page_size: pageSize.value
      }),
      getSoftwareTaskStats()
    ])

    tasks.value = taskResponse.data || []
    total.value = taskResponse.total || 0
    applyTaskStats(taskStats)
  } catch (error) {
    ElMessage.error('加载任务列表失败：' + (error.response?.data?.detail || error.message))
  } finally {
    loading.value = false
  }
}

const viewDetails = async (task) => {
  selectedTask.value = task
  showDetailsDialog.value = true
  loadingResults.value = true

  try {
    const response = await getSoftwareTaskDetail(task.id)
    selectedTask.value = response
    taskResults.value = response.results || []
  } catch (error) {
    ElMessage.error('加载任务详情失败：' + (error.response?.data?.detail || error.message))
  } finally {
    loadingResults.value = false
  }
}

const deleteTask = async (task) => {
  try {
    await ElMessageBox.confirm(
      `确定删除任务 "${task.task_name}"？删除后将无法恢复。`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      }
    )

    await deleteSoftwareTask(task.id)
    ElMessage.success('任务删除成功')
    loadTasks()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除任务失败：' + (error.response?.data?.detail || error.message))
    }
  }
}

const openResultLog = (result) => {
  selectedResult.value = result
  showResultLogDialog.value = true
}

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
    pending: '等待',
    running: '运行中',
    completed: '完成',
    failed: '失败',
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

const startAutoRefresh = () => {
  refreshTimer = setInterval(() => {
    loadTasks()
  }, 10000)
}

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
.task-management {
  padding: 0;
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
  margin-right: 20px;
}

.stat-info {
  flex: 1;
}

.stat-value {
  font-size: 28px;
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

.log-card {
  margin-top: 16px;
}

.log-block {
  margin: 0;
  padding: 14px;
  border-radius: 8px;
  background: #0f172a;
  color: #e2e8f0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: Consolas, 'Courier New', monospace;
  line-height: 1.6;
  max-height: 320px;
  overflow: auto;
}

.error-log-block {
  color: #fecaca;
}
</style>
