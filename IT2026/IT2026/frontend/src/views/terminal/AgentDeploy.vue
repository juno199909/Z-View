<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">Agent部署</h2>
        <div class="zv-page-subtitle">网页自助下载 · 静默安装 · 域批量部署 —— 新终端三步接入，无需手动拷贝</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadAll" :loading="loading">刷新</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="zv-deploy-tip"
      title="部署流程说明"
      description="① 下载安装器（setup exe）→ ② 拷到终端双击运行，UAC 提权后「下一步 → 安装 → 完成」→ ③ Agent 自动注册服务并上线，首个心跳自动完成设备凭据注册。批量部署请用部署脚本（静默模式），脚本内嵌下载 Token，勿公开传播。" />

    <div class="zv-deploy-grid">
      <!-- 安装包下载 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-card-title">安装包</div>
        <div class="zv-deploy-desc">
          图形化安装器（内含完整 Agent）：下载后双击即可，跟着向导「下一步」完成，无需命令行。下方命令仅供静默/脚本部署。
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

      <!-- 退出密码 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-card-title">退出密码</div>
        <div class="zv-deploy-desc">
          启用后，终端托盘「退出代理」需输入此密码验证（管理台不可达时拒绝退出）。密码仅保存哈希，保存后不可查看。
        </div>
        <el-form label-width="0" @submit.prevent>
          <el-input v-model="exitPassword" type="password" show-password
            :placeholder="exitPolicy.enabled ? '输入新密码以更换（至少 4 位）' : '设置退出密码（至少 4 位）'"
            style="max-width: 280px" />
        </el-form>
        <div class="zv-deploy-actions" style="margin-top: 10px">
          <el-tag :type="exitPolicy.enabled ? 'success' : 'info'" size="small">
            {{ exitPolicy.enabled ? '已启用验证' : '未启用' }}
          </el-tag>
          <el-button type="primary" size="small" :loading="exitPolicySaving"
            :disabled="!exitPassword || exitPassword.length < 4" @click="saveExitPolicy(true)">
            {{ exitPolicy.enabled ? '更换密码' : '启用验证' }}
          </el-button>
          <el-button v-if="exitPolicy.enabled" size="small" :loading="exitPolicySaving"
            @click="saveExitPolicy(false)">
            关闭验证
          </el-button>
        </div>
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
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { CopyDocument, Document, Download, Refresh } from '@element-plus/icons-vue'
import request from '@/api/request'
import { getAuthToken } from '@/api/auth-session'
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

const exitPolicy = ref({ enabled: false })
const exitPolicySaving = ref(false)
const exitPassword = ref('')

const loadExitPolicy = async () => {
  try {
    const resp = await request.get('/console/agent-exit-policy')
    exitPolicy.value = resp || { enabled: false }
  } catch (e) {
    // 错误提示由全局拦截器弹出
  }
}

const saveExitPolicy = async (enabled) => {
  exitPolicySaving.value = true
  try {
    const resp = await request.put('/console/agent-exit-policy', {
      enabled,
      password: enabled ? exitPassword.value : undefined
    })
    ElMessage.success(resp?.message || '已保存')
    exitPassword.value = ''
    await loadExitPolicy()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    exitPolicySaving.value = false
  }
}

const triggerDownload = (blob, filename) => {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  // 大文件场景：延迟回收，避免个别浏览器在下载尚未建立时即失效
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}

const downloadPackage = async () => {
  downloading.value = true
  try {
<<<<<<< HEAD
    // setup exe ~100MB：全局 30s 超时会掐断大包下载，这里显式不限时；
    // 共享拦截器只回 data、拿不到响应头，故用裸 axios 读取服务端文件名
    const resp = await axios.get('/api/v1/console/agent-deploy/package', {
      responseType: 'blob',
      timeout: 0,
      headers: getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {}
    })
    const filename = resp.headers?.['x-agent-package-filename']
      || `Z-View-Setup-${dayjs().format('YYYYMMDD')}.exe`
    triggerDownload(resp.data, filename)
=======
    const blob = await request.get('/console/agent-deploy/package', { responseType: 'blob' })
    triggerDownload(blob, `Z-View-Agent-${dayjs().format('YYYYMMDD')}.zip`)
>>>>>>> 5008f5d2d3812fb8acdbedcc26f6d151bad8f58b
    ElMessage.success('安装包已开始下载')
  } catch (e) {
    const msg = e?.response?.status === 404
      ? '暂无可用安装包，请先在「Agent 升级」页上传'
      : '下载失败，请重试'
    ElMessage.error(msg)
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

onMounted(() => {
  loadAll()
  loadExitPolicy()
})
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
