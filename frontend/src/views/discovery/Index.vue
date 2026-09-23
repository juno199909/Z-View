<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端发现</h2>
        <div class="zv-page-subtitle">Ping 扫描 / SNMP 探测 / 一键入库</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadRecentScans">刷新</el-button>
      </div>
    </div>

    <div class="zv-discovery-grid">
      <!-- Ping 扫描 -->
      <div class="zv-card zv-card-flex">
        <div class="zv-card-head">
          <div>
            <div class="zv-card-title">
              <el-icon><Connection /></el-icon>
              Ping 扫描 / 子网发现
            </div>
            <div class="zv-card-subtitle">输入 IP 范围扫描在线主机</div>
          </div>
          <el-tag type="info" size="small" effect="light">快速</el-tag>
        </div>

        <div class="zv-form">
          <div class="zv-form-item">
            <label class="zv-label">IP 范围</label>
            <el-input v-model="pingForm.ip_ranges" type="textarea" :rows="4"
              placeholder="192.168.1.0/24&#10;10.0.0.1-10.0.0.100&#10;192.168.1.1,192.168.1.2" />
            <div class="zv-hint">支持 CIDR、IP 段、逗号分隔，结果自动进入资产列表。</div>
          </div>
          <div class="zv-form-item">
            <label class="zv-label">并发数</label>
            <el-slider v-model="pingForm.concurrency" :min="10" :max="1000" :step="10" show-input />
          </div>
          <div class="zv-form-item">
            <label class="zv-label">超时 (ms)</label>
            <el-input-number v-model="pingForm.timeout" :min="1000" :max="10000" :step="500" style="width: 100%" />
          </div>
          <el-button type="primary" :icon="VideoPlay" :loading="pinging" @click="startPing">开始扫描</el-button>
        </div>
      </div>

      <!-- SNMP 扫描 -->
      <div class="zv-card zv-card-flex">
        <div class="zv-card-head">
          <div>
            <div class="zv-card-title">
              <el-icon><Cpu /></el-icon>
              SNMP 扫描
            </div>
            <div class="zv-card-subtitle">识别网络设备 / 交换机 / 路由器</div>
          </div>
          <el-tag type="warning" size="small" effect="light">深度</el-tag>
        </div>

        <div class="zv-form">
          <div class="zv-form-item">
            <label class="zv-label">Community</label>
            <el-input v-model="snmpForm.community" placeholder="public" />
          </div>
          <div class="zv-form-item">
            <label class="zv-label">IP 范围</label>
            <el-input v-model="snmpForm.ip_ranges" type="textarea" :rows="3" placeholder="192.168.1.0/24" />
          </div>
          <div class="zv-form-item">
            <label class="zv-label">SNMP 版本</label>
            <el-select v-model="snmpForm.version" style="width: 100%">
              <el-option label="v2c" value="2c" />
              <el-option label="v1" value="1" />
            </el-select>
          </div>
          <div class="zv-form-item">
            <label class="zv-label">超时 (ms)</label>
            <el-input-number v-model="snmpForm.timeout" :min="1000" :max="10000" :step="500" style="width: 100%" />
          </div>
          <el-button type="primary" :icon="VideoPlay" :loading="snmping" @click="startSnmp">开始扫描</el-button>
        </div>
      </div>
    </div>

    <div class="zv-card">
      <div class="zv-card-head">
        <div>
          <div class="zv-card-title">扫描结果</div>
          <div class="zv-card-subtitle">本次扫描发现的在线主机（后端自动入库）</div>
        </div>
        <div style="display: flex; gap: 8px; align-items: center;">
          <el-tag v-if="scanProgress" size="small" type="warning" effect="light">{{ scanProgress }}</el-tag>
          <el-tag v-if="results.length && !scanProgress" type="primary" effect="light" size="small">
            {{ results.length }} 台
          </el-tag>
        </div>
      </div>
      <el-table v-loading="pinging || snmping" :data="results">
        <el-table-column prop="ip_address" label="IP 地址" width="160">
          <template #default="{ row }"><span class="zv-mono">{{ row.ip_address }}</span></template>
        </el-table-column>
        <el-table-column prop="hostname" label="主机名" min-width="160" />
        <el-table-column prop="mac_address" label="MAC" width="160">
          <template #default="{ row }"><span class="zv-mono">{{ row.mac_address || '-' }}</span></template>
        </el-table-column>
        <el-table-column prop="vendor" label="厂商" width="180" />
        <el-table-column prop="device_type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="light">{{ row.device_type || '未知' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.already_exists" size="small" type="success" effect="light">已入库</el-tag>
            <el-tag v-else size="small" type="warning" effect="light">未入库</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" align="right">
          <template #default="{ row }">
            <el-button v-if="!row.already_exists" text type="primary" size="small" @click="importAsset(row)">入库</el-button>
            <el-button v-else text size="small" @click="viewAsset(row.ip_address)">查看</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无扫描结果" :image-size="80" /></template>
      </el-table>
    </div>

    <div class="zv-card">
      <div class="zv-card-head">
        <div>
          <div class="zv-card-title">历史扫描</div>
          <div class="zv-card-subtitle">最近 20 次扫描记录</div>
        </div>
      </div>
      <el-table :data="recentScans">
        <el-table-column label="时间" width="170">
          <template #default="{ row }"><span class="zv-mono">{{ row.created_at }}</span></template>
        </el-table-column>
        <el-table-column prop="scan_type" label="类型" width="100" />
        <el-table-column prop="ip_ranges" label="IP 范围" min-width="240" show-overflow-tooltip />
        <el-table-column prop="total" label="总数" width="80" align="center" />
        <el-table-column prop="online" label="在线" width="80" align="center" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'success' ? 'success' : 'danger'" effect="light">
              {{ row.status === 'success' ? '完成' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, VideoPlay, Connection, Cpu } from '@element-plus/icons-vue'
import { startPingScan, startSnmpScan, getRecentScans, getTaskDetail, importDiscoveredAsset } from '@/api/discovery'
import { getAssetOptions, getAssetList } from '@/api/asset'
import { useRouter } from 'vue-router'

const router = useRouter()
const results = ref([])
const recentScans = ref([])
const pinging = ref(false)
const snmping = ref(false)
const scanProgress = ref('')

const pingForm = reactive({ ip_ranges: '192.168.1.0/24', concurrency: 100, timeout: 3000 })
const snmpForm = reactive({ community: 'public', ip_ranges: '', version: '2c', timeout: 5000 })

const splitIpRanges = (text) => {
  const items = String(text || '')
    .split(/[\n,，;；]+/)
    .map(item => item.trim())
    .filter(Boolean)
  return [...new Set(items)]
}

let pollTimer = null
const stopPolling = () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}
onBeforeUnmount(stopPolling)

const pollTask = (taskId) => new Promise((resolve, reject) => {
  let waited = 0
  stopPolling()
  pollTimer = setInterval(async () => {
    waited += 1
    if (waited > 300) {
      stopPolling()
      reject(new Error('扫描超时'))
      return
    }
    try {
      const task = await getTaskDetail(taskId)
      const current = task.current ?? 0
      const total = task.total ?? 0
      scanProgress.value = `进度 ${current}/${total}，已发现 ${task.found ?? 0} 台`
      if (['completed', 'failed', 'cancelled'].includes(task.status)) {
        stopPolling()
        if (task.status === 'completed') resolve(task)
        else reject(new Error(task.error || `任务${task.status === 'cancelled' ? '已取消' : '失败'}`))
      }
    } catch (error) {
      stopPolling()
      reject(error)
    }
  }, 1000)
})

const applyTaskResults = async (task) => {
  // 后端扫描时已自动 upsert 入库，found_ips 只有 IP；回查资产库补全主机名/ID
  results.value = (task.found_ips || []).map(ip => ({
    ip_address: ip,
    hostname: '-',
    already_exists: true
  }))
  try {
    const res = await getAssetOptions()
    const byIp = {}
    for (const a of (res.data || [])) byIp[a.ip_address] = a
    results.value = results.value.map(r => {
      const match = byIp[r.ip_address]
      return match ? { ...r, id: match.id, hostname: match.hostname || '-' } : r
    })
  } catch {}
}

const startPing = async () => {
  const ipRanges = splitIpRanges(pingForm.ip_ranges)
  if (!ipRanges.length) return ElMessage.warning('请输入 IP 范围')
  pinging.value = true
  scanProgress.value = '任务提交中...'
  try {
    const res = await startPingScan({ ip_ranges: ipRanges, concurrency: pingForm.concurrency, timeout: pingForm.timeout })
    const task = await pollTask(res.task_id)
    await applyTaskResults(task)
    ElMessage.success(`扫描完成，发现 ${results.value.length} 台在线主机（已自动入库）`)
    loadRecentScans()
  } catch (error) {
    ElMessage.error('Ping 扫描失败：' + (error.message || '未知错误'))
  } finally {
    pinging.value = false
    scanProgress.value = ''
  }
}

const startSnmp = async () => {
  const ipRanges = splitIpRanges(snmpForm.ip_ranges)
  if (!ipRanges.length) return ElMessage.warning('请输入 IP 范围')
  snmping.value = true
  scanProgress.value = '任务提交中...'
  try {
    const targets = ipRanges.map(ip => ({ ip, community: snmpForm.community || 'public' }))
    const version = snmpForm.version === '1' ? 1 : 2
    const timeoutSeconds = Math.min(30, Math.max(1, Math.round(snmpForm.timeout / 1000)))
    const res = await startSnmpScan({ targets, version, timeout: timeoutSeconds })
    const task = await pollTask(res.task_id)
    await applyTaskResults(task)
    ElMessage.success(`扫描完成，发现 ${results.value.length} 台 SNMP 设备（已自动入库）`)
    loadRecentScans()
  } catch (error) {
    ElMessage.error('SNMP 扫描失败：' + (error.message || '未知错误'))
  } finally {
    snmping.value = false
    scanProgress.value = ''
  }
}

const importAsset = async (row) => {
  try {
    const res = await importDiscoveredAsset({
      ip_address: row.ip_address,
      hostname: row.hostname === '-' ? null : row.hostname,
      mac_address: row.mac_address || null,
      vendor: row.vendor || null,
      device_type: row.device_type || null
    })
    row.already_exists = true
    row.id = res.id
    ElMessage.success(res.already_exists ? '该主机已在资产库中' : '入库成功')
  } catch (error) {
    ElMessage.error('入库失败')
  }
}

const viewAsset = async (ip) => {
  try {
    const res = await getAssetList({ page: 1, page_size: 20, keyword: ip })
    const match = (res.data || []).find(item => item.ip_address === ip) || (res.data || [])[0]
    if (match?.id) {
      router.push(`/asset/detail/${match.id}`)
    } else {
      ElMessage.warning(`未在资产库中找到 ${ip}`)
    }
  } catch (error) {
    ElMessage.error('查询资产失败')
  }
}

const loadRecentScans = async () => {
  try {
    const res = await getRecentScans({ limit: 20 })
    recentScans.value = res.data || []
  } catch (error) {
    console.error('加载历史扫描失败:', error)
  }
}

onMounted(() => loadRecentScans())
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1600px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-discovery-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 16px;

  @media (max-width: 1100px) {
    grid-template-columns: 1fr;
  }
}

.zv-card-flex {
  display: flex;
  flex-direction: column;
  margin-bottom: 16px;
}

.zv-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 22px;
  border-bottom: 1px solid $border-color-light;
  background: $slate-50;
}

.zv-card-title {
  font-size: 15px;
  font-weight: 600;
  color: $text-primary;
  display: flex;
  align-items: center;
  gap: 8px;
  .el-icon { color: $brand-primary; }
}

.zv-card-subtitle { font-size: 12px; color: $text-tertiary; margin-top: 2px; }

.zv-form { padding: 20px 22px; display: flex; flex-direction: column; gap: 16px; }

.zv-form-item { display: flex; flex-direction: column; gap: 6px; }
.zv-label { font-size: 13px; color: $text-secondary; font-weight: 500; }
.zv-hint { font-size: 11px; color: $text-tertiary; margin-top: 4px; }

.zv-mono { font-family: $font-mono; font-size: 12px; color: $text-secondary; }

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell { background: #fafbfc; color: $text-secondary; font-weight: 600; font-size: 12px; }
  tr:hover > td.el-table__cell { background: rgba(37, 99, 235, 0.03) !important; }
  td.el-table__cell { border-bottom: 1px solid $slate-100 !important; }
  .el-table__inner-wrapper::before { height: 0; }
}
</style>
