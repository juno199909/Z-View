<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">用户与权限</h2>
        <div class="zv-page-subtitle">账号角色与资产分组可见范围（Scoped RBAC）：受限用户仅能看到并操作所分配分组内的终端</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" :loading="loading" @click="loadUsers">刷新</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreate">新建用户</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="zv-tip" title="管理员不受范围限制；operator/viewer 未分配分组时不限制（兼容存量账号），分配分组后仅可见所选分组内的终端。范围变更立即生效。" />

    <el-table :data="users" stripe v-loading="loading">
      <el-table-column prop="username" label="用户名" min-width="140" />
      <el-table-column prop="role" label="角色" width="110">
        <template #default="{row}">
          <el-tag size="small" :type="row.role === 'admin' ? 'danger' : row.role === 'operator' ? 'warning' : 'info'">
            {{ row.role }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="资产范围" min-width="220">
        <template #default="{row}">
          <el-tag v-if="row.role === 'admin'" size="small" type="success">全部终端（管理员）</el-tag>
          <el-tag v-else-if="!row.scoped_group_ids || !row.scoped_group_ids.length" size="small">不限制</el-tag>
          <template v-else>
            <el-tag v-for="gid in row.scoped_group_ids" :key="gid" size="small" type="warning" style="margin-right:4px">
              {{ groupNameMap[gid] || `分组 #${gid}` }}
            </el-tag>
          </template>
        </template>
      </el-table-column>
      <el-table-column prop="enabled" label="状态" width="90">
        <template #default="{row}">
          <el-switch :model-value="row.enabled" @change="val => toggleEnabled(row, val)" :loading="row._toggling" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="280" fixed="right">
        <template #default="{row}">
          <el-button link type="primary" :disabled="row.role === 'admin'" @click="openScope(row)">设置范围</el-button>
          <el-button link type="warning" @click="doResetPassword(row)">重置密码</el-button>
          <el-button link type="danger" @click="doDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 新建用户 -->
    <el-dialog v-model="createVisible" title="新建用户" width="480px">
      <el-form label-width="100px">
        <el-form-item label="用户名" required>
          <el-input v-model="createForm.username" maxlength="32" placeholder="字母、数字、点、下划线、连字符" />
        </el-form-item>
        <el-form-item label="初始密码" required>
          <el-input v-model="createForm.password" type="password" show-password placeholder="至少 8 位，含字母和数字" />
        </el-form-item>
        <el-form-item label="角色" required>
          <el-radio-group v-model="createForm.role">
            <el-radio-button value="admin">admin</el-radio-button>
            <el-radio-button value="operator">operator</el-radio-button>
            <el-radio-button value="viewer">viewer</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="createForm.role !== 'admin'" label="资产范围">
          <el-select v-model="createForm.scoped_group_ids" multiple filterable placeholder="不选 = 不限制" style="width: 100%">
            <el-option v-for="g in groups" :key="g.id" :label="g.name || g.group_name" :value="g.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 设置范围 -->
    <el-dialog v-model="scopeVisible" title="设置资产分组范围" width="480px">
      <p style="margin: 0 0 10px; font-size: 13px;">用户：<b>{{ scopeTarget?.username }}</b>（{{ scopeTarget?.role }}）</p>
      <el-select v-model="scopeForm.group_ids" multiple filterable placeholder="不选 = 不限制" style="width: 100%">
        <el-option v-for="g in groups" :key="g.id" :label="g.name || g.group_name" :value="g.id" />
      </el-select>
      <div class="zv-scope-hint">该用户将只能看到并操作所选分组内的终端；清空列表 = 不限制。保存后该用户需重新获取会话（刷新页面）以加载新范围。</div>
      <template #footer>
        <el-button @click="scopeVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitScope">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { listUsers, createUser, updateUser, resetUserPassword, deleteUser } from '@/api/auth'
import { getGroups } from '@/api/group'

const loading = ref(false)
const saving = ref(false)
const users = ref([])
const groups = ref([])
const groupNameMap = ref({})

const createVisible = ref(false)
const createForm = reactive({ username: '', password: '', role: 'operator', scoped_group_ids: [] })
const scopeVisible = ref(false)
const scopeTarget = ref(null)
const scopeForm = reactive({ group_ids: [] })

const loadScopeOptions = async () => {
  try {
    const g = await getGroups()
    groups.value = Array.isArray(g?.data) ? g.data : (Array.isArray(g) ? g : [])
    groupNameMap.value = Object.fromEntries(groups.value.map(x => [x.id, x.name || x.group_name]))
  } catch (e) {
    console.error('加载分组失败:', e)
  }
}

const loadUsers = async () => {
  loading.value = true
  try {
    const res = await listUsers()
    const rows = res?.users || res?.data || []
    users.value = rows.map(u => ({ ...u, _toggling: false }))
  } catch (e) {
    ElMessage.error(e?.response?.status === 403 ? '需要管理员权限' : '加载用户失败')
  } finally {
    loading.value = false
  }
}

const openCreate = async () => {
  Object.assign(createForm, { username: '', password: '', role: 'operator', scoped_group_ids: [] })
  await loadScopeOptions()
  createVisible.value = true
}

const submitCreate = async () => {
  if (!createForm.username.trim() || !createForm.password) {
    ElMessage.warning('请填写用户名和密码')
    return
  }
  saving.value = true
  try {
    await createUser({
      username: createForm.username.trim(),
      password: createForm.password,
      role: createForm.role,
      scoped_group_ids: createForm.role === 'admin' ? [] : createForm.scoped_group_ids
    })
    ElMessage.success('用户创建成功')
    createVisible.value = false
    loadUsers()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
  }
}

const toggleEnabled = async (row, val) => {
  row._toggling = true
  try {
    await updateUser(row.username, { enabled: val })
    row.enabled = val
    ElMessage.success(val ? '已启用' : '已停用')
  } catch (e) {
    ElMessage.error('操作失败')
  } finally {
    row._toggling = false
  }
}

const openScope = async row => {
  scopeTarget.value = row
  scopeForm.group_ids = [...(row.scoped_group_ids || [])]
  await loadScopeOptions()
  scopeVisible.value = true
}

const submitScope = async () => {
  saving.value = true
  try {
    await updateUser(scopeTarget.value.username, { scoped_group_ids: scopeForm.group_ids })
    ElMessage.success('范围已更新并即时生效')
    scopeVisible.value = false
    loadUsers()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '保存失败')
  } finally {
    saving.value = false
  }
}

const doResetPassword = async row => {
  let password = ''
  try {
    const res = await ElMessageBox.prompt(`为用户 ${row.username} 设置新密码`, '重置密码', {
      inputType: 'password',
      inputPattern: /^.{8,}$/,
      inputErrorMessage: '至少 8 位',
      confirmButtonText: '重置',
    })
    password = res.value
  } catch (e) {
    return
  }
  try {
    await resetUserPassword(row.username, { new_password: password })
    ElMessage.success('密码已重置，该用户原令牌已全部失效')
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '重置失败')
  }
}

const doDelete = async row => {
  try {
    await ElMessageBox.confirm(`确定删除用户 ${row.username}？`, '删除用户', { type: 'warning' })
  } catch (e) {
    return
  }
  try {
    await deleteUser(row.username)
    ElMessage.success('已删除')
    loadUsers()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  }
}

onMounted(loadUsers)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1100px; margin: 0 auto; }
.zv-page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; gap: 12px; }
.zv-page-title { font-size: 20px; font-weight: 600; margin: 0; color: $text-primary; }
.zv-page-subtitle { font-size: 12px; color: $text-tertiary; margin-top: 4px; }
.zv-page-actions { display: flex; gap: 10px; }
.zv-tip { margin-bottom: 16px; }
.zv-scope-hint { margin-top: 10px; font-size: 12px; color: $text-tertiary; line-height: 1.6; }
</style>
