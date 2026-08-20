<template>
  <div class="zv-security-overview">
    <div class="zv-sec-welcome">
      <div>
        <h2 class="zv-sec-title">安全态势总览</h2>
        <p class="zv-sec-sub">Z-View 终端安全管理 · 实时掌握企业终端安全风险</p>
      </div>
      <el-button :icon="Refresh" plain @click="loadData">刷新</el-button>
    </div>

    <div class="zv-sec-stats">
      <div class="zv-sec-card zv-sec-primary">
        <div class="zv-sec-card-value">{{ overview.terminals?.total || 0 }}</div>
        <div class="zv-sec-card-label">终端总数</div>
        <div class="zv-sec-card-sub">在线 {{ overview.terminals?.online || 0 }} / 离线 {{ overview.terminals?.offline || 0 }}</div>
      </div>
      <div class="zv-sec-card zv-sec-danger">
        <div class="zv-sec-card-value">{{ overview.events?.open || 0 }}</div>
        <div class="zv-sec-card-label">待处置事件</div>
        <div class="zv-sec-card-sub">严重 {{ overview.events?.critical || 0 }} · 高危 {{ overview.events?.high || 0 }}</div>
      </div>
      <div class="zv-sec-card zv-sec-warning">
        <div class="zv-sec-card-value">{{ overview.events?.events_24h || 0 }}</div>
        <div class="zv-sec-card-label">24h 新增事件</div>
        <div class="zv-sec-card-sub">累计 {{ overview.events?.total || 0 }}</div>
      </div>
      <div class="zv-sec-card zv-sec-success">
        <div class="zv-sec-card-value">{{ overview.policies?.active || 0 }}</div>
        <div class="zv-sec-card-label">生效策略数</div>
        <div class="zv-sec-card-sub">绑定 {{ overview.policies?.bindings || 0 }} 条</div>
      </div>
    </div>

    <div class="zv-sec-charts">
      <div class="zv-sec-chart-box">
        <div class="zv-sec-chart-title">事件类型分布（近7天）</div>
        <v-chart class="zv-sec-chart" :option="typeChartOption" autoresize />
      </div>
      <div class="zv-sec-chart-box">
        <div class="zv-sec-chart-title">待处置事件级别</div>
        <v-chart class="zv-sec-chart" :option="severityChartOption" autoresize />
      </div>
    </div>

    <div class="zv-sec-risk" v-if="overview.risk_terminals?.length">
      <div class="zv-sec-risk-title">风险终端 TOP10</div>
      <el-table :data="overview.risk_terminals" stripe @row-click="goDetail">
        <el-table-column prop="hostname" label="主机名" min-width="140" />
        <el-table-column prop="ip_address" label="IP地址" min-width="130" />
        <el-table-column prop="event_count" label="未处置事件数" width="130">
          <template #default="{ row }"><el-tag type="danger">{{ row.event_count }}</el-tag></template>
        </el-table-column>
      </el-table>
    </div>

    <el-empty v-else description="暂无风险终端" :image-size="80" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart, BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { getSecurityOverview } from '@/api/security'

use([CanvasRenderer, PieChart, BarChart, GridComponent, LegendComponent, TooltipComponent])

const router = useRouter()
const overview = ref({})

const typeChartOption = computed(() => {
  const byType = overview.value.events?.by_type || {}
  const data = Object.entries(byType).map(([name, value]) => ({ name, value }))
  return {
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll' },
    series: [{
      type: 'pie', radius: ['40%', '70%'], data,
      label: { show: true, formatter: '{b}: {c}' }
    }]
  }
})

const severityChartOption = computed(() => {
  const bySev = overview.value.events?.by_severity || {}
  const order = ['critical', 'high', 'medium', 'low', 'info']
  const colors = { critical: '#f56c6c', high: '#e6a23c', medium: '#409eff', low: '#67c23a', info: '#909399' }
  const xData = order.filter(k => bySev[k])
  const yData = xData.map(k => bySev[k])
  return {
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: xData },
    yAxis: { type: 'value' },
    series: [{
      type: 'bar', data: yData, barWidth: '50%',
      itemStyle: { color: (p) => colors[xData[p.dataIndex]] || '#409eff' }
    }]
  }
})

const loadData = async () => {
  try {
    const res = await getSecurityOverview()
    overview.value = res
  } catch (e) {
    ElMessage.error('加载安全总览失败')
  }
}

const goDetail = (row) => {
  router.push(`/security/terminals/${row.id}`)
}

onMounted(loadData)
</script>

<style scoped lang="scss">
.zv-security-overview { padding: 16px; }
.zv-sec-welcome { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.zv-sec-title { font-size: 22px; font-weight: 600; margin: 0; color: #303133; }
.zv-sec-sub { font-size: 13px; color: #909399; margin: 6px 0 0; }
.zv-sec-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 20px; }
.zv-sec-card { padding: 20px; border-radius: 10px; color: #fff; }
.zv-sec-primary { background: linear-gradient(135deg, #409eff, #337ecc); }
.zv-sec-danger { background: linear-gradient(135deg, #f56c6c, #c45656); }
.zv-sec-warning { background: linear-gradient(135deg, #e6a23c, #b88230); }
.zv-sec-success { background: linear-gradient(135deg, #67c23a, #529b2e); }
.zv-sec-card-value { font-size: 32px; font-weight: 700; }
.zv-sec-card-label { font-size: 14px; margin-top: 4px; opacity: 0.9; }
.zv-sec-card-sub { font-size: 12px; margin-top: 6px; opacity: 0.75; }
.zv-sec-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
.zv-sec-chart-box { background: #fff; border-radius: 10px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
.zv-sec-chart-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #303133; }
.zv-sec-chart { height: 280px; }
.zv-sec-risk { background: #fff; border-radius: 10px; padding: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
.zv-sec-risk-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; color: #303133; }
</style>