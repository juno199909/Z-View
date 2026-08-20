<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端部署</h2>
        <div class="zv-page-subtitle">网页自助下载 · 静默安装 · 域批量部署 —— 新终端三步接入，无需手动拷贝</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadAll" :loading="loading">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="zv-deploy-tip"
      title="部署流程说明"
      description="① 下载安装包（或部署脚本）→ ② 终端管理员运行安装（脚本自动完成）→ ③ Agent 注册服务并上线，首个心跳自动完成设备凭据注册。脚本内嵌下载 Token，仅限内部分发，勿公开传播。" />

    <div class="zv-deploy-grid">
      <!-- 安装包下载 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-card-title">安装包</div>
        <div class="zv-deploy-desc">
          最新版 Z-View.exe（与"Agent 升级"共用同一仓库）。单台部署：下载后在终端以管理员运行安装命令即可。
        </div>
        <div class="zv-deploy-cmd" v-if="installCmd">
          <code>{{ installCmd }}</code>
          <el-button :icon="CopyDocument" size="small" text @click="copyText(installCmd)">复制</el-button>
        </div>
        <div class="zv-deploy-actions">
          <el-button type="primary" :icon="Download" :loading="downloading" @click="downloadPackage">
            下载安装包
          </el-button>
          <el-button :icon="Document" @click="openScript" :loading="scriptLoading">查看部署脚本</el-button>
        </div>
      </div>

      <!-- 批量部署说明 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-card-title">批量部署（域 / 三方桌管）</div>
        <ol class="zv-deploy-steps">
          <li>点击「查看部署脚本」，按需在 URL 追加 <code>?center=http://中心IP:8080</code> 指定终端侧访问地址</li>
          <li>将脚本放入域控开机/登录脚本目录，或通过三方桌管静默推送执行</li>
          <li>脚本自动完成：下载安装包 → 注册服务（失败自动重启）→ 延迟自启 → 上线</li>
        </ol>
        <el-alert type="warning" :closable="false" show-icon
          title="版本一致性"
          description="终端 Agent 版本以「Agent 升级」页下发的最新包为准；新装终端安装后自动进入同一升级通道。" />
      </div>
    </div>

    <!-- 终端安装状态 -->
    <div class="zv-card">
      <div class="zv-card-head">
        <div>
          <div class="zv-card-title">终端安装状态</div>
          <div class="zv-card-subtitle">已注册 {{ summary.enrolled || 0 }} 台 · 待注册 {{ summary.pending || 0 }} 台</div>
        </div>
      </div>
      <el-table :data="rows" v-loading="loading" size="default" stripe>
        <el-table-column prop="asset_id" label="资产ID" width="90" />
        <el-table-column prop="hostname" label="主机名" min-width="140" />
        <el-table-column prop="ip_address" label="IP 地址" min-width="130" />
        <el-table-column label="设备凭据" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.enrolled" type="success" size="small">已注册</el-tag>
            <el-tag v-else type="warning" size="small">待注册</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="资产状态" width="100" />
        <el-table-column label="最近心跳" min-width="160">
          <template #default="{ row }">{{ formatTime(row.last_seen) }}</template>
        </el-table-column>
        <el-table-column label="凭据注册时间" min-width="160">
          <template #default="{ row }">{{ formatTime(row.enrolled_at) }}</template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 部署脚本弹窗 -->
    <el-dialog v-model="scriptDialog" title="一键部署脚本（内嵌下载 Token，勿公开传播）" width="720px">
      <el-input
        v-model="scriptText"
        type="textarea"
        :rows="16"
        readonly
        class="zv-deploy-script"
      />
      <template #footer>
        <el-button @click="downloadScriptFile">下载 .ps1</el-button>
        <el-button type="primary" :icon="CopyDocument" @click="copyText(scriptText)">复制脚本</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { CopyDocument, Document, Download, Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'
import dayjs from 'dayjs'

const loading = ref(false)
const downloading = ref(false)
const scriptLoading = ref(false)
const scriptDialog = ref(false)
const scriptText = ref('')
const rows = ref([])

const summary = computed(() => {
  const enrolled = rows.value.filter(r => r.enrolled).length
  return { enrolled, pending: rows.value.length - enrolled }
})

// 中心地址以浏览器当前访问地址为准（与部署脚本生成的默认值一致）
const installCmd = computed(() => {
  return `Z-View.exe --install --quiet --server-url ${window.location.origin}`
})

const loadAll = async () => {
  loading.value = true
  try {
    const resp = await request.get('/console/agent-credentials')
    rows.value = resp?.data || []
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

const triggerDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const downloadPackage = async () => {
  downloading.value = true
  try {
    const blob = await request.get('/console/agent-deploy/package', { responseType: 'blob' })
    triggerDownload(blob, `Z-View-Setup-${dayjs().format('YYYYMMDD')}.exe`)
    ElMessage.success('安装包已开始下载')
  } catch (e) {
  } finally {
    downloading.value = false
  }
}

const openScript = async () => {
  scriptLoading.value = true
  try {
    scriptText.value = await request.get('/console/agent-deploy/script', {
      transformResponse: [d => d]
    })
    scriptDialog.value = true
  } catch (e) {
  } finally {
    scriptLoading.value = false
  }
}

const downloadScriptFile = () => {
  triggerDownload(new Blob([scriptText.value], { type: 'text/plain;charset=utf-8' }), 'deploy-zview-agent.ps1')
}

const copyText = async (text) => {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制到剪贴板')
  } catch (e) {
    ElMessage.error('复制失败，请手动选择复制')
  }
}

const formatTime = (v) => {
  if (!v) return '-'
  const d = dayjs(v)
  return d.isValid() ? d.format('YYYY-MM-DD HH:mm:ss') : String(v)
}

onMounted(loadAll)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1400px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; align-items: center; }
.zv-deploy-tip { margin-bottom: 16px; }
.zv-deploy-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 16px;

  @media (max-width: 1100px) {
    grid-template-columns: 1fr;
  }
}
.zv-card-title { font-weight: 600; margin-bottom: 10px; }
.zv-card-head { margin-bottom: 10px; }
.zv-deploy-desc { color: var(--el-text-color-regular); font-size: 13px; margin-bottom: 10px; line-height: 1.7; }
.zv-deploy-cmd {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 12px;

  code { font-size: 12px; word-break: break-all; flex: 1; }
}
.zv-deploy-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.zv-deploy-steps {
  margin: 0 0 12px 18px;
  color: var(--el-text-color-regular);
  font-size: 13px;
  line-height: 2;

  code { background: var(--el-fill-color-light); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
}
.zv-deploy-script { font-family: Consolas, monospace; }
</style>
