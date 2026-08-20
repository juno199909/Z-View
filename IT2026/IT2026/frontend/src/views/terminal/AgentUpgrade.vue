<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">Agent 升级</h2>
        <div class="zv-page-subtitle">上传新版 Z-View.exe 后，所有在线 Agent 将在一个心跳周期（30 秒）内自动升级，无需手动部署</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadAll" :loading="loading">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="zv-upgrade-tip"
      title="升级流程说明"
      description="上传 exe（平台记录 SHA256）→ Agent 心跳时发现新版本 → 自动下载并校验 SHA256 → 备份旧版 → 替换重启（失败自动回滚）→ 上报新版本。仅 1.3.1 及以上 Agent 支持自动升级，更早版本需手动引导部署一次。" />

    <div class="zv-upgrade-grid">
      <!-- 上传新版本 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-card-title">上传新版本</div>
        <el-upload
          ref="uploadRef"
          class="zv-upgrade-upload"
          drag
          :auto-upload="false"
          :limit="1"
          accept=".exe"
          :on-change="onFileChange"
          :on-remove="() => (file = null)"
        >
          <el-icon :size="36" class="zv-upload-icon"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽 Z-View.exe 到此处，或<em>点击选择</em></div>
          <template #tip>
            <div class="el-upload__tip">仅支持 .exe 文件，上传后按版本号保存（同版本覆盖）</div>
          </template>
        </el-upload>
        <div class="zv-upload-form">
          <el-input v-model="version" placeholder="版本号，如 1.3.3" style="width: 220px" clearable />
          <el-button type="primary" :loading="uploading" :disabled="!file || !version" @click="doUpload">
            上传并下发
          </el-button>
        </div>
      </div>

      <!-- 当前最新版本 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-card-title">平台当前版本</div>
        <template v-if="status.latest && status.latest.version">
          <div class="zv-latest-version">{{ status.latest.version }}</div>
          <div class="zv-latest-meta">
            <div>文件：{{ status.latest.filename }}</div>
            <div>大小：{{ formatSize(status.latest.size) }}</div>
            <div>SHA256：{{ shortHash(status.latest.sha256) }}</div>
            <div>上传：{{ status.latest.uploaded_by || '-' }} · {{ status.latest.uploaded_at || '-' }}</div>
          </div>
        </template>
        <el-empty v-else description="尚未上传任何升级包" :image-size="70" />
      </div>
    </div>

    <!-- 各终端版本对比 -->
    <div class="zv-card">
      <div class="zv-card-head">
        <div>
          <div class="zv-card-title">终端版本对比</div>
          <div class="zv-card-subtitle">共 {{ status.assets?.length || 0 }} 台已安装 Agent 的终端 · 心跳自动上报版本</div>
        </div>
      </div>
      <el-table v-loading="loading" :data="status.assets || []">
        <el-table-column prop="hostname" label="主机名" min-width="160" />
        <el-table-column prop="ip_address" label="IP 地址" min-width="140" />
        <el-table-column label="当前版本" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.current_version" size="small" effect="plain">v{{ row.current_version }}</el-tag>
            <span v-else class="zv-mono">未上报</span>
          </template>
        </el-table-column>
        <el-table-column label="升级状态" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.up_to_date" type="success" size="small">已是最新</el-tag>
            <el-tag v-else-if="row.current_version" type="warning" size="small">待升级</el-tag>
            <el-tag v-else type="info" size="small">等待上报</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后上报" width="180">
          <template #default="{ row }">{{ formatTime(row.last_report) }}</template>
        </el-table-column>
        <template #empty><el-empty description="暂无终端" :image-size="80" /></template>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, UploadFilled } from '@element-plus/icons-vue'
import request from '@/api/request'
import dayjs from 'dayjs'

const loading = ref(false)
const uploading = ref(false)
const file = ref(null)
const uploadRef = ref(null)
const version = ref('')
const status = ref({})
let pollTimer = null

const onFileChange = (uploadFile) => {
  if (!uploadFile.name.toLowerCase().endsWith('.exe')) {
    ElMessage.error('仅支持 .exe 文件')
    uploadRef.value?.clearFiles()
    return
  }
  file.value = uploadFile.raw
}

const doUpload = async () => {
  if (!file.value || !version.value) return
  uploading.value = true
  try {
    const form = new FormData()
    form.append('file', file.value)
    form.append('version', version.value.trim())
    await request.post('/agent/upgrade/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 300000
    })
    ElMessage.success(`版本 ${version.value} 已上传，在线 Agent 将自动升级`)
    file.value = null
    version.value = ''
    uploadRef.value?.clearFiles()
    loadAll()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    uploading.value = false
  }
}

const loadAll = async () => {
  loading.value = true
  try {
    status.value = await request.get('/agent/upgrade/status')
  } catch (e) {
  } finally {
    loading.value = false
  }
}

const formatSize = (bytes) => {
  if (!bytes) return '-'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}
const shortHash = (h) => (h ? String(h).substring(0, 16).toUpperCase() + '…' : '-')
const formatTime = (v) => {
  if (!v) return '-'
  const d = Number(v)
  return d ? dayjs(d * 1000).format('YYYY-MM-DD HH:mm:ss') : String(v)
}

onMounted(() => {
  loadAll()
  pollTimer = setInterval(loadAll, 30000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1400px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; align-items: center; }
.zv-mono { font-family: $font-mono; font-size: 12px; color: $text-tertiary; }

.zv-upgrade-tip { margin-bottom: 20px; }

.zv-upgrade-grid {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 16px;
  margin-bottom: 20px;
  @media (max-width: 1100px) { grid-template-columns: 1fr; }
}

.zv-card-title { font-size: 15px; font-weight: 600; color: $text-primary; margin-bottom: 14px; }
.zv-card-subtitle { font-size: 12px; color: $text-tertiary; margin-top: 2px; }

.zv-upgrade-upload { width: 100%; }
.zv-upload-icon { color: $brand-primary; margin-bottom: 8px; }

.zv-upload-form {
  display: flex;
  gap: 10px;
  margin-top: 14px;
  align-items: center;
}

.zv-latest-version {
  font-size: 34px;
  font-weight: 700;
  font-family: $font-mono;
  color: $brand-primary;
  margin-bottom: 12px;
}

.zv-latest-meta {
  font-size: 12px;
  color: $text-secondary;
  line-height: 2;
  word-break: break-all;
}

.zv-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 22px;
  border-bottom: 1px solid $border-color-light;
  background: $slate-50;
}

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell { background: #fafbfc; color: $text-secondary; font-weight: 600; font-size: 12px; }
}
</style>
