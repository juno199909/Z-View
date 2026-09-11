<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">事件列表</h2>
        <div class="zv-page-subtitle">活跃告警按终端聚合为事件（每终端一个开放事件），全部恢复自动关单</div>
      </div>
      <div class="zv-page-actions">
        <el-radio-group v-model="statusFilter" @change="loadIncidents">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="open">开放</el-radio-button>
          <el-radio-button value="acknowledged">已确认</el-radio-button>
          <el-radio-button value="resolved">已恢复</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" @click="loadAll" :loading="loading">刷新</el-button>
      </div>
    </div>

    <div class="zv-inc-stats">
      <div class="zv-stat-card">
        <div class="zv-stat-num zv-stat-open">{{ stats.open }}</div>
        <div class="zv-stat-label">开放</div>
      </div>
      <div class="zv-stat-card">
        <div class="zv-stat-num zv-stat-ack">{{ stats.acknowledged }}</div>
        <div class="zv-stat-label">已确认</div>
      </div>
      <div class="zv-stat-card">
        <div class="zv-stat-num zv-stat-critical">{{ stats.critical_open }}</div>
        <div class="zv-stat-label">含严重告警</div>
      </div>
      <div class="zv-stat-card">
        <div class="zv-stat-num">{{ stats.resolved }}</div>
        <div class="zv-stat-label">已恢复</div>
      </div>
    </div>

    <div class="zv-card">
      <el-table :data="incidents" v-loading="loading" size="default" stripe>
        <el-table-column prop="incident_id" label="事件 ID" min-width="220" show-overflow-tooltip />
        <el-table-column prop="hostname" label="终端" min-width="130" />
        <el-table-column label="级别" width="90">
          <template #default="{ row }">
            <el-tag :type="row.severity === 'critical' ? 'danger' : row.severity === 'warning' ? 'warning' : 'info'" size="small">
              {{ row.severity }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'resolved' ? 'success' : row.status === 'acknowledged' ? 'primary' : 'danger'"
              :effect="row.status === 'open' ? 'dark' : 'light'" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="alert_count" label="告警数" width="80" align="center" />
        <el-table-column prop="title" label="事件描述" min-width="240" show-overflow-tooltip />
        <el-table-column label="最近告警" min-width="160">
          <template #default="{ row }">{{ formatTime(row.last_alert_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.status === 'open'" size="small" @click="ack(row)">确认</el-button>
            <el-button v-if="row.status !== 'resolved'" size="small" type="warning" @click="close(row)">关闭</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'
import dayjs from 'dayjs'

const loading = ref(false)
const statusFilter = ref('')
const incidents = ref([])
const stats = ref({ open: 0, acknowledged: 0, resolved: 0, critical_open: 0 })

const statusLabel = (status) => ({
  open: '开放', acknowledged: '已确认', resolved: '已恢复'
}[status] || status)

const formatTime = (v) => {
  if (!v) return '-'
  const d = dayjs(v)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(v)
}

const loadAll = async () => {
  loading.value = true
  try {
    const query = statusFilter.value ? `?status=${statusFilter.value}` : ''
    const list = await request({ url: `/console/incidents${query}`, method: 'get' })
    incidents.value = list.incidents || []
    stats.value = await request({ url: '/console/incidents/stats', method: 'get' })
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

const ack = (row) => {
  ElMessageBox.confirm(`确认事件 ${row.incident_id}？`, '确认', { type: 'warning' })
    .then(async () => {
      await request({ url: `/console/incidents/${row.incident_id}/acknowledge`, method: 'post' })
      ElMessage.success('已确认')
      loadAll()
    })
    .catch(() => {})
}

const close = (row) => {
  ElMessageBox.confirm(`手动关闭事件 ${row.incident_id}？`, '关闭', { type: 'warning' })
    .then(async () => {
      await request({ url: `/console/incidents/${row.incident_id}/close`, method: 'post' })
      ElMessage.success('已关闭')
      loadAll()
    })
    .catch(() => {})
}

onMounted(loadAll)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1400px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; align-items: center; }
.zv-inc-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 16px;

  @media (max-width: 900px) { grid-template-columns: repeat(2, 1fr); }
}
.zv-stat-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 14px 18px;
  text-align: center;
}
.zv-stat-num { font-size: 26px; font-weight: 700; }
.zv-stat-open { color: var(--el-color-danger); }
.zv-stat-ack { color: var(--el-color-primary); }
.zv-stat-critical { color: var(--el-color-error); }
.zv-stat-label { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
</style>
