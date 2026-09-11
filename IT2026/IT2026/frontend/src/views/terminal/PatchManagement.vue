<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">补丁管理</h2>
        <div class="zv-page-subtitle">基于 Windows Update 状态的补丁可视（Phase 1：状态采集与查看；安装下发经任务通道后续开放）</div>
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
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'
import dayjs from 'dayjs'

const loading = ref(false)
const terminals = ref([])

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
</style>
