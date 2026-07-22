<template>
  <div class="login-container">
    <el-card class="login-card">
      <div class="login-header">
        <img src="/zview-logo.png" alt="Z-View" class="brand-logo" />
        <h1>Z-View</h1>
        <p>企业终端运维平台</p>
      </div>

      <el-alert
        v-if="loginNotice"
        :title="loginNotice"
        type="warning"
        :closable="false"
        class="login-notice"
      />

      <el-form :model="loginForm" :rules="rules" ref="formRef" size="large">
        <el-form-item prop="username">
          <el-input
            v-model="loginForm.username"
            placeholder="用户名"
            prefix-icon="User"
          />
        </el-form-item>

        <el-form-item prop="password">
          <el-input
            v-model="loginForm.password"
            type="password"
            placeholder="密码"
            prefix-icon="Lock"
            @keyup.enter="handleLogin"
          />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="loading" @click="handleLogin" style="width: 100%">
            登录
          </el-button>
        </el-form-item>
      </el-form>

    </el-card>
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
    />

    <el-form ref="forcePasswordFormRef" :model="forcePasswordForm" :rules="forcePasswordRules" label-position="top">
      <el-form-item label="新密码" prop="new_password">
        <el-input
          v-model="forcePasswordForm.new_password"
          type="password"
          show-password
          placeholder="请输入新密码"
        />
        <div class="password-strength">
          <div class="strength-header">
            <span>密码强度</span>
            <span :class="['strength-label', `strength-${forcePasswordStrength.tone}`]">
              {{ forcePasswordStrength.label }}
            </span>
          </div>
          <div class="strength-bar">
            <span :style="{ width: `${forcePasswordStrength.percentage}%` }" :class="`strength-${forcePasswordStrength.tone}`" />
          </div>
          <ul class="strength-checklist">
            <li
              v-for="item in forcePasswordStrength.requirements"
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
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
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
const forcePasswordStrength = computed(() =>
  evaluatePasswordStrength(forcePasswordForm.new_password, loginForm.username)
)

const validateForcedPassword = (_rule, value, callback) => {
  const result = evaluatePasswordStrength(value, loginForm.username)
  if (!value) {
    callback(new Error('请输入新密码'))
    return
  }
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
  if (!valid) {
    return
  }

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
  if (!forcePasswordFormRef.value) {
    return
  }

  const valid = await forcePasswordFormRef.value.validate().catch(() => false)
  if (!valid) {
    return
  }

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
.login-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);

  .login-card {
    width: 420px;
    padding: 20px;

    .login-notice {
      margin-bottom: 18px;
    }

    .login-header {
      text-align: center;
      margin-bottom: 30px;

      .brand-logo {
        width: 72px;
        height: 72px;
        object-fit: contain;
        margin-bottom: 14px;
      }

      h1 {
        font-size: 28px;
        color: #333;
        margin-bottom: 10px;
      }

      p {
        color: #999;
        font-size: 14px;
      }
    }

  }
}

.force-password-alert {
  margin-bottom: 16px;
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
</style>
