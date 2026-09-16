<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">Agent策略</h2>
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

        <div class="zv-policy-toggle">
          <div class="zv-toggle-info">
            <div class="zv-toggle-label">允许远程 Shell</div>
            <div class="zv-toggle-hint">开启后远程桌面会话内可打开命令终端执行 PowerShell 命令（默认关闭，所有命令将被审计记录）</div>
          </div>
          <el-switch v-model="form.allow_shell" />
        </div>

        <div v-if="form.allow_shell" class="zv-policy-item">
          <div class="zv-policy-label">命令超时时间</div>
          <div class="zv-policy-control">
            <el-input-number v-model="form.shell_timeout_seconds" :min="5" :max="600" :step="5" controls-position="right" style="width: 180px" />
            <span class="zv-unit">秒</span>
          </div>
          <div class="zv-policy-hint">单条命令最长执行时间（5 - 600 秒），超时自动终止进程</div>
        </div>
      </div>

      <!-- 分组/终端覆盖策略（统一策略引擎 P1-05） -->
      <div class="zv-card zv-card-pad zv-override-card">
        <div class="zv-policy-head">
          <div class="zv-policy-icon" style="background: linear-gradient(135deg, #f59e0b, #d97706);">
            <el-icon :size="20"><Operation /></el-icon>
          </div>
          <div style="flex: 1;">
            <div class="zv-policy-title">分组 / 终端覆盖策略</div>
            <div class="zv-policy-subtitle">按分组或单终端覆盖上方全局默认值（优先级：终端 &gt; 分组 &gt; 全局），终端心跳时自动生效</div>
          </div>
          <el-button size="small" type="primary" plain :icon="Plus" @click="openOverrideDialog">新建覆盖策略</el-button>
        </div>

        <el-table :data="overridePolicies" size="small" stripe>
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="policy_name" label="策略名" min-width="140" show-overflow-tooltip />
          <el-table-column prop="priority" label="优先级" width="70" />
          <el-table-column prop="enabled" label="状态" width="80">
            <template #default="{row}">
              <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="binding_count" label="绑定数" width="70" />
          <el-table-column label="操作" width="200" fixed="right">
            <template #default="{row}">
              <el-button link type="primary" size="small" @click="toggleOverride(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
              <el-button link type="primary" size="small" @click="openBindDialog(row)">绑定</el-button>
              <el-button link type="danger" size="small" @click="removeOverride(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </div>

    <!-- 新建覆盖策略 -->
    <el-dialog v-model="overrideDialogVisible" title="新建分组/终端覆盖策略" width="520px" append-to-body>
      <el-form label-width="110px">
        <el-form-item label="策略名" required>
          <el-input v-model="overrideForm.policy_name" placeholder="如：机房 A 高频心跳" maxlength="100" />
        </el-form-item>
        <el-form-item label="生效范围" required>
          <el-radio-group v-model="overrideForm.scope_type">
            <el-radio-button value="group">按分组</el-radio-button>
            <el-radio-button value="asset">按终端</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="目标" required>
          <el-select v-if="overrideForm.scope_type === 'group'" v-model="overrideForm.scope_id" placeholder="选择分组" style="width: 100%">
            <el-option v-for="g in groups" :key="g.id" :label="g.name || g.group_name" :value="g.id" />
          </el-select>
          <el-select v-else v-model="overrideForm.scope_id" filterable placeholder="选择终端" style="width: 100%">
            <el-option v-for="a in assets" :key="a.id" :label="`${a.hostname}（${a.ip_address || a.id}）`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="overrideForm.priority" :min="0" :max="100" controls-position="right" />
          <span class="zv-unit">同范围多条策略时数值大者生效</span>
        </el-form-item>
        <el-divider content-position="left">覆盖配置（仅勾选/填写项会覆盖全局值）</el-divider>
        <el-form-item label="心跳间隔">
          <el-input-number v-model="overrideForm.heartbeat" :min="5" :max="3600" :step="5" controls-position="right" style="width: 160px" />
          <span class="zv-unit">秒</span>
        </el-form-item>
        <el-form-item label="远控需用户确认">
          <el-switch v-model="overrideForm.require_consent" />
        </el-form-item>
        <el-form-item label="允许远程 Shell">
          <el-switch v-model="overrideForm.allow_shell" />
        </el-form-item>
        <el-form-item v-if="overrideForm.allow_shell" label="Shell 超时">
          <el-input-number v-model="overrideForm.shell_timeout_seconds" :min="5" :max="600" :step="5" controls-position="right" style="width: 160px" />
          <span class="zv-unit">秒</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="overrideDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="overrideSaving" @click="submitOverride">创建并绑定</el-button>
      </template>
    </el-dialog>

    <!-- 绑定管理 -->
    <el-dialog v-model="bindDialogVisible" title="管理绑定范围" width="560px" append-to-body>
      <div v-if="bindTarget">
        <p style="margin: 0 0 10px; font-size: 13px;">
          策略：<b>{{ bindTarget.policy_name }}</b>（优先级 {{ bindTarget.priority }}）
        </p>
        <el-table :data="bindRows" size="small" stripe v-loading="bindLoading">
          <el-table-column label="范围" width="90">
            <template #default="{row}">{{ scopeLabel(row.scope_type) }}</template>
          </el-table-column>
          <el-table-column label="目标" min-width="160">
            <template #default="{row}">{{ scopeTargetLabel(row) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="80">
            <template #default="{row}">
              <el-button link type="danger" size="small" @click="removeBinding(row)">解绑</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-divider style="margin: 14px 0 10px;" />
        <div style="display: flex; gap: 8px; align-items: center;">
          <el-radio-group v-model="bindForm.scope_type" size="small">
            <el-radio-button value="group">分组</el-radio-button>
            <el-radio-button value="asset">终端</el-radio-button>
          </el-radio-group>
          <el-select v-if="bindForm.scope_type === 'group'" v-model="bindForm.scope_id" placeholder="选择分组" size="small" style="width: 220px">
            <el-option v-for="g in groups" :key="g.id" :label="g.name || g.group_name" :value="g.id" />
          </el-select>
          <el-select v-else v-model="bindForm.scope_id" filterable placeholder="选择终端" size="small" style="width: 220px">
            <el-option v-for="a in assets" :key="a.id" :label="`${a.hostname}（${a.ip_address || a.id}）`" :value="a.id" />
          </el-select>
          <el-button type="primary" size="small" :loading="bindSaving" @click="addBinding">添加绑定</el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Refresh, RefreshLeft, Clock, Monitor, Plus, Operation } from '@element-plus/icons-vue'
import { getAgentPolicies, updateAgentPolicies } from '@/api/agentPolicy'
import {
  getSecurityPolicies, createSecurityPolicy, updateSecurityPolicy, deleteSecurityPolicy,
  getSecurityPolicyDetail, bindSecurityPolicy, unbindSecurityPolicy
} from '@/api/security'
import { getGroups } from '@/api/group'
import { getAssetList } from '@/api/asset'

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
  disable_uac_secure_desktop: true,
  allow_shell: false,
  shell_timeout_seconds: 60
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
  form.allow_shell = Boolean(remote.allow_shell)
  form.shell_timeout_seconds = Number(remote.shell_timeout_seconds ?? DEFAULTS.shell_timeout_seconds)
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
        disable_uac_secure_desktop: form.disable_uac_secure_desktop,
        allow_shell: form.allow_shell,
        shell_timeout_seconds: form.shell_timeout_seconds
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

// ============ 分组/终端覆盖策略（统一策略引擎 P1-05） ============
const overridePolicies = ref([])
const overrideDialogVisible = ref(false)
const overrideSaving = ref(false)
const overrideForm = reactive({
  policy_name: '', scope_type: 'group', scope_id: null, priority: 10,
  heartbeat: 30, require_consent: true, allow_shell: false, shell_timeout_seconds: 60
})
const groups = ref([])
const assets = ref([])
const bindDialogVisible = ref(false)
const bindTarget = ref(null)
const bindRows = ref([])
const bindLoading = ref(false)
const bindSaving = ref(false)
const bindForm = reactive({ scope_type: 'group', scope_id: null })
const groupNameMap = ref({})
const assetNameMap = ref({})

const scopeLabel = v => ({ global: '全局', group: '分组', asset: '终端' }[v] || v)

const scopeTargetLabel = row => {
  if (row.scope_type === 'global') return '全部终端'
  if (row.scope_type === 'group') return groupNameMap.value[row.scope_id] || `分组 #${row.scope_id}`
  return assetNameMap.value[row.scope_id] || `终端 #${row.scope_id}`
}

const loadScopeOptions = async () => {
  try {
    const [g, a] = await Promise.all([getGroups(), getAssetList({ page: 1, page_size: 500 })])
    groups.value = Array.isArray(g?.data) ? g.data : (Array.isArray(g) ? g : [])
    groupNameMap.value = Object.fromEntries(groups.value.map(x => [x.id, x.name || x.group_name]))
    assets.value = Array.isArray(a?.data) ? a.data : []
    assetNameMap.value = Object.fromEntries(assets.value.map(x => [x.id, x.hostname]))
  } catch (e) {
    console.error('加载分组/终端失败:', e)
  }
}

const loadOverridePolicies = async () => {
  try {
    const r = await getSecurityPolicies({ page: 1, page_size: 200, policy_type: 'agent' })
    overridePolicies.value = (r.data || []).map(p => ({ ...p, _toggling: false }))
  } catch (e) {
    console.error('加载覆盖策略失败:', e)
  }
}

const openOverrideDialog = async () => {
  Object.assign(overrideForm, {
    policy_name: '', scope_type: 'group', scope_id: null, priority: 10,
    heartbeat: form.heartbeat, require_consent: form.require_consent,
    allow_shell: form.allow_shell, shell_timeout_seconds: form.shell_timeout_seconds
  })
  await loadScopeOptions()
  overrideDialogVisible.value = true
}

const submitOverride = async () => {
  if (!overrideForm.policy_name.trim()) {
    ElMessage.warning('请填写策略名')
    return
  }
  if (overrideForm.scope_type === 'group' && !overrideForm.scope_id) {
    ElMessage.warning('请选择分组')
    return
  }
  if (overrideForm.scope_type === 'asset' && !overrideForm.scope_id) {
    ElMessage.warning('请选择终端')
    return
  }
  overrideSaving.value = true
  try {
    const config = {
      intervals: { heartbeat: overrideForm.heartbeat },
      remote_desktop: {
        require_consent: overrideForm.require_consent,
        allow_shell: overrideForm.allow_shell,
        shell_timeout_seconds: overrideForm.shell_timeout_seconds
      }
    }
    const created = await createSecurityPolicy({
      policy_name: overrideForm.policy_name.trim(),
      policy_type: 'agent',
      description: '分组/终端覆盖策略（终端 Agent）',
      priority: overrideForm.priority,
      config_json: JSON.stringify(config)
    })
    await bindSecurityPolicy(created.id, {
      scope_type: overrideForm.scope_type,
      scope_id: overrideForm.scope_type === 'group' ? overrideForm.scope_id : overrideForm.scope_id
    })
    ElMessage.success('覆盖策略已创建并绑定，目标终端将在下一个心跳周期生效')
    overrideDialogVisible.value = false
    loadOverridePolicies()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '创建失败')
  } finally {
    overrideSaving.value = false
  }
}

const toggleOverride = async row => {
  row._toggling = true
  try {
    await updateSecurityPolicy(row.id, { enabled: !row.enabled })
    ElMessage.success(row.enabled ? '已停用' : '已启用')
    loadOverridePolicies()
  } catch (e) {
    ElMessage.error('操作失败')
  } finally {
    row._toggling = false
  }
}

const removeOverride = async row => {
  try {
    await ElMessageBox.confirm(`确定删除覆盖策略"${row.policy_name}"？`, '删除', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await deleteSecurityPolicy(row.id)
    ElMessage.success('已删除')
    loadOverridePolicies()
  } catch (e) {
    ElMessage.error('删除失败')
  }
}

const openBindDialog = async row => {
  bindTarget.value = row
  bindDialogVisible.value = true
  bindLoading.value = true
  await Promise.all([loadScopeOptions(), loadBindRows()])
  bindLoading.value = false
}

const loadBindRows = async () => {
  try {
    const detail = await getSecurityPolicyDetail(bindTarget.value.id)
    bindRows.value = detail?.bindings || []
  } catch (e) {
    bindRows.value = []
  }
}

const addBinding = async () => {
  if (!bindForm.scope_id) {
    ElMessage.warning('请选择绑定目标')
    return
  }
  bindSaving.value = true
  try {
    await bindSecurityPolicy(bindTarget.value.id, {
      scope_type: bindForm.scope_type,
      scope_id: bindForm.scope_type === 'group' ? bindForm.scope_id : bindForm.scope_id
    })
    ElMessage.success('绑定成功')
    bindForm.scope_id = null
    await loadBindRows()
    loadOverridePolicies()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '绑定失败')
  } finally {
    bindSaving.value = false
  }
}

const removeBinding = async row => {
  try {
    await unbindSecurityPolicy(bindTarget.value.id, row.id)
    ElMessage.success('已解绑')
    await loadBindRows()
    loadOverridePolicies()
  } catch (e) {
    ElMessage.error('解绑失败')
  }
}

onMounted(() => {
  loadPolicies()
  loadOverridePolicies()
})
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

.zv-override-card { grid-column: 1 / -1; }

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
