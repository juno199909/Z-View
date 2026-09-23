<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">补丁管理</h2>
        <div class="zv-page-subtitle">基于 Windows Update 状态的补丁可视与安装下发（安装经任务通道执行，结果在任务中心可查）</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadPatches" :loading="loading">刷新</el-button>
      </div>
    </div>

    <div class="zv-patch-stats">
      <div class="zv-stat-card">
        <div class="zv-stat-num">{{ summary.terminals }}</div>
        <div class="zv-stat-label">已上报终端</div>
      </div>
      <div class="zv-stat-card">
        <div class="zv-stat-num zv-stat-pending">{{ summary.total_pending }}</div>
        <div class="zv-stat-label">待安装补丁</div>
      </div>
      <div class="zv-stat-card">
        <div class="zv-stat-num zv-stat-reboot">{{ summary.reboot_required_count }}</div>
        <div class="zv-stat-label">待重启终端</div>
      </div>
    </div>

    <div class="zv-card">
      <el-table :data="terminals" v-loading="loading" size="default" stripe>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="zv-patch-detail">
              <div v-if="!parsePatches(row.patches).length" class="zv-patch-empty">
                {{ row.error ? '采集失败：' + row.error : '无待安装补丁' }}
              </div>
              <el-table v-else :data="parsePatches(row.patches)" size="small">
                <el-table-column prop="kb" label="KB" width="110">
                  <template #default="{ row: p }">{{ p.kb || '-' }}</template>
                </el-table-column>
                <el-table-column prop="title" label="补丁标题" min-width="280" show-overflow-tooltip />
                <el-table-column prop="severity" label="级别" width="100">
                  <template #default="{ row: p }">{{ p.severity || '-' }}</template>
                </el-table-column>
                <el-table-column prop="size_mb" label="大小 (MB)" width="100" align="right" />
                <el-table-column label="需重启" width="90" align="center">
                  <template #default="{ row: p }">
                    <el-tag v-if="p.reboot_required" type="warning" size="small">是</el-tag>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="100" align="center">
                  <template #default="{ row: p }">
                    <el-button v-if="p.kb" size="small" type="primary" plain
                      :loading="installingKey === `${row.asset_id}:${p.kb}`"
                      @click="installPatch(row, p)">安装</el-button>
                    <el-tooltip v-else content="该补丁无 KB 编号，请使用『全部安装』" placement="top">
                      <span class="zv-patch-na">-</span>
                    </el-tooltip>
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="hostname" label="终端" min-width="130" />
        <el-table-column prop="ip_address" label="IP 地址" min-width="130" />
        <el-table-column prop="pending_count" label="待安装" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.pending_count > 0 ? 'warning' : 'success'" size="small">{{ row.pending_count }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="需重启" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.reboot_required" type="danger" size="small">是</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="最近扫描" min-width="160">
          <template #default="{ row }">{{ formatTime(row.last_scan) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="110" align="center" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" plain :disabled="!row.pending_count"
              :loading="installingKey === `all-${row.asset_id}`"
              @click="installAll(row)">全部安装</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'
import { createAgentJob } from '@/api/jobs'
import dayjs from 'dayjs'

const loading = ref(false)
const terminals = ref([])
const installingKey = ref('')

const summary = computed(() => ({
  terminals: terminals.value.length,
  total_pending: terminals.value.reduce((s, t) => s + (Number(t.pending_count) || 0), 0),
  reboot_required_count: terminals.value.filter(t => t.reboot_required).length,
}))

const parsePatches = (raw) => {
  if (!raw) return []
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    return Array.isArray(parsed) ? parsed : []
  } catch (e) {
    return []
  }
}

const formatTime = (v) => {
  if (!v) return '-'
  const d = dayjs(v)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(v)
}

// 安装下发：经任务通道 wu_install（payload.kb 缺省 = 安装全部待安装补丁）
const dispatchInstall = async (asset, payload, key, desc) => {
  try {
    await ElMessageBox.confirm(
      `将在 ${asset.hostname} 上${desc}。安装经任务通道异步执行（大补丁可能需要较长时间），执行结果可在任务中心查看。`,
      '下发安装任务',
      { type: 'warning', confirmButtonText: '下发安装', cancelButtonText: '取消' }
    )
    installingKey.value = key
    const res = await createAgentJob({
      asset_id: asset.asset_id,
      job_type: 'wu_install',
      payload,
    })
    ElMessage.success(`安装任务已下发（${res.job_id}），可在任务中心跟踪进度`)
  } catch (e) {
    if (e !== 'cancel' && e !== 'close') console.error('安装下发失败:', e)
  } finally {
    installingKey.value = ''
  }
}

const installAll = (row) =>
  dispatchInstall(row, {}, `all-${row.asset_id}`, '安装全部待安装补丁')

const installPatch = (row, p) =>
  dispatchInstall(row, { kb: p.kb }, `${row.asset_id}:${p.kb}`, `安装补丁 ${p.kb}（${p.title?.slice(0, 30) || ''}…）`)

const loadPatches = async () => {
  loading.value = true
  try {
    const result = await request({ url: '/console/patch-status', method: 'get' })
    terminals.value = result.terminals || []
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

onMounted(loadPatches)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1400px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; align-items: center; }
.zv-patch-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin-bottom: 16px;

  @media (max-width: 900px) { grid-template-columns: 1fr; }
}
.zv-stat-card {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 14px 18px;
  text-align: center;
}
.zv-stat-num { font-size: 26px; font-weight: 700; }
.zv-stat-pending { color: var(--el-color-warning); }
.zv-stat-reboot { color: var(--el-color-danger); }
.zv-stat-label { color: var(--el-text-color-secondary); font-size: 12px; margin-top: 4px; }
.zv-patch-detail { padding: 8px 16px; }
.zv-patch-empty { color: var(--el-text-color-secondary); font-size: 13px; padding: 8px 0; }
.zv-patch-na { color: var(--el-text-color-secondary); }
</style>
