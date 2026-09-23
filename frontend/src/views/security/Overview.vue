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
        <div class="zv-sec-card-sub">绑定 {{ overview.policies?.bindings || 0 }} 条 · 防火墙 {{ overview.policies?.firewall || 0 }}</div>
      </div>
      <div class="zv-sec-card zv-sec-warning">
        <div class="zv-sec-card-value">{{ overview.usb?.devices || 0 }}</div>
        <div class="zv-sec-card-label">USB 设备</div>
        <div class="zv-sec-card-sub">近 24h 事件 {{ overview.usb?.events_24h || 0 }} 条</div>
      </div>
      <div class="zv-sec-card" :class="(overview.executions?.failed_24h || 0) > 0 ? 'zv-sec-danger' : 'zv-sec-info'">
        <div class="zv-sec-card-value">{{ overview.executions?.total_24h || 0 }}</div>
        <div class="zv-sec-card-label">24h 策略执行</div>
        <div class="zv-sec-card-sub">失败 {{ overview.executions?.failed_24h || 0 }} 次</div>
      </div>
    </div>

    <div class="zv-ov-grid">
      <div class="zv-ov-panel">
        <h3 class="zv-ov-panel-title">最近 USB 事件</h3>
        <el-table :data="overview.recent_usb_events || []" stripe size="small" max-height="320">
          <el-table-column prop="occurred_at" label="时间" width="160" />
          <el-table-column prop="event_type" label="类型" width="90">
            <template #default="{row}">
              <el-tag size="small" :type="usbTypeTag(row.event_type)">{{ usbTypeLabel(row.event_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="friendly_name" label="设备" min-width="150" show-overflow-tooltip>
            <template #default="{row}">{{ row.friendly_name || row.device_id || '—' }}</template>
          </el-table-column>
          <el-table-column prop="hostname" label="终端" min-width="120" show-overflow-tooltip>
            <template #default="{row}">{{ row.hostname || '—' }}</template>
          </el-table-column>
          <el-table-column prop="vid_pid" label="VID:PID" width="110">
            <template #default="{row}">{{ row.vid_pid || '—' }}</template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无 USB 事件" :image-size="60" />
          </template>
        </el-table>
      </div>

      <div class="zv-ov-panel">
        <h3 class="zv-ov-panel-title">最近失败的策略执行</h3>
        <el-table :data="overview.recent_failed || []" stripe size="small" max-height="320">
          <el-table-column prop="policy_name" label="策略" min-width="140" show-overflow-tooltip />
          <el-table-column prop="hostname" label="终端" min-width="110" show-overflow-tooltip>
            <template #default="{row}">{{ row.hostname || '—' }}</template>
          </el-table-column>
          <el-table-column prop="failed_rules" label="失败规则" width="80" />
          <el-table-column prop="executed_at" label="时间" width="160" />
          <template #empty>
            <el-empty description="暂无失败记录" :image-size="60" />
          </template>
        </el-table>
      </div>
    </div>

    <div class="zv-ov-links">
      <router-link to="/security/terminals" class="zv-ov-link">终端安全 →</router-link>
      <router-link to="/security/firewall" class="zv-ov-link">防火墙管理 →</router-link>
      <router-link to="/security/usb" class="zv-ov-link">USB 管控 →</router-link>
      <router-link to="/security/policies" class="zv-ov-link">策略中心 →</router-link>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getSecurityOverview } from '@/api/security'

const overview = ref({})

const usbTypeLabel = (v) => ({ insert: '接入', remove: '移除', blocked: '已阻断', allowed: '已放行' })[v] || v
const usbTypeTag = (v) => ({ blocked: 'danger', insert: 'primary', remove: 'info', allowed: 'success' })[v] || 'info'

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
.zv-sec-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; }
.zv-sec-card { padding: 20px; border-radius: 10px; color: #fff; }
.zv-sec-primary { background: linear-gradient(135deg, #409eff, #337ecc); }
.zv-sec-success { background: linear-gradient(135deg, #67c23a, #529b2e); }
.zv-sec-warning { background: linear-gradient(135deg, #e6a23c, #cf8a2a); }
.zv-sec-danger { background: linear-gradient(135deg, #f56c6c, #d94848); }
.zv-sec-info { background: linear-gradient(135deg, #909399, #6b6e78); }
.zv-sec-card-value { font-size: 32px; font-weight: 700; }
.zv-sec-card-label { font-size: 14px; margin-top: 4px; opacity: 0.9; }
.zv-sec-card-sub { font-size: 12px; margin-top: 6px; opacity: 0.75; }
.zv-ov-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 20px; }
.zv-ov-panel { background: #fff; border-radius: 10px; padding: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.zv-ov-panel-title { font-size: 15px; font-weight: 600; margin: 0 0 10px; color: #303133; }
.zv-ov-links { margin-top: 18px; display: flex; gap: 22px; }
.zv-ov-link { font-size: 13px; color: #409eff; text-decoration: none; }
.zv-ov-link:hover { text-decoration: underline; }
@media (max-width: 1200px) {
  .zv-sec-stats { grid-template-columns: repeat(2, 1fr); }
  .zv-ov-grid { grid-template-columns: 1fr; }
}
</style>
