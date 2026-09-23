<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">告警配置</h2>
        <div class="zv-page-subtitle">CPU / 内存 / 磁盘 / 健康度 / 离线判定阈值，保存后由告警评估线程（60 秒周期）自动生效；留空的项回落系统默认值</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadConfig" :loading="loading">刷新</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="zv-th-tip"
      title="填写规则"
      description="留空 = 使用系统默认值。CPU/内存/磁盘为正向指标（warning 需小于 critical）；健康度为反向指标（分数越低越严重，warning 需大于 critical）。" />

    <div class="zv-card zv-card-pad zv-th-form">
      <el-form :model="form" label-width="170px" :disabled="loading || saving">

        <el-divider content-position="left">CPU 使用率（%）</el-divider>
        <el-form-item label="提醒阈值">
          <el-input v-model="form.cpu_warning" placeholder="默认 80" clearable style="max-width: 180px" />
          <span class="zv-th-hint">达到此值触发提醒告警</span>
        </el-form-item>
        <el-form-item label="严重阈值">
          <el-input v-model="form.cpu_critical" placeholder="默认 90" clearable style="max-width: 180px" />
        </el-form-item>

        <el-divider content-position="left">内存使用率（%）</el-divider>
        <el-form-item label="提醒阈值">
          <el-input v-model="form.memory_warning" placeholder="默认 90" clearable style="max-width: 180px" />
        </el-form-item>
        <el-form-item label="严重阈值">
          <el-input v-model="form.memory_critical" placeholder="默认 95" clearable style="max-width: 180px" />
        </el-form-item>

        <el-divider content-position="left">磁盘使用率（%）</el-divider>
        <el-form-item label="提醒阈值">
          <el-input v-model="form.disk_warning" placeholder="默认 90" clearable style="max-width: 180px" />
        </el-form-item>
        <el-form-item label="严重阈值">
          <el-input v-model="form.disk_critical" placeholder="默认 95" clearable style="max-width: 180px" />
        </el-form-item>

        <el-divider content-position="left">健康度（分数，越低越严重）</el-divider>
        <el-form-item label="提醒阈值">
          <el-input v-model="form.health_warning" placeholder="默认 60（低于提醒）" clearable style="max-width: 180px" />
        </el-form-item>
        <el-form-item label="严重阈值">
          <el-input v-model="form.health_critical" placeholder="默认 40（低于严重）" clearable style="max-width: 180px" />
        </el-form-item>

        <el-divider content-position="left">离线判定（秒）</el-divider>
        <el-form-item label="离线判定秒数">
          <el-input v-model="form.offline_seconds" placeholder="默认 180" clearable style="max-width: 180px" />
          <span class="zv-th-hint">超过该秒数无心跳判定离线</span>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { fetchAlertThresholds, updateAlertThresholds } from '@/api/alert'

const loading = ref(false)
const saving = ref(false)
const form = ref({
  cpu_warning: null, cpu_critical: null,
  memory_warning: null, memory_critical: null,
  disk_warning: null, disk_critical: null,
  health_warning: null, health_critical: null,
  offline_seconds: null,
})

const loadConfig = async () => {
  loading.value = true
  try {
    const cfg = await fetchAlertThresholds()
    for (const key of Object.keys(form.value)) {
      form.value[key] = cfg.config[key] ?? null
    }
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const patch = {}
    for (const [key, value] of Object.entries(form.value)) {
      if (value === '' || value === null || value === undefined) {
        patch[key] = null
      } else {
        const num = Number(value)
        patch[key] = Number.isNaN(num) ? null : num
      }
    }
    await updateAlertThresholds(patch)
    ElMessage.success('阈值已保存，60 秒内生效')
    loadConfig()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    saving.value = false
  }
}

onMounted(loadConfig)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 900px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; align-items: center; }
.zv-th-tip { margin-bottom: 16px; }
.zv-th-form { max-width: 720px; }
.zv-th-hint { color: var(--el-text-color-secondary); font-size: 12px; margin-left: 12px; }
</style>
