<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">用户管理</h2>
        <div class="zv-page-subtitle">平台账号与角色权限管理 · 仅系统管理员可见</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadUsers" :loading="loading">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">添加用户</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="zv-role-tip"
      title="角色权限说明"
      description="管理员：全部权限（含用户管理）· 运维员：终端/软件/告警/安全等运维操作 · 只读：仅查看。角色变更与停用会立即吊销该用户已登录的令牌。" />

    <div class="zv-card">
      <el-table v-loading="loading" :data="users">
        <el-table-column prop="username" label="用户名" min-width="140">
          <template #default="{ row }">
            <div class="zv-user-cell">
              <span class="zv-user-name">{{ row.username }}</span>
              <el-tag v-if="row.username === currentUsername" size="small" effect="plain">当前账号</el-tag>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="角色" width="140">
          <template #default="{ row }">
            <el-select
              :model-value="row.role"
              size="small"
              :disabled="row.username === currentUsername"
              @change="(role) => handleRoleChange(row, role)"
            >
              <el-option label="管理员" value="admin" />
              <el-option label="运维员" value="operator" />
              <el-option label="只读" value="viewer" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-switch
              :model-value="row.enabled"
              :disabled="row.username === currentUsername"
              @change="(enabled) => handleEnabledChange(row, enabled)"
            />
          </template>
        </el-table-column>
        <el-table-column label="首次登录改密" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.must_change_password" type="warning" size="small">待修改</el-tag>
            <span v-else class="zv-mono">-</span>
          </template>
        </el-table-column>
        <el-table-column label="密码更新时间" width="170">
          <template #default="{ row }">
            <span class="zv-mono">{{ formatTime(row.password_updated_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">
            <span class="zv-mono">{{ formatTime(row.created_at) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="170" align="right" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="openResetDialog(row)">重置密码</el-button>
            <el-button
              text type="danger" size="small"
              :disabled="row.username === currentUsername"
              @click="handleDelete(row)"
            >删除</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无用户" :image-size="80" /></template>
      </el-table>
    </div>

    <!-- 添加用户 -->
    <el-dialog v-model="createVisible" title="添加用户" width="480px" destroy-on-close>
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" placeholder="字母/数字/._- ，2-32 位" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input v-model="createForm.password" type="password" show-password placeholder="至少 8 位，含字母和数字" />
        </el-form-item>
        <el-form-item label="角色" prop="role">
          <el-select v-model="createForm.role" style="width: 100%">
            <el-option label="管理员（全部权限）" value="admin" />
            <el-option label="运维员（运维操作）" value="operator" />
            <el-option label="只读（仅查看）" value="viewer" />
          </el-select>
        </el-form-item>
        <el-alert type="warning" :closable="false" show-icon
          title="新用户首次登录将被要求修改初始密码" />
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码 -->
    <el-dialog v-model="resetVisible" title="重置密码" width="460px" destroy-on-close>
      <el-form ref="resetFormRef" :model="resetForm" :rules="resetRules" label-width="90px">
        <el-form-item label="用户名">
          <span class="zv-mono">{{ resetTarget?.username }}</span>
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input v-model="resetForm.new_password" type="password" show-password placeholder="至少 8 位，含字母和数字" />
        </el-form-item>
        <el-alert type="warning" :closable="false" show-icon
          title="重置后该用户已登录的令牌将全部失效，需要重新登录" />
      </el-form>
      <template #footer>
        <el-button @click="resetVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleReset">重置</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import {
  listUsers, createUser, updateUser, resetUserPassword, deleteUser
} from '@/api/auth'
import { getStoredAuthUsername } from '@/api/auth-session'

const loading = ref(false)
const submitting = ref(false)
const users = ref([])
const currentUsername = getStoredAuthUsername() || ''

const createVisible = ref(false)
const createFormRef = ref(null)
const createForm = reactive({ username: '', password: '', role: 'operator' })
const createRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { pattern: /^[A-Za-z0-9_.\-]{2,32}$/, message: '字母/数字/._- ，2-32 位', trigger: 'blur' }
  ],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 8, message: '至少 8 位', trigger: 'blur' }
  ],
  role: [{ required: true, message: '请选择角色', trigger: 'change' }]
}

const resetVisible = ref(false)
const resetFormRef = ref(null)
const resetTarget = ref(null)
const resetForm = reactive({ new_password: '' })
const resetRules = {
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 8, message: '至少 8 位', trigger: 'blur' }
  ]
}

const loadUsers = async () => {
  loading.value = true
  try {
    const res = await listUsers()
    users.value = res.users || []
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

const openCreateDialog = () => {
  createForm.username = ''
  createForm.password = ''
  createForm.role = 'operator'
  createVisible.value = true
}

const handleCreate = async () => {
  await createFormRef.value?.validate().catch(() => Promise.reject())
  submitting.value = true
  try {
    await createUser({ ...createForm })
    ElMessage.success(`用户 ${createForm.username} 创建成功`)
    createVisible.value = false
    loadUsers()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    submitting.value = false
  }
}

const handleRoleChange = async (row, role) => {
  try {
    await ElMessageBox.confirm(
      `确定将用户「${row.username}」的角色变更为「${{ admin: '管理员', operator: '运维员', viewer: '只读' }[role]}」吗？其现有登录将失效。`,
      '变更角色', { type: 'warning' }
    )
    await updateUser(row.username, { role })
    ElMessage.success('角色已变更，该用户令牌已吊销')
    loadUsers()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('角色变更失败')
    loadUsers()
  }
}

const handleEnabledChange = async (row, enabled) => {
  try {
    await ElMessageBox.confirm(
      `确定${enabled ? '启用' : '停用'}用户「${row.username}」吗？${enabled ? '' : '停用后该用户将立即无法访问平台。'}`,
      '账号状态', { type: 'warning' }
    )
    await updateUser(row.username, { enabled })
    ElMessage.success(enabled ? '用户已启用' : '用户已停用')
    loadUsers()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('操作失败')
    loadUsers()
  }
}

const openResetDialog = (row) => {
  resetTarget.value = row
  resetForm.new_password = ''
  resetVisible.value = true
}

const handleReset = async () => {
  await resetFormRef.value?.validate().catch(() => Promise.reject())
  submitting.value = true
  try {
    await resetUserPassword(resetTarget.value.username, { new_password: resetForm.new_password })
    ElMessage.success(`用户 ${resetTarget.value.username} 的密码已重置`)
    resetVisible.value = false
    loadUsers()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    submitting.value = false
  }
}

const handleDelete = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定删除用户「${row.username}」吗？删除后无法恢复。`,
      '删除用户', { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' }
    )
    await deleteUser(row.username)
    ElMessage.success('用户已删除')
    loadUsers()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

const formatTime = (v) => {
  if (!v) return '-'
  const n = Number(v)
  return n ? dayjs.unix(n).format('YYYY-MM-DD HH:mm:ss') : String(v)
}

onMounted(loadUsers)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1300px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }
.zv-role-tip { margin-bottom: 20px; }

.zv-user-cell { display: flex; align-items: center; gap: 8px; }
.zv-user-name { font-weight: 600; color: $text-primary; }
.zv-mono { font-family: $font-mono; font-size: 12px; color: $text-secondary; }

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell { background: #fafbfc; color: $text-secondary; font-weight: 600; font-size: 12px; }
}
</style>
