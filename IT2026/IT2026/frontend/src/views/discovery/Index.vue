<template>
  <div class="app-container">
    <el-row :gutter="20" class="discovery-action-row">
      <el-col :xs="24" :lg="12" class="discovery-panel">
        <el-card shadow="never" class="discovery-card">
          <template #header>
            <div class="card-header">
              <span>Ping 扫描 / 子网发现</span>
              <el-tag type="info" size="small">快速发现在线资产</el-tag>
            </div>
          </template>

          <el-form :model="pingForm" label-width="120px" @submit.prevent>
            <el-form-item label="IP 范围">
              <el-input
                v-model="pingForm.ip_ranges"
                type="textarea"
                :rows="4"
                placeholder="支持多种格式：&#10;192.168.1.0/24&#10;10.0.0.1-10.0.0.100&#10;192.168.1.1,192.168.1.2"
              />
              <div class="form-tip">支持 CIDR、IP 段、逗号分隔，扫描结果会自动进入资产列表。</div>
            </el-form-item>

            <el-form-item label="并发数">
              <el-slider v-model="pingForm.concurrency" :min="10" :max="1000" :step="10" show-input />
              <div class="form-tip">建议 100 到 500，根据网络质量调整。</div>
            </el-form-item>

            <el-form-item label="超时时间">
              <el-input-number v-model="pingForm.timeout" :min="1000" :max="10000" :step="500" />
              <span class="unit-text">毫秒</span>
            </el-form-item>

            <el-form-item>
              <el-button type="primary" :loading="pingLoading" @click="handlePingScan">开始发现</el-button>
              <el-button @click="resetPingForm">重置</el-button>
            </el-form-item>
          </el-form>

          <div v-if="pingResult" class="scan-result">
            <el-alert :type="pingResult.type" :title="pingResult.title" :closable="false">
              <div v-html="pingResult.message"></div>
            </el-alert>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="12" class="discovery-panel">
        <el-card shadow="never" class="discovery-card">
          <template #header>
            <div class="card-header">
              <span>SNMP 采集</span>
              <el-tag type="warning" size="small">交换机 / 路由器 / 打印机</el-tag>
            </div>
          </template>

          <el-form :model="snmpForm" label-width="120px" @submit.prevent>
            <el-form-item label="目标列表">
              <el-input
                v-model="snmpForm.targets_text"
                type="textarea"
                :rows="4"
                placeholder="每行一个目标，支持：&#10;192.168.1.10&#10;192.168.1.11 public&#10;192.168.1.12,private"
              />
              <div class="form-tip">每行一个 IP，可选填写 community，默认 public。</div>
            </el-form-item>

            <el-form-item label="SNMP 版本">
              <el-select v-model="snmpForm.version" style="width: 100%">
                <el-option label="v1" :value="1" />
                <el-option label="v2c" :value="2" />
              </el-select>
            </el-form-item>

            <el-form-item label="超时时间">
              <el-input-number v-model="snmpForm.timeout" :min="1" :max="30" :step="1" />
              <span class="unit-text">秒</span>
            </el-form-item>

            <el-form-item>
              <el-button type="primary" :loading="snmpLoading" @click="handleSnmpScan">开始采集</el-button>
              <el-button @click="resetSnmpForm">重置</el-button>
            </el-form-item>
          </el-form>

          <div v-if="snmpResult" class="scan-result">
            <el-alert :type="snmpResult.type" :title="snmpResult.title" :closable="false">
              <div v-html="snmpResult.message"></div>
            </el-alert>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="task-card">
      <template #header>
        <div class="task-header">
          <span>采集任务进度</span>
          <el-button size="small" :icon="Refresh" @click="loadTaskProgress">刷新</el-button>
        </div>
      </template>

      <el-table :data="taskList" v-loading="taskLoading" stripe>
        <el-table-column prop="task_id" label="任务 ID" min-width="250" show-overflow-tooltip />

        <el-table-column label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="getTaskTypeType(row.type)" size="small">
              {{ getTaskTypeText(row.type) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="target" label="目标" min-width="160" show-overflow-tooltip />

        <el-table-column label="进度" width="250">
          <template #default="{ row }">
            <div class="progress-cell">
              <el-progress
                :percentage="row.progress || 0"
                :status="row.status === 'completed' ? 'success' : row.status === 'failed' ? 'exception' : undefined"
                :stroke-width="12"
              />
              <span class="progress-count">{{ row.current || 0 }}/{{ row.total || 0 }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="getTaskStatusType(row.status)" size="small">
              {{ getTaskStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="发现资产" width="100">
          <template #default="{ row }">
            <el-text :type="(row.found || 0) > 0 ? 'success' : 'info'">
              {{ row.found || 0 }}
            </el-text>
          </template>
        </el-table-column>

        <el-table-column label="失败" width="90">
          <template #default="{ row }">
            <el-text :type="(row.failed || 0) > 0 ? 'danger' : 'info'">
              {{ row.failed || 0 }}
            </el-text>
          </template>
        </el-table-column>

        <el-table-column prop="duration" label="耗时" width="110" />
        <el-table-column prop="created_at" label="创建时间" width="180" />

        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row.status === 'running' || row.status === 'pending'"
              type="danger"
              link
              size="small"
              @click="cancelTask(row.task_id)"
            >
              取消
            </el-button>
            <el-button v-else type="primary" link size="small" @click="viewTaskDetail(row)">
              详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty v-if="!taskLoading && taskList.length === 0" description="暂无采集任务" />
    </el-card>

    <el-card shadow="never" class="help-card">
      <template #header>
        <span>使用说明</span>
      </template>

      <el-row :gutter="20">
        <el-col :xs="24" :md="12">
          <h4>Ping 扫描</h4>
          <ul>
            <li>适合快速发现子网内在线主机。</li>
            <li>支持 CIDR、IP 段、逗号分隔输入。</li>
            <li>发现结果会自动同步到资产与终端概览。</li>
          </ul>
        </el-col>

        <el-col :xs="24" :md="12">
          <h4>SNMP 采集</h4>
          <ul>
            <li>适合交换机、路由器、打印机等网络设备。</li>
            <li>支持每个目标单独指定 community。</li>
            <li>采集成功后会回填厂商、型号等基础信息。</li>
          </ul>
        </el-col>
      </el-row>
    </el-card>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { cancelTask as cancelTaskAPI, getDiscoveryTasks, startPingScan, startSnmpScan } from '@/api/discovery'

const pingLoading = ref(false)
const snmpLoading = ref(false)
const taskLoading = ref(false)

const pingForm = reactive({
  ip_ranges: '192.168.1.0/24',
  concurrency: 100,
  timeout: 3000
})

const snmpForm = reactive({
  targets_text: '',
  version: 2,
  timeout: 5
})

const pingResult = ref(null)
const snmpResult = ref(null)
const taskList = ref([])
let refreshTimer = null

const getErrorMessage = (error, fallback = '请求失败，请确认后端服务已启动') => {
  return error?.response?.data?.detail || error?.response?.data?.message || error?.response?.data?.error || error?.message || fallback
}

const normalizeTaskList = (payload) => {
  if (Array.isArray(payload?.data)) return payload.data
  if (Array.isArray(payload)) return payload
  return []
}

const parsePingTargets = (rawText) => {
  return String(rawText || '')
    .replace(/\r/g, '')
    .split('\n')
    .map(item => item.trim())
    .filter(Boolean)
}

const parseSnmpTargets = (rawText) => {
  return String(rawText || '')
    .replace(/\r/g, '')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .map(line => {
      const normalized = line.replace(/，/g, ',')
      const parts = normalized.includes(',')
        ? normalized.split(',').map(item => item.trim()).filter(Boolean)
        : normalized.split(/\s+/).map(item => item.trim()).filter(Boolean)

      return {
        ip: parts[0],
        community: parts[1] || 'public'
      }
    })
    .filter(item => item.ip)
}

const loadTaskProgress = async () => {
  taskLoading.value = true
  try {
    const res = await getDiscoveryTasks()
    taskList.value = normalizeTaskList(res)
  } catch (error) {
    taskList.value = []
    console.error('加载发现任务失败:', error)
  } finally {
    taskLoading.value = false
  }
}

const handlePingScan = async () => {
  const ip_ranges = parsePingTargets(pingForm.ip_ranges)
  if (ip_ranges.length === 0) {
    ElMessage.warning('请输入至少一个扫描目标')
    return
  }

  pingLoading.value = true
  pingResult.value = null

  try {
    const res = await startPingScan({
      ip_ranges,
      concurrency: pingForm.concurrency,
      timeout: pingForm.timeout
    })

    pingResult.value = {
      type: 'success',
      title: 'Ping 发现任务已提交',
      message: `
        <div style="line-height: 1.8;">
          <strong>任务 ID:</strong> <code>${res.task_id}</code><br>
          <strong>扫描目标数:</strong> ${res.total_ips || ip_ranges.length}<br>
          <strong>提示:</strong> 下方任务列表会自动刷新进度。
        </div>
      `
    }
    await loadTaskProgress()
  } catch (error) {
    pingResult.value = {
      type: 'error',
      title: 'Ping 发现失败',
      message: getErrorMessage(error)
    }
  } finally {
    pingLoading.value = false
  }
}

const handleSnmpScan = async () => {
  const targets = parseSnmpTargets(snmpForm.targets_text)
  if (targets.length === 0) {
    ElMessage.warning('请输入至少一个 SNMP 目标')
    return
  }

  snmpLoading.value = true
  snmpResult.value = null

  try {
    const res = await startSnmpScan({
      targets,
      version: snmpForm.version,
      timeout: snmpForm.timeout
    })

    const runtimeNote = res.snmp_available === false && res.snmp_runtime_error
      ? `<br><strong>运行环境提示:</strong> ${res.snmp_runtime_error}`
      : ''

    snmpResult.value = {
      type: res.snmp_available === false ? 'warning' : 'success',
      title: 'SNMP 采集任务已提交',
      message: `
        <div style="line-height: 1.8;">
          <strong>任务 ID:</strong> <code>${res.task_id}</code><br>
          <strong>采集目标数:</strong> ${res.total_targets || targets.length}<br>
          <strong>版本:</strong> v${snmpForm.version === 1 ? '1' : '2c'}
          ${runtimeNote}
        </div>
      `
    }
    await loadTaskProgress()
  } catch (error) {
    snmpResult.value = {
      type: 'error',
      title: 'SNMP 采集失败',
      message: getErrorMessage(error)
    }
  } finally {
    snmpLoading.value = false
  }
}

const cancelTask = async (taskId) => {
  try {
    await ElMessageBox.confirm(`确定取消任务 ${taskId} 吗？`, '取消任务', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch {
    return
  }

  try {
    await cancelTaskAPI(taskId)
    ElMessage.success('已发送取消请求')
    await loadTaskProgress()
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '取消任务失败'))
  }
}

const getTaskTypeText = (type) => {
  const map = {
    ping: 'Ping 发现',
    snmp: 'SNMP 采集'
  }
  return map[type] || type || '未知'
}

const getTaskTypeType = (type) => {
  const map = {
    ping: 'primary',
    snmp: 'warning'
  }
  return map[type] || 'info'
}

const getTaskStatusText = (status) => {
  const map = {
    pending: '等待中',
    running: '进行中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
  }
  return map[status] || status || '未知'
}

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

const renderListHtml = (items, formatter) => {
  if (!Array.isArray(items) || items.length === 0) return ''
  return `
    <div class="detail-list">
      ${items.map(formatter).join('')}
    </div>
  `
}

const viewTaskDetail = (task) => {
  const metadata = task.metadata || {}
  const metadataRows = Object.entries(metadata)
    .filter(([, value]) => value !== null && value !== undefined && value !== '')
    .map(([key, value]) => `<div><strong>${key}:</strong> ${value}</div>`)
    .join('')

  const foundHtml = renderListHtml(task.found_ips, item => `<div>${item}</div>`)
  const failedHtml = renderListHtml(task.failed_targets, item => {
    const ip = item?.ip || '-'
    const error = item?.error || '未知错误'
    return `<div>${ip}: ${error}</div>`
  })

  ElMessageBox({
    title: '任务详情',
    message: `
      <div style="line-height: 1.9;">
        <div><strong>任务 ID:</strong> ${task.task_id}</div>
        <div><strong>类型:</strong> ${getTaskTypeText(task.type)}</div>
        <div><strong>目标:</strong> ${task.target || '-'}</div>
        <div><strong>状态:</strong> ${getTaskStatusText(task.status)}</div>
        <div><strong>进度:</strong> ${task.current || 0}/${task.total || 0} (${task.progress || 0}%)</div>
        <div><strong>发现资产:</strong> ${task.found || 0}</div>
        <div><strong>失败数量:</strong> ${task.failed || 0}</div>
        <div><strong>耗时:</strong> ${task.duration || '-'}</div>
        <div><strong>创建时间:</strong> ${task.created_at || '-'}</div>
        ${task.error ? `<div><strong>错误:</strong> ${task.error}</div>` : ''}
        ${metadataRows ? `<div style="margin-top: 8px;"><strong>任务参数</strong>${metadataRows}</div>` : ''}
        ${foundHtml ? `<div style="margin-top: 8px;"><strong>发现 IP</strong>${foundHtml}</div>` : ''}
        ${failedHtml ? `<div style="margin-top: 8px;"><strong>失败明细</strong>${failedHtml}</div>` : ''}
      </div>
    `,
    dangerouslyUseHTMLString: true,
    confirmButtonText: '关闭'
  })
}

const resetPingForm = () => {
  pingForm.ip_ranges = '192.168.1.0/24'
  pingForm.concurrency = 100
  pingForm.timeout = 3000
  pingResult.value = null
}

const resetSnmpForm = () => {
  snmpForm.targets_text = ''
  snmpForm.version = 2
  snmpForm.timeout = 5
  snmpResult.value = null
}

onMounted(() => {
  loadTaskProgress()
  refreshTimer = setInterval(() => {
    loadTaskProgress()
  }, 5000)
})

onUnmounted(() => {
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
  display: flex;
  flex-direction: column;
  height: 100%;
}

.card-header,
.task-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.task-card,
.help-card {
  margin-top: 20px;
}

.form-tip {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}

.unit-text {
  margin-left: 10px;
  color: #606266;
}

.scan-result {
  margin-top: 20px;
  padding-top: 20px;
  border-top: 1px solid #ebeef5;
}

.progress-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.progress-count {
  min-width: 72px;
  text-align: right;
  font-size: 12px;
  color: #909399;
}

h4 {
  margin: 0 0 10px;
  color: #303133;
}

ul {
  margin: 0;
  padding-left: 20px;
  color: #606266;
  line-height: 1.9;
}

.detail-list {
  max-height: 220px;
  overflow-y: auto;
  margin-top: 6px;
  padding: 8px 10px;
  background: #f5f7fa;
  border-radius: 4px;
}
</style>
