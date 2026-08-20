<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端策略</h2>
        <div class="zv-page-subtitle">通过策略统一下发终端行为，最后更新 {{ updatedAt || '尚未更新' }}</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" :loading="loading" @click="loadPolicies">刷新</el-button>
        <el-button :icon="RefreshLeft" @click="resetToDefaults">恢复推荐值</el-button>
        <el-button type="primary" :icon="Check" :loading="saving" @click="savePolicies">保存并下发</el-button>
      </div>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="zv-tip"
      title="保存后所有可通讯的终端将在下一个心跳周期内自动同步最新策略，无需在终端上做任何操作。"
    />

    <div v-loading="loading" class="zv-policy-grid">
      <!-- 上报频率 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-policy-head">
          <div class="zv-policy-icon" style="background: linear-gradient(135deg, #3b82f6, #2563eb);">
            <el-icon :size="20"><Clock /></el-icon>
          </div>
          <div>
            <div class="zv-policy-title">数据上报频率</div>
            <div class="zv-policy-subtitle">终端向服务端同步各类数据的周期</div>
          </div>
        </div>

        <div class="zv-policy-item">
          <div class="zv-policy-label">心跳间隔</div>
          <div class="zv-policy-control">
            <el-input-number v-model="form.heartbeat" :min="5" :max="3600" :step="5" controls-position="right" style="width: 180px" />
            <span class="zv-unit">秒</span>
          </div>
          <div class="zv-policy-hint">终端在线状态与资源使用率的上报周期（5 - 3600 秒）</div>
        </div>

        <div class="zv-policy-item">
          <div class="zv-policy-label">软件清单</div>
          <div class="zv-policy-control">
            <el-input-number v-model="form.software" :min="10" :max="86400" :step="10" controls-position="right" style="width: 180px" />
            <span class="zv-unit">秒</span>
          </div>
          <div class="zv-policy-hint">已安装软件列表的上报周期（10 - 86400 秒）</div>
        </div>

        <div class="zv-policy-item">
          <div class="zv-policy-label">硬件信息</div>
          <div class="zv-policy-control">
            <el-input-number v-model="form.hardware" :min="300" :max="604800" :step="300" controls-position="right" style="width: 180px" />
            <span class="zv-unit">秒</span>
          </div>
          <div class="zv-policy-hint">操作系统 / CPU / 内存等静态信息的上报周期（300 - 604800 秒）</div>
        </div>
      </div>

      <!-- 远程桌面策略 -->
      <div class="zv-card zv-card-pad">
        <div class="zv-policy-head">
          <div class="zv-policy-icon" style="background: linear-gradient(135deg, #8b5cf6, #7c3aed);">
            <el-icon :size="20"><Monitor /></el-icon>
          </div>
          <div>
            <div class="zv-policy-title">远程桌面策略</div>
            <div class="zv-policy-subtitle">控制远控连接的用户授权与安全行为</div>
          </div>
        </div>

        <div class="zv-policy-toggle">
          <div class="zv-toggle-info">
            <div class="zv-toggle-label">远控需用户确认</div>
            <div class="zv-toggle-hint">开启后，有用户登录的终端在远程桌面连接前需用户点击同意</div>
          </div>
          <el-switch v-model="form.require_consent" />
        </div>

        <div class="zv-policy-item">
          <div class="zv-policy-label">确认超时时间</div>
          <div class="zv-policy-control">
            <el-input-number v-model="form.consent_timeout_seconds" :min="5" :max="3600" :step="5" controls-position="right" style="width: 180px" />
            <span class="zv-unit">秒</span>
          </div>
          <div class="zv-policy-hint">等待用户确认的超时时间（5 - 3600 秒），超时视为拒绝</div>
        </div>

        <div class="zv-policy-toggle">
          <div class="zv-toggle-info">
            <div class="zv-toggle-label">无人登录时允许远控</div>
            <div class="zv-toggle-hint">开启后无登录用户的终端也允许建立远程桌面会话（安全敏感，谨慎开启）</div>
          </div>
          <el-switch v-model="form.allow_if_no_user" />
        </div>

        <div class="zv-policy-toggle">
          <div class="zv-toggle-info">
            <div class="zv-toggle-label">UAC 弹窗免安全桌面</div>
            <div class="zv-toggle-hint">开启后 UAC 确认框直接显示在当前桌面，远程画面可查看可点击（推荐开启）</div>
          </div>
          <el-switch v-model="form.disable_uac_secure_desktop" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Refresh, RefreshLeft, Clock, Monitor } from '@element-plus/icons-vue'
import { getAgentPolicies, updateAgentPolicies } from '@/api/agentPolicy'

const loading = ref(false)
const saving = ref(false)
const updatedAt = ref('')

const DEFAULTS = {
  heartbeat: 30,
  software: 30,
  hardware: 86400,
  require_consent: true,
  consent_timeout_seconds: 90,
  allow_if_no_user: false,
  disable_uac_secure_desktop: true
}

const form = reactive({ ...DEFAULTS })

const applyPolicies = policies => {
  const intervals = policies?.intervals || {}
  const remote = policies?.remote_desktop || {}
  form.heartbeat = Number(intervals.heartbeat ?? DEFAULTS.heartbeat)
  form.software = Number(intervals.software ?? DEFAULTS.software)
  form.hardware = Number(intervals.hardware ?? DEFAULTS.hardware)
  form.require_consent = Boolean(remote.require_consent)
  form.consent_timeout_seconds = Number(remote.consent_timeout_seconds ?? DEFAULTS.consent_timeout_seconds)
  form.allow_if_no_user = Boolean(remote.allow_if_no_user)
  form.disable_uac_secure_desktop = remote.disable_uac_secure_desktop !== false
}

const loadPolicies = async () => {
  loading.value = true
  try {
    const data = await getAgentPolicies()
    applyPolicies(data?.policies)
    updatedAt.value = data?.updated_at || ''
  } catch (error) {
    console.error('加载终端策略失败:', error)
  } finally {
    loading.value = false
  }
}

const savePolicies = async () => {
  saving.value = true
  try {
    const data = await updateAgentPolicies({
      intervals: { heartbeat: form.heartbeat, software: form.software, hardware: form.hardware },
      remote_desktop: {
        require_consent: form.require_consent,
        consent_timeout_seconds: form.consent_timeout_seconds,
        allow_if_no_user: form.allow_if_no_user,
        disable_uac_secure_desktop: form.disable_uac_secure_desktop
      }
    })
    applyPolicies(data?.policies)
    updatedAt.value = data?.updated_at || ''
    ElMessage.success('策略已保存，在线终端将自动同步')
  } catch (error) {
    console.error('保存终端策略失败:', error)
  } finally {
    saving.value = false
  }
}

const resetToDefaults = () => {
  Object.assign(form, DEFAULTS)
  ElMessage.info('已恢复推荐值，点击"保存并下发"后生效')
}

onMounted(loadPolicies)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1200px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-tip { margin-bottom: 20px; }

.zv-policy-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;

  @media (max-width: 1100px) {
    grid-template-columns: 1fr;
  }
}

.zv-card-pad { padding: 24px 26px; }

.zv-policy-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 24px;
  padding-bottom: 18px;
  border-bottom: 1px solid $border-color-light;
}

.zv-policy-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.10);
}

.zv-policy-title { font-size: 16px; font-weight: 600; color: $text-primary; }
.zv-policy-subtitle { font-size: 12px; color: $text-tertiary; margin-top: 2px; }

.zv-policy-item {
  padding: 16px 0;
  border-bottom: 1px solid $slate-100;

  &:last-child { border-bottom: none; }
}

.zv-policy-label {
  font-size: 13px;
  font-weight: 500;
  color: $text-primary;
  margin-bottom: 8px;
}

.zv-policy-control {
  display: flex;
  align-items: center;
  gap: 8px;
}

.zv-unit {
  font-size: 12px;
  color: $text-tertiary;
}

.zv-policy-hint {
  font-size: 12px;
  color: $text-tertiary;
  margin-top: 6px;
  line-height: 1.5;
}

.zv-policy-toggle {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 0;
  border-bottom: 1px solid $slate-100;
  gap: 16px;

  &:last-child { border-bottom: none; }
}

.zv-toggle-info { flex: 1; }
.zv-toggle-label { font-size: 13px; font-weight: 500; color: $text-primary; margin-bottom: 4px; }
.zv-toggle-hint { font-size: 12px; color: $text-tertiary; line-height: 1.5; }
</style>
