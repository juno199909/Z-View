<template>
  <div class="app-container">
    <el-row :gutter="20" class="discovery-action-row">
      <!-- Ping扫描 / 子网发现 -->
      <el-col :xs="24" class="discovery-panel">
        <el-card shadow="never" class="discovery-card">
          <template #header>
            <div class="card-header">
              <span>🔍 Ping扫描 / 子网发现</span>
              <el-tag type="info" size="small">快速发现在线设备与网段资产</el-tag>
            </div>
          </template>

          <el-form :model="pingForm" label-width="120px" @submit.prevent>
            <el-form-item label="IP范围" prop="ip_ranges">
              <el-input
                v-model="pingForm.ip_ranges"
                type="textarea"
                :rows="4"
                placeholder="支持多种格式：&#10;192.168.1.0/24&#10;10.0.0.1-10.0.0.100&#10;192.168.1.1,192.168.1.2"
              />
              <div class="form-tip">支持CIDR、IP段、逗号分隔，可直接用于子网发现</div>
            </el-form-item>

            <el-form-item label="并发数" prop="concurrency">
              <el-slider v-model="pingForm.concurrency" :min="10" :max="1000" :step="10" show-input />
              <div class="form-tip">并发数越大扫描越快，建议100-500</div>
            </el-form-item>

            <el-form-item label="超时时间" prop="timeout">
              <el-input-number v-model="pingForm.timeout" :min="1000" :max="10000" :step="500" />
              <span style="margin-left: 10px">毫秒</span>
              <div class="form-tip">单个IP的超时时间，建议3000ms</div>
            </el-form-item>

            <el-form-item>
              <el-button type="primary" :loading="pingLoading" @click="handlePingScan" icon="Search">
                开始发现
              </el-button>
              <el-button @click="resetPingForm">重置</el-button>
            </el-form-item>
          </el-form>

          <!-- 扫描结果 -->
          <div v-if="pingResult" class="scan-result">
            <el-alert :type="pingResult.type" :title="pingResult.title" :closable="false">
              <div v-html="pingResult.message"></div>
            </el-alert>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 采集进度监控 -->
    <el-card shadow="never" style="margin-top: 20px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span>📈 采集进度监控</span>
          <el-button size="small" :icon="Refresh" @click="loadTaskProgress">刷新</el-button>
        </div>
      </template>

      <el-table :data="taskList" v-loading="taskLoading" stripe>
        <el-table-column prop="task_id" label="任务ID" width="280" show-overflow-tooltip>
          <template #default="{ row }">
            <el-text type="info" size="small">{{ row.task_id }}</el-text>
          </template>
        </el-table-column>

        <el-table-column label="类型" width="100">
          <template #default="{ row }">
            <el-tag :type="row.type === 'ping' ? 'primary' : 'info'" size="small">
              {{ row.type === 'ping' ? '🔍 子网发现' : '📋 其他' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="目标" min-width="150">
          <template #default="{ row }">
            {{ row.target }}
          </template>
        </el-table-column>

        <el-table-column label="进度" width="250">
          <template #default="{ row }">
            <div style="display: flex; align-items: center; gap: 10px;">
              <el-progress
                :percentage="row.progress"
                :status="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'exception' : undefined"
                :stroke-width="12"
              />
              <span style="min-width: 80px; text-align: right; font-size: 12px; color: #666;">
                {{ row.current }}/{{ row.total }}
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="getTaskStatusType(row.status)" size="small">
              {{ getTaskStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="发现资产" width="100">
          <template #default="{ row }">
            <el-text v-if="row.found > 0" type="success" style="font-weight: 600;">
              {{ row.found }}
            </el-text>
            <el-text v-else type="info">0</el-text>
          </template>
        </el-table-column>

        <el-table-column label="失败" width="90">
          <template #default="{ row }">
            <el-text v-if="row.failed > 0" type="danger" style="font-weight: 600;">
              {{ row.failed }}
            </el-text>
            <el-text v-else type="info">0</el-text>
          </template>
        </el-table-column>

        <el-table-column label="耗时" width="100">
          <template #default="{ row }">
            {{ row.duration }}
          </template>
        </el-table-column>

        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">
            {{ row.created_at }}
          </template>
        </el-table-column>

        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'running'"
              type="danger"
              link
              size="small"
              @click="cancelTask(row.task_id)"
            >
              取消
            </el-button>
            <el-button
              v-else
              type="primary"
              link
              size="small"
              @click="viewTaskDetail(row)"
            >
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!taskList.length && !taskLoading" description="暂无采集任务" />
    </el-card>

    <!-- 使用说明 -->
    <el-card shadow="never" style="margin-top: 20px">
      <template #header>
        <span>💡 使用说明</span>
      </template>

      <el-row :gutter="20">
        <el-col :span="12">
          <h4>Ping扫描</h4>
          <ul style="line-height: 2; color: #666;">
            <li>快速发现网络中的在线设备</li>
            <li>支持CIDR格式（如 192.168.1.0/24）</li>
            <li>支持IP范围（如 192.168.1.1-192.168.1.100）</li>
            <li>自动识别在线/离线状态</li>
            <li>扫描结果自动添加到资产列表</li>
          </ul>
        </el-col>

        <el-col :span="12">
          <h4>子网发现</h4>
          <ul style="line-height: 2; color: #666;">
            <li>直接输入网段即可批量发现同子网终端</li>
            <li>适用于办公网、实验室、机房等固定网段</li>
            <li>建议先用 /24 小网段逐步扩大发现范围</li>
            <li>并发与超时可根据网络质量动态调整</li>
            <li>发现结果会统一沉淀到资产与终端列表</li>
          </ul>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { startPingScan, getDiscoveryTasks, cancelTask as cancelTaskAPI } from '@/api/discovery'

const pingLoading = ref(false)
const taskLoading = ref(false)

const getErrorMessage = (error, fallback = '请求失败，请确保后端服务已启动') => {
  return error.response?.data?.detail || error.response?.data?.message || error.response?.data?.error || error.message || fallback
}

const pingForm = reactive({
  ip_ranges: '192.168.1.0/24',
  concurrency: 100,
  timeout: 3000
})

const pingResult = ref(null)
const taskList = ref([])
let refreshTimer = null

// Ping扫描
const handlePingScan = async () => {
  if (!pingForm.ip_ranges.trim()) {
    ElMessage.warning('请输入IP范围')
    return
  }

  pingLoading.value = true
  pingResult.value = null

  try {
    const ip_ranges = pingForm.ip_ranges
      .split('\n')
      .map(s => s.trim())
      .filter(s => s)

    const res = await startPingScan({
      ip_ranges,
      concurrency: pingForm.concurrency,
      timeout: pingForm.timeout
    })

    pingResult.value = {
      type: 'success',
      title: '✅ 子网发现任务已提交',
      message: `
        <div style="line-height: 1.8;">
          <strong>任务ID:</strong> <code>${res.task_id}</code><br>
          <strong>发现目标数:</strong> ${res.total_ips || ip_ranges.length}<br>
          <strong>提示:</strong> 请查看下方【采集进度监控】面板<br>
          <small>扫描完成后，发现的资产会自动添加到资产列表和终端概览</small>
        </div>
      `
    }

    // 立即刷新任务列表
    loadTaskProgress()
  } catch (error) {
    console.error('扫描失败:', error)
    pingResult.value = {
      type: 'error',
      title: '❌ 扫描失败',
      message: getErrorMessage(error)
    }
  } finally {
    pingLoading.value = false
  }
}

// 加载任务进度
const loadTaskProgress = async () => {
  taskLoading.value = true
  try {
    // 调用真实API获取任务列表
    const res = await getDiscoveryTasks()
    taskList.value = res.data || []
  } catch (error) {
    console.error('加载任务进度失败:', error)
    // API调用失败时清空列表，不使用模拟数据
    taskList.value = []
  } finally {
    taskLoading.value = false
  }
}

// 取消任务
const cancelTask = (taskId) => {
  ElMessageBox.confirm(
    `确定要取消任务 ${taskId.substring(0, 20)}... 吗？`,
    '取消任务',
    {
      confirmButtonText: '确定取消',
      cancelButtonText: '我再想想',
      type: 'warning'
    }
  )
    .then(async () => {
      try {
        // 调用真实API取消任务
        await cancelTaskAPI(taskId)
        ElMessage.success('任务已取消')

        // 立即更新本地任务状态
        const task = taskList.value.find(t => t.task_id === taskId)
        if (task) {
          task.status = 'cancelled'
          task.progress = 0
        }

        // 1秒后重新加载列表
        setTimeout(() => {
          loadTaskProgress()
      }, 1000)
    } catch (error) {
        ElMessage.error('取消失败: ' + getErrorMessage(error, '取消任务失败'))
      }
    })
    .catch(() => {
      // 用户点击了取消按钮，不做任何操作
    })
}

// 查看任务详情
const viewTaskDetail = (task) => {
  const statusText = getTaskStatusText(task.status)
  const typeText = task.type === 'ping' ? 'Ping扫描 / 子网发现' : '其他发现任务'

  // 构建发现的IP列表
  let foundIPsHtml = ''
  if (task.found_ips && task.found_ips.length > 0) {
    foundIPsHtml = '<br><strong>发现的IP:</strong><br>'
    foundIPsHtml += '<div style="max-height: 200px; overflow-y: auto; background: #f5f5f5; padding: 10px; border-radius: 4px;">'
    foundIPsHtml += task.found_ips.map(ip => `• ${ip}`).join('<br>')
    foundIPsHtml += '</div>'
  }

  let failedTargetsHtml = ''
  if (task.failed_targets && task.failed_targets.length > 0) {
    failedTargetsHtml = '<br><strong>失败明细:</strong><br>'
    failedTargetsHtml += '<div style="max-height: 220px; overflow-y: auto; background: #fff4f4; padding: 10px; border-radius: 4px; border: 1px solid #fbc4c4;">'
    failedTargetsHtml += task.failed_targets
      .map(item => `• ${item.ip || '-'}：${item.error || '未知错误'}`)
      .join('<br>')
    failedTargetsHtml += '</div>'
  }

  ElMessageBox({
    title: '📋 任务详情',
    message: `
      <div style="line-height: 2;">
        <strong>任务ID:</strong> ${task.task_id}<br>
        <strong>类型:</strong> ${typeText}<br>
        <strong>目标:</strong> ${task.target}<br>
        <strong>状态:</strong> ${statusText}<br>
        <strong>进度:</strong> ${task.current}/${task.total} (${task.progress}%)<br>
        <strong>发现资产:</strong> ${task.found} 个<br>
        <strong>失败目标:</strong> ${task.failed || 0} 个<br>
        <strong>耗时:</strong> ${task.duration}<br>
        <strong>创建时间:</strong> ${task.created_at}
        ${task.error ? `<br><strong>失败原因:</strong> ${task.error}` : ''}
        ${foundIPsHtml}
        ${failedTargetsHtml}
      </div>
    `,
    dangerouslyUseHTMLString: true,
    confirmButtonText: '关闭',
    type: 'info'
  })
}

// 获取任务状态类型
const getTaskStatusType = (status) => {
  const map = {
    pending: 'info',
    running: 'warning',
    completed: 'success',
    failed: 'danger',
    cancelled: 'info'
  }
  return map[status] || 'info'
}

// 获取任务状态文本
const getTaskStatusText = (status) => {
  const map = {
    pending: '等待中',
    running: '进行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
  }
  return map[status] || status
}

// 重置表单
const resetPingForm = () => {
  pingForm.ip_ranges = '192.168.1.0/24'
  pingForm.concurrency = 100
  pingForm.timeout = 3000
  pingResult.value = null
}

onMounted(() => {
  // 页面加载时初始化任务列表
  loadTaskProgress()

  // 每5秒自动刷新任务进度
  refreshTimer = setInterval(() => {
    loadTaskProgress()
  }, 5000)
})

onUnmounted(() => {
  // 组件销毁时清除定时器
  if (refreshTimer) {
    clearInterval(refreshTimer)
  }
})
</script>

<style lang="scss" scoped>
.discovery-action-row {
  align-items: stretch;
}

.discovery-panel {
  display: flex;
  margin-bottom: 20px;
}

.discovery-card {
  width: 100%;
}

.discovery-card :deep(.el-card__body) {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.form-tip {
  font-size: 12px;
  color: #999;
  margin-top: 5px;
}

.scan-result {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid #eee;
}

h4 {
  color: #333;
  margin-bottom: 10px;
}

ul {
  padding-left: 20px;
  margin: 0;
}
</style>
