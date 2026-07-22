<template>
  <el-container class="layout-container">
    <el-aside width="200px">
      <div class="logo">
        <img src="/zview-logo.png" alt="Z-View" class="logo-image" />
        <h2>Z-View</h2>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#304156"
        text-color="#fff"
        active-text-color="#409EFF"
      >
        <el-menu-item index="/dashboard">
          <el-icon><Monitor /></el-icon>
          <span>仪表板</span>
        </el-menu-item>

        <el-menu-item index="/terminal/overview">
          <el-icon><Monitor /></el-icon>
          <span>终端概览</span>
        </el-menu-item>

        <el-menu-item index="/asset/group">
          <el-icon><Box /></el-icon>
          <span>分组管理</span>
        </el-menu-item>

        <el-menu-item index="/terminal/software-center">
          <el-icon><Document /></el-icon>
          <span>软件管理</span>
        </el-menu-item>

        <el-menu-item index="/alert">
          <el-icon><Bell /></el-icon>
          <span>告警中心</span>
        </el-menu-item>

        <el-menu-item index="/log">
          <el-icon><Tickets /></el-icon>
          <span>日志中心</span>
        </el-menu-item>

        <el-menu-item index="/automation">
          <el-icon><Operation /></el-icon>
          <span>批量操作</span>
        </el-menu-item>

        <el-menu-item index="/discovery">
          <el-icon><Search /></el-icon>
          <span>资产发现</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header>
        <div class="header-content">
          <span class="title">Z-View 企业终端运维平台</span>
          <div class="user-section">
            <el-dropdown trigger="click" @command="handleUserCommand">
              <span class="user-info">
                <el-icon><User /></el-icon>
                {{ displayUsername }}
              </span>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="profile">个人中心</el-dropdown-item>
                  <el-dropdown-item divided command="logout">退出登录</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
      </el-header>

      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <el-dialog
    v-model="profileDialogVisible"
    :title="forcePasswordChangeMode ? '首次登录，请先修改密码' : '个人中心'"
    width="560px"
    destroy-on-close
    :show-close="!forcePasswordChangeMode"
    :close-on-click-modal="false"
    :close-on-press-escape="false"
    :before-close="handleProfileDialogBeforeClose"
  >
    <div v-loading="profileLoading" class="profile-panel">
      <el-alert
        v-if="forcePasswordChangeMode"
        title="当前账号仍需完成首次改密。修改成功前无法继续使用系统。"
        type="warning"
        :closable="false"
        class="force-password-alert"
      />

      <div class="profile-meta">
        <div class="meta-row">
          <span class="meta-label">当前账号</span>
          <span class="meta-value">{{ profile.username || displayUsername }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">凭据来源</span>
          <span class="meta-value">{{ credentialSourceText }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">首次登录状态</span>
          <span class="meta-value">
            <el-tag :type="profile.must_change_password ? 'danger' : 'success'" size="small">
              {{ profile.must_change_password ? '需立即改密' : '已完成改密' }}
            </el-tag>
          </span>
        </div>
        <div class="meta-row">
          <span class="meta-label">最近改密</span>
          <span class="meta-value">{{ passwordUpdatedAtText }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">本次登录时间</span>
          <span class="meta-value">{{ sessionIssuedAtText }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">会话到期时间</span>
          <span class="meta-value">{{ sessionExpiresAtText }}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">剩余会话时长</span>
          <span class="meta-value">
            <el-tag :type="sessionRemainingTagType" size="small">{{ sessionRemainingText }}</el-tag>
          </span>
        </div>
      </div>

      <el-divider>修改密码</el-divider>

      <el-form
        ref="passwordFormRef"
        :model="passwordForm"
        :rules="passwordRules"
        label-position="top"
      >
        <el-form-item label="当前密码" prop="current_password">
          <el-input
            v-model="passwordForm.current_password"
            type="password"
            show-password
            placeholder="请输入当前密码"
          />
        </el-form-item>
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="passwordForm.new_password"
            type="password"
            show-password
            placeholder="至少 8 位，需包含字母、数字，并满足更强复杂度"
          />
          <div class="password-strength">
            <div class="strength-header">
              <span>密码强度</span>
              <span :class="['strength-label', `strength-${passwordStrength.tone}`]">
                {{ passwordStrength.label }}
              </span>
            </div>
            <div class="strength-bar">
              <span :style="{ width: `${passwordStrength.percentage}%` }" :class="`strength-${passwordStrength.tone}`" />
            </div>
            <ul class="strength-checklist">
              <li
                v-for="item in passwordStrength.requirements"
                :key="item.text"
                :class="{ passed: item.passed }"
              >
                {{ item.text }}
              </li>
            </ul>
          </div>
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input
            v-model="passwordForm.confirm_password"
            type="password"
            show-password
            placeholder="请再次输入新密码"
            @keyup.enter="submitPasswordChange"
          />
        </el-form-item>
      </el-form>
    </div>

    <template #footer>
      <span class="dialog-footer">
        <el-button @click="handleProfileDialogCancel">
          {{ forcePasswordChangeMode ? '退出登录' : '取消' }}
        </el-button>
        <el-button type="primary" :loading="passwordSubmitting" @click="submitPasswordChange">
          {{ forcePasswordChangeMode ? '修改密码并重新登录' : '保存并重新登录' }}
        </el-button>
      </span>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Monitor, Box, Document, Search, User, Bell, Operation, Tickets } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import {
  changePassword,
  clearAuthSession,
  evaluatePasswordStrength,
  getAuthSessionMeta,
  getAuthSessionRemainingSeconds,
  getCurrentUser,
  getStoredAuthUsername,
  handleAuthExpired,
  isPasswordChangeRequired,
  updateAuthSessionMeta
} from '@/api/auth'

const router = useRouter()
const sessionNow = ref(Date.now())
const profileDialogVisible = ref(false)
const profileLoading = ref(false)
const passwordFormRef = ref(null)
const passwordSubmitting = ref(false)
const forcePasswordChangeMode = ref(false)
const profile = reactive({
  username: '',
  credential_source: 'default',
  password_updated_at: null,
  issued_at: null,
  expires_at: null,
  must_change_password: false
})
const displayUsername = computed(() => profile.username || getStoredAuthUsername() || '管理员')
const passwordForm = reactive({
  current_password: '',
  new_password: '',
  confirm_password: ''
})
let sessionTimer = null

const formatUnixSeconds = (value, fallback = '未记录') => {
  const timestamp = Number(value)
  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    return fallback
  }
  return dayjs.unix(timestamp).format('YYYY-MM-DD HH:mm:ss')
}

const syncProfileFromSession = (session = {}) => {
  const meta = session || {}
  profile.username = meta.username || getStoredAuthUsername() || '管理员'
  profile.credential_source = meta.credential_source || 'default'
  profile.password_updated_at = meta.password_updated_at || null
  profile.issued_at = meta.issued_at || null
  profile.expires_at = meta.expires_at || null
  profile.must_change_password = Boolean(meta.must_change_password)
}

const credentialSourceText = computed(() =>
  profile.credential_source === 'file' ? '本地已修改凭据' : '默认凭据'
)

const validateConfirmPassword = (_rule, value, callback) => {
  if (!value) {
    callback(new Error('请再次输入新密码'))
    return
  }
  if (value !== passwordForm.new_password) {
    callback(new Error('两次输入的新密码不一致'))
    return
  }
  callback()
}

const validatePasswordStrength = (_rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入新密码'))
    return
  }

  const result = evaluatePasswordStrength(value, profile.username || displayUsername.value)
  if (!result.passed) {
    callback(new Error(result.message))
    return
  }
  callback()
}

const passwordRules = {
  current_password: [{ required: true, message: '请输入当前密码', trigger: 'blur' }],
  new_password: [
    { validator: validatePasswordStrength, trigger: 'blur' }
  ],
  confirm_password: [{ validator: validateConfirmPassword, trigger: 'blur' }]
}

const passwordUpdatedAtText = computed(() => formatUnixSeconds(profile.password_updated_at, '尚未修改'))
const sessionIssuedAtText = computed(() => formatUnixSeconds(profile.issued_at))
const sessionExpiresAtText = computed(() => formatUnixSeconds(profile.expires_at, '未设置'))
const sessionRemainingSeconds = computed(() => getAuthSessionRemainingSeconds(sessionNow.value))
const sessionRemainingText = computed(() => {
  if (sessionRemainingSeconds.value === null) {
    return '未限制'
  }
  if (sessionRemainingSeconds.value <= 0) {
    return '已过期'
  }
  const hours = Math.floor(sessionRemainingSeconds.value / 3600)
  const minutes = Math.floor((sessionRemainingSeconds.value % 3600) / 60)
  const seconds = sessionRemainingSeconds.value % 60
  if (hours > 0) {
    return `${hours}时 ${minutes}分 ${seconds}秒`
  }
  if (minutes > 0) {
    return `${minutes}分 ${seconds}秒`
  }
  return `${seconds}秒`
})
const sessionRemainingTagType = computed(() => {
  if (sessionRemainingSeconds.value === null) {
    return 'info'
  }
  if (sessionRemainingSeconds.value <= 300) {
    return 'danger'
  }
  if (sessionRemainingSeconds.value <= 1800) {
    return 'warning'
  }
  return 'success'
})
const passwordStrength = computed(() =>
  evaluatePasswordStrength(passwordForm.new_password, profile.username || displayUsername.value)
)

const resetPasswordForm = () => {
  passwordForm.current_password = ''
  passwordForm.new_password = ''
  passwordForm.confirm_password = ''
  passwordFormRef.value?.clearValidate()
}

const loadCurrentProfile = async () => {
  profileLoading.value = true
  try {
    const currentUser = await getCurrentUser()
    syncProfileFromSession(currentUser)
    updateAuthSessionMeta(currentUser)
    if (currentUser?.must_change_password) {
      forcePasswordChangeMode.value = true
    }
  } catch (error) {
    console.error('加载个人中心失败:', error)
    ElMessage.warning('个人资料加载失败，已显示本地账号信息')
  } finally {
    profileLoading.value = false
  }
}

const openProfileDialog = async ({ force = false } = {}) => {
  forcePasswordChangeMode.value = force || isPasswordChangeRequired()
  profileDialogVisible.value = true
  resetPasswordForm()
  syncProfileFromSession(getAuthSessionMeta())
  await loadCurrentProfile()
}

const submitPasswordChange = async () => {
  if (!passwordFormRef.value) {
    return
  }

  const valid = await passwordFormRef.value.validate().catch(() => false)
  if (!valid) {
    return
  }

  passwordSubmitting.value = true
  try {
    await changePassword({
      current_password: passwordForm.current_password,
      new_password: passwordForm.new_password
    })
    ElMessage.success('密码修改成功，请重新登录')
    forcePasswordChangeMode.value = false
    profileDialogVisible.value = false
    clearAuthSession()
    await router.replace('/login?reason=password_changed')
  } catch (error) {
    console.error('修改密码失败:', error)
  } finally {
    passwordSubmitting.value = false
  }
}

const handleLogout = async () => {
  clearAuthSession()
  ElMessage.success('退出成功')
  await router.replace('/login')
}

const handleProfileDialogBeforeClose = (done) => {
  if (forcePasswordChangeMode.value) {
    ElMessage.warning('首次登录必须先修改密码')
    return
  }
  done()
}

const handleProfileDialogCancel = async () => {
  if (forcePasswordChangeMode.value) {
    await handleLogout()
    return
  }
  profileDialogVisible.value = false
}

const startSessionWatcher = () => {
  sessionTimer = window.setInterval(() => {
    sessionNow.value = Date.now()
    if (sessionRemainingSeconds.value !== null && sessionRemainingSeconds.value <= 0) {
      handleAuthExpired('expired')
    }
  }, 1000)
}

const handleUserCommand = async (command) => {
  if (command === 'logout') {
    await handleLogout()
    return
  }

  if (command === 'profile') {
    await openProfileDialog()
  }
}

onMounted(async () => {
  syncProfileFromSession(getAuthSessionMeta())
  startSessionWatcher()

  if (isPasswordChangeRequired()) {
    await openProfileDialog({ force: true })
  }
})

onBeforeUnmount(() => {
  if (sessionTimer) {
    window.clearInterval(sessionTimer)
    sessionTimer = null
  }
})
</script>

<style lang="scss" scoped>
.layout-container {
  height: 100vh;
  overflow: hidden;
}

.el-aside {
  background: #304156;
  color: #fff;
  overflow-y: auto;

  .logo {
    height: 60px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    background: #1f2d3d;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);

    .logo-image {
      width: 28px;
      height: 28px;
      object-fit: contain;
      flex-shrink: 0;
    }

    h2 {
      color: #fff;
      font-size: 20px;
      font-weight: 600;
    }
  }

  .el-menu {
    border-right: none;
  }
}

.el-header {
  background: #fff;
  box-shadow: 0 1px 4px rgba(0, 21, 41, 0.08);
  display: flex;
  align-items: center;
  padding: 0 20px;

  .header-content {
    width: 100%;
    display: flex;
    justify-content: space-between;
    align-items: center;

    .title {
      font-size: 16px;
      font-weight: 500;
      color: #333;
    }

    .user-section {
      .user-info {
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 5px;
        padding: 5px 10px;
        border-radius: 4px;
        transition: background 0.3s;

        &:hover {
          background: #f0f2f5;
        }
      }
    }
  }
}

.profile-panel {
  .force-password-alert {
    margin-bottom: 14px;
  }

  .profile-meta {
    display: grid;
    gap: 10px;
    margin-bottom: 8px;
    padding: 14px 16px;
    border-radius: 10px;
    background: #f6f8fb;
    border: 1px solid #e7edf5;
  }

  .meta-row {
    display: flex;
    justify-content: space-between;
    gap: 16px;
  }

  .meta-label {
    color: #6b7280;
  }

  .meta-value {
    color: #1f2937;
    font-weight: 500;
    text-align: right;
  }

  .password-strength {
    width: 100%;
    margin-top: 10px;
  }

  .strength-header {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: #64748b;
  }

  .strength-label {
    font-weight: 600;
  }

  .strength-bar {
    height: 6px;
    background: #e5e7eb;
    border-radius: 999px;
    overflow: hidden;
    margin: 8px 0 10px;

    span {
      display: block;
      height: 100%;
      border-radius: 999px;
      transition: width 0.2s ease;
    }
  }

  .strength-weak {
    color: #dc2626;
    background: #dc2626;
  }

  .strength-medium {
    color: #d97706;
    background: #d97706;
  }

  .strength-strong {
    color: #16a34a;
    background: #16a34a;
  }

  .strength-checklist {
    margin: 0;
    padding-left: 18px;
    color: #94a3b8;
    font-size: 12px;
    line-height: 1.6;

    li.passed {
      color: #16a34a;
    }
  }
}

.el-main {
  background: #f0f2f5;
  overflow-y: auto;
  padding: 0;
}
</style>
