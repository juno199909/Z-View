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
      <div class="zv-sec-card zv-sec-success">
        <div class="zv-sec-card-value">{{ overview.policies?.active || 0 }}</div>
        <div class="zv-sec-card-label">生效策略数</div>
        <div class="zv-sec-card-sub">绑定 {{ overview.policies?.bindings || 0 }} 条</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getSecurityOverview } from '@/api/security'

const overview = ref({})

const loadData = async () => {
  try {
    overview.value = await getSecurityOverview()
  } catch (e) {
    ElMessage.error('加载安全总览失败')
  }
}

onMounted(loadData)
</script>

<style scoped lang="scss">
.zv-security-overview { padding: 16px; }
.zv-sec-welcome { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.zv-sec-title { font-size: 22px; font-weight: 600; margin: 0; color: #303133; }
.zv-sec-sub { font-size: 13px; color: #909399; margin: 6px 0 0; }
.zv-sec-stats { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.zv-sec-card { padding: 20px; border-radius: 10px; color: #fff; }
.zv-sec-primary { background: linear-gradient(135deg, #409eff, #337ecc); }
.zv-sec-success { background: linear-gradient(135deg, #67c23a, #529b2e); }
.zv-sec-card-value { font-size: 32px; font-weight: 700; }
.zv-sec-card-label { font-size: 14px; margin-top: 4px; opacity: 0.9; }
.zv-sec-card-sub { font-size: 12px; margin-top: 6px; opacity: 0.75; }
</style>
