<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">任务中心</h2>
        <div class="zv-page-subtitle">通用任务通道：终端任务下发、执行状态与结果回传（心跳通道，约 30s 延迟）</div>
      </div>
      <div class="zv-page-actions">
        <el-switch v-model="autoRefresh" active-text="自动刷新" style="margin-right: 12px" />
        <el-button type="primary" :icon="Plus" @click="openDispatch">下发任务</el-button>
        <el-button :icon="Refresh" @click="loadJobs" :loading="loading">刷新</el-button>
      </div>
    </div>

    <div class="zv-card" style="margin-bottom: 12px">
      <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap">
        <el-radio-group v-model="stateFilter" @change="loadJobs">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="pending">待下发</el-radio-button>
          <el-radio-button value="dispatched">已下发</el-radio-button>
          <el-radio-button value="running">执行中</el-radio-button>
          <el-radio-button value="succeeded">成功</el-radio-button>
          <el-radio-button value="failed">失败</el-radio-button>
          <el-radio-button value="expired">已过期</el-radio-button>
        </el-radio-group>
        <el-select v-model="typeFilter" placeholder="任务类型" clearable style="width: 150px" @change="loadJobs">
          <el-option v-for="t in JOB_TYPES" :key="t" :label="t" :value="t" />
        </el-select>
      </div>
    </div>

    <div class="zv-card">
      <el-table :data="jobs" v-loading="loading" size="default" stripe>
        <el-table-column prop="job_id" label="任务 ID" min-width="190" show-overflow-tooltip />
        <el-table-column prop="hostname" label="终端" min-width="130">
          <template #default="{ row }">{{ row.hostname || `asset ${row.asset_id}` }}</template>
        </el-table-column>
        <el-table-column prop="job_type" label="类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.job_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="stateTagType(row.state)" :effect="row.state === 'running' ? 'dark' : 'light'" size="small">
              {{ stateLabel(row.state) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="payload" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ payloadPreview(row.payload) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="165">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="更新时间" width="165">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="showDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-drawer v-model="detailVisible" :title="detailRow ? `任务详情 ${detailRow.job_id}` : '任务详情'" size="40%">
      <template v-if="detailRow">
        <el-descriptions :column="1" border size="small" style="margin-bottom: 16px">
          <el-descriptions-item label="任务 ID">{{ detailRow.job_id }}</el-descriptions-item>
          <el-descriptions-item label="终端">{{ detailRow.hostname || detailRow.asset_id }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ detailRow.job_type }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="stateTagType(detailRow.state)" size="small">{{ stateLabel(detailRow.state) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatTime(detailRow.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatTime(detailRow.updated_at) }}</el-descriptions-item>
        </el-descriptions>
        <h4 style="margin: 0 0 8px">Payload</h4>
        <pre class="zv-json-block">{{ pretty(detailRow.payload) }}</pre>
        <h4 style="margin: 16px 0 8px">Result</h4>
        <pre class="zv-json-block">{{ pretty(detailRow.result) }}</pre>
      </template>
    </el-drawer>

    <el-dialog v-model="dispatchVisible" title="下发任务" width="480px">
      <el-form label-width="90px">
        <el-form-item label="终端 ID" required>
          <el-input v-model.number="dispatchForm.asset_id" placeholder="asset_id（如 28）" />
        </el-form-item>
        <el-form-item label="任务类型" required>
          <el-select v-model="dispatchForm.job_type" style="width: 100%">
            <el-option v-for="t in JOB_TYPES" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-form-item label="Payload">
          <el-input v-model="dispatchForm.payload" type="textarea" :rows="4" placeholder='JSON，可留空。如 {"kb": "KB2267602"}' />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dispatchVisible = false">取消</el-button>
        <el-button type="primary" :loading="dispatching" @click="submitDispatch">下发</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { listAgentJobs, createAgentJob } from '@/api/jobs'

const JOB_TYPES = ['command', 'report', 'wu_diag', 'wu_install', 'wu_collect']

const loading = ref(false)
const autoRefresh = ref(false)
const jobs = ref([])
const stateFilter = ref('')
const typeFilter = ref('')
const detailVisible = ref(false)
const detailRow = ref(null)
const dispatchVisible = ref(false)
const dispatching = ref(false)
const dispatchForm = ref({ asset_id: '', job_type: 'wu_diag', payload: '' })
let timer = null

const stateTagType = (s) => ({
  succeeded: 'success',
  failed: 'danger',
  running: 'primary',
  dispatched: 'warning',
  pending: 'info',
  expired: 'info'
}[s] || 'info')

const stateLabel = (s) => ({
  pending: '待下发',
  dispatched: '已下发',
  running: '执行中',
  succeeded: '成功',
  failed: '失败',
  expired: '已过期'
}[s] || s)

const formatTime = (v) => {
  if (!v) return '-'
  const d = dayjs(v)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(v)
}

const pretty = (v) => {
  if (v === null || v === undefined || v === '') return '（空）'
  try {
    const obj = typeof v === 'string' ? JSON.parse(v) : v
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(v)
  }
}

const payloadPreview = (v) => {
  if (v === null || v === undefined || v === '') return '-'
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > 60 ? s.slice(0, 60) + '…' : s
}

const loadJobs = async () => {
  loading.value = true
  try {
    const params = {}
    if (stateFilter.value) params.state = stateFilter.value
    if (typeFilter.value) params.job_type = typeFilter.value
    const res = await listAgentJobs(params)
    jobs.value = res.jobs || []
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

const showDetail = (row) => {
  detailRow.value = { ...row }
  detailVisible.value = true
}

const openDispatch = () => {
  dispatchForm.value = { asset_id: '', job_type: 'wu_diag', payload: '' }
  dispatchVisible.value = true
}

const submitDispatch = async () => {
  if (!dispatchForm.value.asset_id) {
    ElMessage.warning('请填写终端 asset_id')
    return
  }
  let payload = null
  const raw = (dispatchForm.value.payload || '').trim()
  if (raw) {
    try {
      payload = JSON.parse(raw)
    } catch {
      ElMessage.error('Payload 不是合法 JSON')
      return
    }
  }
  dispatching.value = true
  try {
    const res = await createAgentJob({
      asset_id: Number(dispatchForm.value.asset_id),
      job_type: dispatchForm.value.job_type,
      payload
    })
    ElMessage.success(`已下发 ${res.job_id}`)
    dispatchVisible.value = false
    stateFilter.value = ''
    loadJobs()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    dispatching.value = false
  }
}

onMounted(() => {
  loadJobs()
  timer = setInterval(() => {
    if (autoRefresh.value) loadJobs()
  }, 30000)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.zv-json-block {
  background: var(--el-fill-color-light, #f5f7fa);
  border: 1px solid var(--el-border-color-lighter, #e4e7ed);
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
</style>
