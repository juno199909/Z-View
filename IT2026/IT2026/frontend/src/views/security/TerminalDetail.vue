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
        <h3>远程安全运维</h3>
        <el-alert type="warning" :closable="false" show-icon style="margin-bottom:10px"
          title="命令经 Agent 控制通道（9001）直接下发并写审计日志；隔离将下发防火墙阻断规则、仅保留控制端口。" />
        <el-space wrap>
          <el-button type="primary" plain :loading="opLoading==='scan'" @click="doRemote('scan')">安全扫描</el-button>
          <el-button type="warning" plain :loading="opLoading==='kill'" @click="promptKillProcess">结束进程</el-button>
          <el-button type="danger" plain :loading="opLoading==='isolate'" @click="confirmOp('isolate')">隔离终端</el-button>
          <el-button type="success" plain :loading="opLoading==='unisolate'" @click="confirmOp('unisolate')">解除隔离</el-button>
        </el-space>
        <pre v-if="lastResult" class="zv-sec-op-result">{{ lastResult }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSecurityTerminalDetail, remoteScan, remoteKillProcess, remoteIsolate, remoteUnisolate } from '@/api/security'

const route = useRoute()
const loading = ref(false)
const detail = ref({})
const opLoading = ref('')
const lastResult = ref('')

const loadData = async () => {
  loading.value = true
  try { detail.value = await getSecurityTerminalDetail(route.params.id) }
  catch (e) { ElMessage.error('加载终端安全详情失败') }
  finally { loading.value = false }
}

const runOp = async (kind, fn) => {
  opLoading.value = kind
  try {
    const r = await fn()
    lastResult.value = JSON.stringify(r, null, 2)
    ElMessage.success(r?.dispatched === false ? '下发失败：' + (r?.error || '未知错误') : '命令已下发')
  } catch (e) { ElMessage.error('命令下发失败') }
  finally { opLoading.value = '' }
}

const doRemote = (kind) => runOp(kind, () => remoteScan(route.params.id))

const promptKillProcess = async () => {
  try {
    const { value } = await ElMessageBox.prompt(
      '输入进程 PID 或进程名（逗号分隔可同时填写，如: 1234, notepad.exe）',
      '结束进程', { inputPlaceholder: 'PID 或进程名', inputPattern: /\S/, inputErrorMessage: '不能为空' })
    const parts = value.split(',').map(s => s.trim()).filter(Boolean)
    const pid = parts.map(Number).find(n => !isNaN(n))
    const name = parts.find(s => isNaN(Number(s)))
    await runOp('kill', () => remoteKillProcess(route.params.id, { pid, name }))
  } catch (e) { if (e !== 'cancel') ElMessage.error('操作失败') }
}

const confirmOp = (kind) => {
  const label = kind === 'isolate'
    ? '隔离后该终端仅保留 Agent 控制通道，其余网络全部阻断。'
    : '将移除隔离防火墙规则，恢复网络。'
  ElMessageBox.confirm(label, kind === 'isolate' ? '隔离终端' : '解除隔离', { type: 'warning' })
    .then(() => runOp(kind, () => kind === 'isolate' ? remoteIsolate(route.params.id) : remoteUnisolate(route.params.id)))
    .catch(() => {})
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
.zv-sec-op-result { background: #1e1e28; color: #d6e2f0; padding: 12px; border-radius: 6px; font-size: 12px; max-height: 220px; overflow: auto; margin-top: 10px; }
</style>