<template>
  <div class="zv-sec-terminal-detail" v-loading="loading">
    <el-page-header @back="$router.back()" :title="'返回'">
      <template #content>{{ detail.asset?.hostname }} 安全详情</template>
    </el-page-header>
    <div v-if="detail.asset" class="zv-sec-detail-body">
      <el-descriptions :column="2" border title="终端信息">
        <el-descriptions-item label="主机名">{{ detail.asset.hostname }}</el-descriptions-item>
        <el-descriptions-item label="IP地址">{{ detail.asset.ip_address }}</el-descriptions-item>
        <el-descriptions-item label="MAC">{{ detail.asset.mac_address }}</el-descriptions-item>
        <el-descriptions-item label="操作系统">{{ detail.asset.os_type }} {{ detail.asset.os_version }}</el-descriptions-item>
        <el-descriptions-item label="Agent状态">{{ detail.asset.agent_install_status }}</el-descriptions-item>
        <el-descriptions-item label="Agent版本">{{ detail.asset.agent_version || '-' }}</el-descriptions-item>
        <el-descriptions-item label="最后心跳">{{ detail.asset.last_seen }}</el-descriptions-item>
      </el-descriptions>

      <div class="zv-sec-event-stats">
        <div class="zv-sec-stat-item" v-for="s in ['critical','high','medium','low']" :key="s">
          <div class="zv-sec-stat-num" :class="'sev-'+s">{{ detail.events?.by_severity?.[s] || 0 }}</div>
          <div class="zv-sec-stat-name">{{ ({critical:'严重',high:'高危',medium:'中危',low:'低危'})[s] }}</div>
        </div>
        <div class="zv-sec-stat-item">
          <div class="zv-sec-stat-num">{{ detail.usb_devices || 0 }}</div>
          <div class="zv-sec-stat-name">USB设备</div>
        </div>
      </div>

      <div class="zv-sec-section">
        <h3>生效策略</h3>
        <el-table :data="detail.policies || []" stripe size="small">
          <el-table-column prop="policy_name" label="策略名" min-width="140" />
          <el-table-column prop="policy_type" label="类型" width="120" />
          <el-table-column prop="scope_type" label="范围" width="100" />
        </el-table>
      </div>

      <div class="zv-sec-section">
        <h3>最近安全事件</h3>
        <el-table :data="detail.events?.recent || []" stripe size="small">
          <el-table-column prop="event_type" label="类型" width="120" />
          <el-table-column prop="severity" label="级别" width="80" />
          <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="100" />
          <el-table-column prop="occurred_at" label="时间" width="160" />
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getSecurityTerminalDetail } from '@/api/security'

const route = useRoute()
const loading = ref(false)
const detail = ref({})

const loadData = async () => {
  loading.value = true
  try { detail.value = await getSecurityTerminalDetail(route.params.id) }
  catch (e) { ElMessage.error('加载终端安全详情失败') }
  finally { loading.value = false }
}
onMounted(loadData)
</script>

<style scoped lang="scss">
.zv-sec-terminal-detail { padding: 16px; }
.zv-sec-detail-body { margin-top: 16px; }
.zv-sec-event-stats { display: flex; gap: 16px; margin: 20px 0; }
.zv-sec-stat-item { flex: 1; text-align: center; padding: 16px; background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.zv-sec-stat-num { font-size: 28px; font-weight: 700; color: #303133; }
.zv-sec-stat-name { font-size: 13px; color: #909399; margin-top: 4px; }
.sev-critical { color: #f56c6c; } .sev-high { color: #e6a23c; } .sev-medium { color: #409eff; } .sev-low { color: #67c23a; }
.zv-sec-section { margin-top: 20px; }
.zv-sec-section h3 { font-size: 15px; font-weight: 600; margin: 0 0 10px; color: #303133; }
</style>