<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">Agent 健康度</h2>
        <div class="zv-page-subtitle">Agent 队列安装、在线与版本健康概况</div>
      </div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-row :gutter="16" v-loading="loading">
      <el-col :span="6" v-for="card in summaryCards" :key="card.label">
        <div class="zv-card zv-stat-card">
          <div class="zv-stat-value" :style="{ color: card.color }">{{ card.value }}</div>
          <div class="zv-stat-label">{{ card.label }}</div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px;">
      <el-col :span="10">
        <div class="zv-card zv-card-pad">
          <h3 class="zv-section-title">Agent 版本分布</h3>
          <el-table :data="versionRows" size="small" stripe>
            <el-table-column label="版本" min-width="120">
              <template #default="{row}">
                <el-tag size="small" :type="row.version === latestVersion ? 'success' : 'info'">v{{ row.version }}</el-tag>
                <span v-if="row.version === latestVersion" class="zv-latest-tag">最新</span>
              </template>
            </el-table-column>
            <el-table-column prop="count" label="终端数" width="90" />
            <el-table-column label="占比" min-width="140">
              <template #default="{row}">
                <el-progress :percentage="row.percent" :stroke-width="8" :show-text="false" />
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
      <el-col :span="14">
        <div class="zv-card zv-card-pad">
          <h3 class="zv-section-title">
            异常清单
            <el-tag v-if="anomalies.length" size="small" type="danger" style="margin-left: 8px">{{ anomalies.length }}</el-tag>
            <el-tag v-else size="small" type="success" style="margin-left: 8px">无异常</el-tag>
          </h3>
          <el-table :data="anomalies" size="small" stripe max-height="420">
            <el-table-column prop="hostname" label="终端" min-width="130" show-overflow-tooltip />
            <el-table-column prop="ip_address" label="IP" width="130" />
            <el-table-column label="类型" width="110">
              <template #default="{row}">
                <el-tag size="small" :type="anomalyType(row.type).tag">{{ anomalyType(row.type).label }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="detail" label="详情" min-width="180" show-overflow-tooltip />
          </el-table>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'

const loading = ref(false)
const health = ref(null)

const load = async () => {
  loading.value = true
  try {
    const res = await request({ url: '/agent-fleet/health', method: 'get' })
    health.value = res?.data || res
  } catch (e) {
    ElMessage.error('加载健康度失败')
  } finally {
    loading.value = false
  }
}

const summary = computed(() => health.value?.summary || {})
const summaryCards = computed(() => [
  { label: '终端总数', value: summary.value.total ?? '-', color: '#409eff' },
  { label: '在线', value: summary.value.online ?? '-', color: '#67c23a' },
  { label: '已装 Agent', value: summary.value.installed ?? '-', color: '#8b5cf6' },
  { label: '异常', value: summary.value.anomaly_count ?? '-', color: '#e6a23c' },
])

const latestVersion = computed(() => health.value?.latest_version || '')
const versionRows = computed(() => {
  const versions = health.value?.versions || {}
  const total = Object.values(versions).reduce((a, b) => a + b, 0) || 1
  return Object.entries(versions)
    .map(([version, count]) => ({ version, count, percent: Math.round((count / total) * 100) }))
    .sort((a, b) => b.count - a.count)
})
const anomalies = computed(() => health.value?.anomalies || [])

const anomalyType = type => ({
  never_seen: { label: '从未上报', tag: 'danger' },
  stale_heartbeat: { label: '心跳中断', tag: 'warning' },
  version_lag: { label: '版本落后', tag: 'info' },
}[type] || { label: type, tag: 'info' })

onMounted(load)
</script>

<style lang="scss" scoped>
.zv-page { padding: 16px; max-width: 1300px; margin: 0 auto; }
.zv-page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; gap: 12px; }
.zv-page-title { font-size: 20px; font-weight: 600; margin: 0; }
.zv-page-subtitle { font-size: 12px; color: #909399; margin-top: 4px; }
.zv-stat-card { padding: 18px 22px; text-align: center; }
.zv-stat-value { font-size: 30px; font-weight: 700; line-height: 1.2; }
.zv-stat-label { font-size: 12px; color: #909399; margin-top: 4px; }
.zv-section-title { font-size: 14px; font-weight: 600; margin: 0 0 12px; display: flex; align-items: center; }
.zv-latest-tag { margin-left: 6px; font-size: 12px; color: #67c23a; }
</style>
