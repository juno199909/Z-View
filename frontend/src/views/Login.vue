<template>
  <div class="zv-login">
    <!-- 左侧品牌区 -->
    <div class="zv-login-brand">
      <div class="zv-brand-deco zv-deco-1" />
      <div class="zv-brand-deco zv-deco-2" />
      <div class="zv-brand-deco zv-deco-3" />

      <div class="zv-brand-content">
        <div class="zv-brand-icon">
          <el-icon :size="32"><Monitor /></el-icon>
        </div>
        <h1 class="zv-brand-name">Z-View</h1>
        <p class="zv-brand-tag">企业级终端运维平台</p>

        <ul class="zv-brand-features">
          <li>
            <el-icon :size="16"><Check /></el-icon>
            <span>资产全生命周期管理</span>
          </li>
          <li>
            <el-icon :size="16"><Check /></el-icon>
            <span>实时远程桌面与运维</span>
          </li>
          <li>
            <el-icon :size="16"><Check /></el-icon>
            <span>软件仓库 + 黑白名单</span>
          </li>
          <li>
            <el-icon :size="16"><Check /></el-icon>
            <span>告警 + 批量 + 策略下发</span>
          </li>
        </ul>
      </div>

      <div class="zv-brand-footer">
        © {{ new Date().getFullYear() }} Z-View · 让运维更轻松
      </div>
    </div>

    <!-- 右侧登录表单 -->
    <div class="zv-login-form-side">
      <div class="zv-form-wrap">
        <div class="zv-form-head">
          <h2 class="zv-form-title">欢迎回来 👋</h2>
          <p class="zv-form-subtitle">请使用您的账号登录</p>
        </div>

        <el-alert
          v-if="loginNotice"
          :title="loginNotice"
          type="warning"
          :closable="false"
          class="zv-login-notice"
          show-icon
        />

        <el-form :model="loginForm" :rules="rules" ref="formRef" size="large" class="zv-login-form">
          <el-form-item prop="username">
            <el-input
              v-model="loginForm.username"
              placeholder="请输入用户名"
              autocomplete="username"
              :prefix-icon="'User'"
            />
          </el-form-item>

          <el-form-item prop="password">
            <el-input
              v-model="loginForm.password"
              type="password"
              placeholder="请输入密码"
              show-password
              autocomplete="current-password"
              :prefix-icon="'Lock'"
              @keyup.enter="handleLogin"
            />
          </el-form-item>

          <div class="zv-form-options">
            <el-checkbox v-model="rememberMe">记住我</el-checkbox>
            <a class="zv-form-link">忘记密码？</a>
          </div>

          <el-button
            type="primary"
            :loading="loading"
            @click="handleLogin"
            class="zv-login-button"
            size="large"
          >
            {{ loading ? '登录中...' : '登 录' }}
          </el-button>
        </el-form>

        <div class="zv-form-footer">
          登录即表示同意 <a class="zv-form-link">《服务协议》</a> 与 <a class="zv-form-link">《隐私政策》</a>
        </div>
      </div>
    </div>

    <el-dialog
      v-model="forcePasswordDialogVisible"
      title="首次登录，请先修改密码"
      width="460px"
      :show-close="false"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
    >
      <el-alert
        title="检测到当前账号仍在使用默认凭据。为了系统安全，必须先完成密码修改后才能进入平台。"
        type="warning"
        :closable="false"
        class="force-password-alert"
        show-icon
      />

      <el-form ref="forcePasswordFormRef" :model="forcePasswordForm" :rules="forcePasswordRules" label-position="top">
        <el-form-item label="新密码" prop="new_password">
          <el-input
            v-model="forcePasswordForm.new_password"
            type="password"
            show-password
            placeholder="请输入新密码"
          />
        </el-form-item>
        <el-form-item label="确认新密码" prop="confirm_password">
          <el-input
            v-model="forcePasswordForm.confirm_password"
            type="password"
            show-password
            placeholder="请再次输入新密码"
            @keyup.enter="submitForcedPasswordChange"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <span class="dialog-footer">
          <el-button @click="cancelForcedPasswordChange">退出登录</el-button>
          <el-button type="primary" :loading="forcePasswordSubmitting" @click="submitForcedPasswordChange">
            确认修改
          </el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Monitor, Check } from '@element-plus/icons-vue'
import {
  changePassword,
  clearAuthSession,
  evaluatePasswordStrength,
  login,
  resolveLoginReasonText,
  setAuthSession
} from '@/api/auth'

const router = useRouter()
const route = useRoute()
const formRef = ref(null)
const loading = ref(false)
const rememberMe = ref(false)
const forcePasswordDialogVisible = ref(false)
const forcePasswordFormRef = ref(null)
const forcePasswordSubmitting = ref(false)
const pendingCurrentPassword = ref('')

const loginForm = reactive({
  username: '',
  password: ''
})
const forcePasswordForm = reactive({
  new_password: '',
  confirm_password: ''
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}
const loginNotice = computed(() => resolveLoginReasonText(route.query.reason))

const validateForcedPassword = (_rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入新密码'))
    return
  }
  const result = evaluatePasswordStrength(value, loginForm.username)
  if (!result.passed) {
    callback(new Error(result.message))
    return
  }
  callback()
}

const validateForcedPasswordConfirm = (_rule, value, callback) => {
  if (!value) {
    callback(new Error('请再次输入新密码'))
    return
  }
  if (value !== forcePasswordForm.new_password) {
    callback(new Error('两次输入的新密码不一致'))
    return
  }
  callback()
}

const forcePasswordRules = {
  new_password: [{ validator: validateForcedPassword, trigger: 'blur' }],
  confirm_password: [{ validator: validateForcedPasswordConfirm, trigger: 'blur' }]
}

const resetForcedPasswordForm = () => {
  forcePasswordForm.new_password = ''
  forcePasswordForm.confirm_password = ''
  forcePasswordFormRef.value?.clearValidate()
}

const handleLogin = async () => {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    const session = await login(loginForm)
    setAuthSession(session)
    if (session?.must_change_password) {
      pendingCurrentPassword.value = loginForm.password
      resetForcedPasswordForm()
      forcePasswordDialogVisible.value = true
      ElMessage.warning('首次登录必须先修改密码')
      return
    }

    ElMessage.success('登录成功')
    router.push(route.query.redirect || '/dashboard')
  } catch (error) {
    console.error('登录失败:', error)
  } finally {
    loading.value = false
  }
}

const cancelForcedPasswordChange = async () => {
  forcePasswordDialogVisible.value = false
  pendingCurrentPassword.value = ''
  resetForcedPasswordForm()
  clearAuthSession()
  await router.replace('/login')
}

const submitForcedPasswordChange = async () => {
  if (!forcePasswordFormRef.value) return
  const valid = await forcePasswordFormRef.value.validate().catch(() => false)
  if (!valid) return

  forcePasswordSubmitting.value = true
  try {
    await changePassword({
      current_password: pendingCurrentPassword.value,
      new_password: forcePasswordForm.new_password
    })
    forcePasswordDialogVisible.value = false
    pendingCurrentPassword.value = ''
    resetForcedPasswordForm()
    clearAuthSession()
    ElMessage.success('密码已更新，请使用新密码重新登录')
    await router.replace('/login?reason=password_changed')
  } catch (error) {
    console.error('首次改密失败:', error)
  } finally {
    forcePasswordSubmitting.value = false
  }
}
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-login {
  display: flex;
  min-height: 100vh;
  background: $bg-body;
}

// ---- 左侧品牌 ----
.zv-login-brand {
  flex: 1;
  background: linear-gradient(135deg, #1e40af 0%, #2563eb 50%, #3b82f6 100%);
  color: #fff;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 60px;
}

.zv-brand-deco {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  pointer-events: none;

  &.zv-deco-1 {
    width: 400px;
    height: 400px;
    top: -120px;
    right: -120px;
  }
  &.zv-deco-2 {
    width: 300px;
    height: 300px;
    bottom: -80px;
    left: -80px;
    background: rgba(255, 255, 255, 0.06);
  }
  &.zv-deco-3 {
    width: 180px;
    height: 180px;
    top: 40%;
    left: 60%;
    background: rgba(255, 255, 255, 0.04);
  }
}

.zv-brand-content {
  position: relative;
  z-index: 1;
  max-width: 480px;
}

.zv-brand-icon {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.18);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  margin-bottom: 24px;
  border: 1px solid rgba(255, 255, 255, 0.2);
}

.zv-brand-name {
  font-size: 42px;
  font-weight: 700;
  letter-spacing: 0.5px;
  margin: 0 0 12px 0;
  line-height: 1.1;
}

.zv-brand-tag {
  font-size: 16px;
  color: rgba(255, 255, 255, 0.85);
  margin: 0 0 40px 0;
  font-weight: 400;
}

.zv-brand-features {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;

  li {
    display: flex;
    align-items: center;
    gap: 12px;
    color: rgba(255, 255, 255, 0.92);
    font-size: 15px;

    .el-icon {
      width: 24px;
      height: 24px;
      border-radius: 6px;
      background: rgba(255, 255, 255, 0.18);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #fff;
    }
  }
}

.zv-brand-footer {
  position: relative;
  z-index: 1;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.6);
}

// ---- 右侧表单 ----
.zv-login-form-side {
  flex: 0 0 480px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: $bg-card;
  padding: 40px;
}

.zv-form-wrap {
  width: 100%;
  max-width: 360px;
}

.zv-form-head {
  margin-bottom: 32px;
}

.zv-form-title {
  font-size: 28px;
  font-weight: 700;
  color: $text-primary;
  margin: 0 0 8px 0;
  letter-spacing: -0.3px;
}

.zv-form-subtitle {
  font-size: 14px;
  color: $text-secondary;
  margin: 0;
}

.zv-login-notice {
  margin-bottom: 20px;
}

.zv-login-form {
  :deep(.el-input__wrapper) {
    padding: 4px 12px;
    border-radius: $border-radius;
    background: $bg-body;
    box-shadow: none;
    transition: all $transition-base;

    &.is-focus {
      background: $bg-card;
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
    }
  }

  :deep(.el-input__inner) {
    height: 44px;
    font-size: 14px;
    color: $text-primary;
    &::placeholder { color: $text-tertiary; }
  }

  :deep(.el-input__prefix) {
    .el-icon {
      color: $text-tertiary;
    }
  }

  .el-form-item { margin-bottom: 18px; }
}

.zv-form-options {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin: 0 0 24px 0;

  :deep(.el-checkbox) {
    .el-checkbox__label {
      color: $text-secondary;
      font-size: 13px;
    }
  }
}

.zv-form-link {
  color: $brand-primary;
  font-size: 13px;
  cursor: pointer;
  text-decoration: none;
  transition: color $transition-fast;
  &:hover { color: $brand-primary-light; }
}

.zv-login-button {
  width: 100%;
  height: 48px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 1px;
  border-radius: $border-radius;
  background: linear-gradient(135deg, $brand-primary 0%, $brand-primary-dark 100%) !important;
  border: none !important;
  box-shadow: 0 4px 14px rgba(37, 99, 235, 0.35);
  transition: all $transition-base;

  &:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(37, 99, 235, 0.45);
  }
  &:active {
    transform: translateY(0);
  }
}

.zv-form-footer {
  margin-top: 24px;
  text-align: center;
  font-size: 12px;
  color: $text-tertiary;
  line-height: 1.6;
}

.force-password-alert {
  margin-bottom: 16px;
}

@media (max-width: 960px) {
  .zv-login-brand { display: none; }
  .zv-login-form-side { flex: 1; }
}
</style>
