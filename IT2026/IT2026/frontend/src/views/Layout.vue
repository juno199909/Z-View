<template>
  <div class="zv-layout">
    <!-- 侧边栏 -->
    <aside class="zv-sidebar" :class="{ 'is-collapsed': isCollapsed }">
      <div class="zv-sidebar-brand">
        <div class="zv-brand-icon">
          <el-icon :size="22"><Monitor /></el-icon>
        </div>
        <transition name="el-fade-in">
          <div v-show="!isCollapsed" class="zv-brand-text">
            <div class="zv-brand-name">Z-View</div>
            <div class="zv-brand-sub">终端运维平台</div>
          </div>
        </transition>
      </div>

      <el-scrollbar class="zv-sidebar-scroll">
        <el-menu
          :default-active="$route.path"
          :collapse="isCollapsed"
          :collapse-transition="false"
          router
          background-color="transparent"
          text-color="rgba(255,255,255,0.7)"
          active-text-color="#ffffff"
          class="zv-sidebar-menu"
        >
          <el-menu-item index="/dashboard" class="zv-menu-item">
            <el-icon><Odometer /></el-icon>
            <template #title>
              <span class="zv-menu-label">仪表板</span>
            </template>
          </el-menu-item>

          <el-sub-menu index="/terminal-menu" class="zv-submenu">
            <template #title>
              <el-icon><Monitor /></el-icon>
              <span class="zv-menu-label">终端管理</span>
            </template>
            <el-menu-item index="/terminal/overview" class="zv-menu-item">终端概览</el-menu-item>
            <el-menu-item index="/asset/list" class="zv-menu-item">终端列表</el-menu-item>
            <el-menu-item index="/asset/group" class="zv-menu-item">终端分组</el-menu-item>
            <el-menu-item index="/discovery" class="zv-menu-item">终端发现</el-menu-item>
            <el-menu-item index="/terminal/software-center" class="zv-menu-item">软件管理</el-menu-item>
            <el-menu-item index="/terminal/agent-upgrade" class="zv-menu-item">Agent升级</el-menu-item>
            <el-menu-item index="/automation" class="zv-menu-item">批量操作</el-menu-item>
            <el-menu-item index="/settings/agent-policy" class="zv-menu-item">终端策略</el-menu-item>
          </el-sub-menu>

          <el-sub-menu index="/monitor-menu" class="zv-submenu">
            <template #title>
              <el-icon><Bell /></el-icon>
              <span class="zv-menu-label">监控中心</span>
            </template>
            <el-menu-item index="/alert" class="zv-menu-item">
              <template #title>
                终端日志
                <el-badge v-if="alertBadge > 0" :value="alertBadge" :max="99" class="zv-menu-badge" />
              </template>
            </el-menu-item>
            <el-menu-item index="/log" class="zv-menu-item">日志总览</el-menu-item>
            <el-menu-item index="/log/operations" class="zv-menu-item">操作日志</el-menu-item>
          </el-sub-menu>

          <el-sub-menu index="/security" class="zv-submenu">
            <template #title>
              <el-icon><Lock /></el-icon>
              <span class="zv-menu-label">安全管理</span>
            </template>
            <el-menu-item index="/security/overview" class="zv-menu-item">安全总览</el-menu-item>
            <el-menu-item index="/security/terminals" class="zv-menu-item">终端安全</el-menu-item>
            <el-menu-item index="/security/firewall" class="zv-menu-item">防火墙</el-menu-item>
            <el-menu-item index="/security/usb" class="zv-menu-item">USB管控</el-menu-item>
            <el-menu-item index="/security/app-control" class="zv-menu-item">程序管控</el-menu-item>
            <el-menu-item index="/security/file-protect" class="zv-menu-item">文件保护</el-menu-item>
            <el-menu-item index="/security/behavior" class="zv-menu-item">行为监控</el-menu-item>
            <el-menu-item index="/security/events" class="zv-menu-item">安全事件</el-menu-item>
            <el-menu-item index="/security/policies" class="zv-menu-item">策略中心</el-menu-item>
          </el-sub-menu>
        </el-menu>
      </el-scrollbar>

      <div class="zv-sidebar-footer" @click="toggleSidebar">
        <el-icon :size="16">
          <Fold v-if="!isCollapsed" />
          <Expand v-else />
        </el-icon>
        <span v-show="!isCollapsed">收起菜单</span>
      </div>
    </aside>

    <!-- 右侧主区 -->
    <div class="zv-main">
      <!-- 顶部栏 -->
      <header class="zv-header">
        <div class="zv-header-left">
          <div class="zv-breadcrumb">
            <el-breadcrumb separator="›">
              <el-breadcrumb-item v-for="(item, idx) in breadcrumb" :key="idx">
                <el-icon v-if="item.icon" :size="14" class="zv-breadcrumb-icon"><component :is="item.icon" /></el-icon>
                <span :class="{ 'is-current': idx === breadcrumb.length - 1 }">{{ item.title }}</span>
              </el-breadcrumb-item>
            </el-breadcrumb>
          </div>
        </div>

        <div class="zv-header-right">
          <el-tooltip content="刷新" placement="bottom">
            <div class="zv-header-icon" @click="refreshPage">
              <el-icon :size="18"><Refresh /></el-icon>
            </div>
          </el-tooltip>

          <el-tooltip content="全屏" placement="bottom">
            <div class="zv-header-icon" @click="toggleFullscreen">
              <el-icon :size="18"><FullScreen /></el-icon>
            </div>
          </el-tooltip>

          <el-tooltip content="帮助" placement="bottom">
            <div class="zv-header-icon">
              <el-icon :size="18"><QuestionFilled /></el-icon>
            </div>
          </el-tooltip>

          <div class="zv-header-divider" />

          <el-dropdown trigger="click" @command="handleUserCommand">
            <div class="zv-user-chip">
              <div class="zv-avatar">{{ avatarText }}</div>
              <div class="zv-user-info">
                <div class="zv-user-name">{{ displayUsername }}</div>
                <div class="zv-user-role">{{ userRole }}</div>
              </div>
              <el-icon :size="14" class="zv-user-arrow"><CaretBottom /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>
                  <span>个人中心</span>
                </el-dropdown-item>
                <el-dropdown-item command="logout" divided>
                  <el-icon><SwitchButton /></el-icon>
                  <span>退出登录</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <!-- 内容区 -->
      <main class="zv-content">
        <router-view v-slot="{ Component, route }">
          <transition name="zv-fade" mode="out-in">
            <component :is="Component" :key="route.fullPath" />
          </transition>
        </router-view>
      </main>
    </div>

    <!-- 个人中心对话框（保持兼容）-->
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
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { getAlertStats } from '@/api/alert'
import {
  Monitor, Bell,
  User, Odometer,
  CaretBottom, SwitchButton, Refresh, FullScreen, QuestionFilled,
  Fold, Expand, Lock
} from '@element-plus/icons-vue'
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
const route = useRoute()

const isCollapsed = ref(false)
const sessionNow = ref(Date.now())
const profileDialogVisible = ref(false)
const profileLoading = ref(false)
const passwordFormRef = ref(null)
const passwordSubmitting = ref(false)
const forcePasswordChangeMode = ref(false)
const alertBadge = ref(0)

const profile = reactive({
  username: '',
  credential_source: 'default',
  password_updated_at: null,
  issued_at: null,
  expires_at: null,
  must_change_password: false
})
const passwordForm = reactive({
  current_password: '',
  new_password: '',
  confirm_password: ''
})
let sessionTimer = null

const displayUsername = computed(() => profile.username || getStoredAuthUsername() || '管理员')
const avatarText = computed(() => {
  const name = displayUsername.value || 'A'
  return name.substring(0, 1).toUpperCase()
})
const userRole = computed(() => {
  return profile.username ? '系统管理员' : '访客'
})

const titleMap = {
  '/dashboard': { title: '仪表板', icon: 'Odometer' },
  '/asset/list': { title: '终端列表', icon: 'Box' },
  '/asset/group': { title: '终端分组', icon: 'Files' },
  '/asset/detail': { title: '终端详情', icon: 'Box' },
  '/asset/create': { title: '新增终端', icon: 'Box' },
  '/alert': { title: '终端日志', icon: 'Bell' },
  '/log': { title: '日志总览', icon: 'Document' },
  '/log/operations': { title: '操作日志', icon: 'Document' },
  '/automation': { title: '批量操作', icon: 'Operation' },
  '/terminal/overview': { title: '终端概览', icon: 'Monitor' },
  '/terminal/detail': { title: '终端详情', icon: 'Monitor' },
  '/terminal/software-center': { title: '软件管理', icon: 'Goods' },
  '/terminal/agent-upgrade': { title: 'Agent升级', icon: 'Goods' },
  '/discovery': { title: '终端发现', icon: 'Search' },
  '/settings/agent-policy': { title: '终端策略', icon: 'Setting' },
  '/security/overview': { title: '安全总览', icon: 'Lock' },
  '/security/terminals': { title: '终端安全', icon: 'Lock' },
  '/security/firewall': { title: '防火墙', icon: 'Lock' },
  '/security/usb': { title: 'USB管控', icon: 'Lock' },
  '/security/app-control': { title: '程序管控', icon: 'Lock' },
  '/security/file-protect': { title: '文件保护', icon: 'Lock' },
  '/security/behavior': { title: '行为监控', icon: 'Lock' },
  '/security/events': { title: '安全事件', icon: 'Lock' },
  '/security/policies': { title: '策略中心', icon: 'Lock' }
}

const breadcrumb = computed(() => {
  const items = [{ title: 'Z-View', icon: 'Monitor' }]
  // 精确匹配 → 前缀匹配（支持 /asset/detail/3 这类带参数路径）
  const current = titleMap[route.path]
    || titleMap['/' + route.path.split('/').filter(Boolean).slice(0, 2).join('/')]
  if (current) items.push(current)
  return items
})

const toggleSidebar = () => {
  isCollapsed.value = !isCollapsed.value
}

const refreshPage = () => {
  window.location.reload()
}

const toggleFullscreen = () => {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen?.()
  } else {
    document.exitFullscreen?.()
  }
}

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
  new_password: [{ validator: validatePasswordStrength, trigger: 'blur' }],
  confirm_password: [{ validator: validateConfirmPassword, trigger: 'blur' }]
}

const passwordUpdatedAtText = computed(() => formatUnixSeconds(profile.password_updated_at, '尚未修改'))
const sessionIssuedAtText = computed(() => formatUnixSeconds(profile.issued_at))
const sessionExpiresAtText = computed(() => formatUnixSeconds(profile.expires_at, '未设置'))
const sessionRemainingSeconds = computed(() => getAuthSessionRemainingSeconds(sessionNow.value))
const sessionRemainingText = computed(() => {
  if (sessionRemainingSeconds.value === null) return '未限制'
  if (sessionRemainingSeconds.value <= 0) return '已过期'
  const hours = Math.floor(sessionRemainingSeconds.value / 3600)
  const minutes = Math.floor((sessionRemainingSeconds.value % 3600) / 60)
  if (hours > 0) return `${hours}时 ${minutes}分`
  return `${minutes}分`
})
const sessionRemainingTagType = computed(() => {
  if (sessionRemainingSeconds.value === null) return 'info'
  if (sessionRemainingSeconds.value <= 300) return 'danger'
  if (sessionRemainingSeconds.value <= 1800) return 'warning'
  return 'success'
})

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
  if (!passwordFormRef.value) return
  const valid = await passwordFormRef.value.validate().catch(() => false)
  if (!valid) return
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

  // 加载未解决告警数，驱动“终端日志”菜单角标
  try {
    const alertStats = await getAlertStats()
    alertBadge.value = Number(alertStats?.active ?? alertStats?.unresolved ?? 0) || 0
  } catch {}

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
@use '@/assets/styles/variables.scss' as *;

.zv-layout {
  display: flex;
  height: 100vh;
  background: $bg-body;
  overflow: hidden;
}

// ---- 侧边栏 ----
.zv-sidebar {
  width: $sidebar-width;
  background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
  color: #fff;
  display: flex;
  flex-direction: column;
  transition: width $transition-slow;
  flex-shrink: 0;
  position: relative;
  z-index: 10;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.06);

  &.is-collapsed {
    width: $sidebar-width-collapsed;
  }
}

.zv-sidebar-brand {
  height: $header-height;
  display: flex;
  align-items: center;
  padding: 0 20px;
  gap: 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  flex-shrink: 0;

  .zv-brand-icon {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    color: #fff;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.35);
    flex-shrink: 0;
  }

  .zv-brand-text {
    flex: 1;
    overflow: hidden;
  }

  .zv-brand-name {
    font-size: 18px;
    font-weight: 700;
    color: #fff;
    line-height: 1.2;
    letter-spacing: 0.3px;
  }

  .zv-brand-sub {
    font-size: 11px;
    color: rgba(255, 255, 255, 0.5);
    margin-top: 2px;
  }
}

.zv-sidebar-scroll {
  flex: 1;
  overflow: hidden;
}

.zv-sidebar-menu {
  padding: 12px 8px;
  background: transparent !important;
  border-right: none !important;
}

.zv-menu-item {
  margin: 4px 0;
  border-radius: $border-radius;
  height: 44px;
  line-height: 44px;
  margin: 4px 8px;
  transition: all $transition-base;

  :deep(.el-menu-item) {
    background: transparent !important;
    color: rgba(255, 255, 255, 0.7) !important;
    border-radius: $border-radius;
    transition: all $transition-base;
    height: 44px;
    line-height: 44px;

    .el-icon {
      color: rgba(255, 255, 255, 0.55);
      transition: color $transition-base;
    }

    .zv-menu-label {
      font-size: 14px;
      font-weight: 500;
      margin-left: 8px;
    }

    &:hover {
      background: rgba(255, 255, 255, 0.04) !important;
      color: #fff !important;
      .el-icon { color: #fff; }
    }

    &.is-active {
      background: linear-gradient(90deg, rgba(37, 99, 235, 0.22), rgba(37, 99, 235, 0.08)) !important;
      color: #fff !important;
      font-weight: 600;

      &::before {
        content: '';
        position: absolute;
        left: 0;
        top: 8px;
        bottom: 8px;
        width: 3px;
        background: $brand-primary;
        border-radius: 0 2px 2px 0;
      }

      .el-icon { color: $brand-primary-light; }
    }
  }
}

.zv-menu-badge {
  margin-left: auto;
  :deep(.el-badge__content) {
    background: $danger-color;
    border: 2px solid #0f172a;
  }
}

.zv-sidebar-footer {
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: rgba(255, 255, 255, 0.5);
  font-size: 13px;
  cursor: pointer;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  transition: all $transition-base;
  flex-shrink: 0;

  &:hover {
    color: #fff;
    background: rgba(255, 255, 255, 0.04);
  }
}

// ---- 右侧主区 ----
.zv-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}

.zv-header {
  height: $header-height;
  background: $bg-elevated;
  border-bottom: 1px solid $border-color-light;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  box-shadow: $shadow-xs;
  flex-shrink: 0;
  z-index: 5;
  position: relative;
}

.zv-header-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.zv-breadcrumb {
  :deep(.el-breadcrumb__item) {
    .el-breadcrumb__inner {
      color: $text-tertiary;
      font-weight: 500;
      display: flex;
      align-items: center;
      gap: 4px;
    }
    .zv-breadcrumb-icon {
      color: $text-tertiary;
    }
    .is-current {
      color: $text-primary;
      font-weight: 600;
    }
  }
  :deep(.el-breadcrumb__separator) {
    color: $text-tertiary;
    margin: 0 4px;
  }
}

.zv-header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.zv-header-icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: $border-radius;
  color: $text-secondary;
  cursor: pointer;
  transition: all $transition-base;

  &:hover {
    background: $bg-hover;
    color: $brand-primary;
  }
}

.zv-header-divider {
  width: 1px;
  height: 24px;
  background: $border-color;
  margin: 0 8px;
}

.zv-user-chip {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 4px 4px;
  border-radius: $radius-pill;
  cursor: pointer;
  transition: all $transition-base;

  &:hover {
    background: $bg-hover;
  }

  .zv-avatar {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: linear-gradient(135deg, $brand-primary, $brand-primary-dark);
    color: #fff;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 600;
    font-size: 14px;
  }

  .zv-user-info {
    line-height: 1.2;
  }

  .zv-user-name {
    font-size: 13px;
    font-weight: 600;
    color: $text-primary;
  }

  .zv-user-role {
    font-size: 11px;
    color: $text-tertiary;
  }

  .zv-user-arrow {
    color: $text-tertiary;
  }
}

// ---- 内容区 ----
.zv-content {
  flex: 1;
  overflow-y: auto;
  background: $bg-body;
  min-width: 0;
  position: relative;
}

.zv-fade-enter-active,
.zv-fade-leave-active {
  transition: opacity $transition-base, transform $transition-base;
}

.zv-fade-enter-from {
  opacity: 0;
  transform: translateY(8px);
}

.zv-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

// ---- 兼容老样式 ----
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
    background: $slate-50;
    border: 1px solid $border-color-light;
  }
  .meta-row {
    display: flex;
    justify-content: space-between;
    gap: 16px;
  }
  .meta-label { color: $text-secondary; }
  .meta-value { color: $text-primary; font-weight: 500; text-align: right; }
}
</style>
