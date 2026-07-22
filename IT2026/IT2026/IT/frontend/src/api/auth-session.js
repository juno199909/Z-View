const TOKEN_KEY = 'token'
const USERNAME_KEY = 'auth_username'
const EXPIRES_AT_KEY = 'auth_expires_at'
const ISSUED_AT_KEY = 'auth_issued_at'
const CREDENTIAL_SOURCE_KEY = 'auth_credential_source'
const PASSWORD_UPDATED_AT_KEY = 'auth_password_updated_at'
const MUST_CHANGE_PASSWORD_KEY = 'auth_must_change_password'
const REQUESTER_KEY = 'remote_desktop_requester'

function setOptionalStorage(key, value) {
  if (value === null || value === undefined || value === '') {
    localStorage.removeItem(key)
    return
  }
  localStorage.setItem(key, String(value))
}

function parseStoredNumber(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function parseStoredBoolean(value) {
  return value === '1' || value === 'true'
}

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function getStoredAuthUsername() {
  return localStorage.getItem(USERNAME_KEY) || ''
}

export function getAuthSessionMeta() {
  return {
    username: getStoredAuthUsername(),
    issued_at: parseStoredNumber(localStorage.getItem(ISSUED_AT_KEY)),
    expires_at: parseStoredNumber(localStorage.getItem(EXPIRES_AT_KEY)),
    credential_source: localStorage.getItem(CREDENTIAL_SOURCE_KEY) || 'default',
    password_updated_at: parseStoredNumber(localStorage.getItem(PASSWORD_UPDATED_AT_KEY)),
    must_change_password: parseStoredBoolean(localStorage.getItem(MUST_CHANGE_PASSWORD_KEY))
  }
}

function persistAuthMeta(session) {
  const username = session?.username
  if (username) {
    localStorage.setItem(USERNAME_KEY, username)
    localStorage.setItem(REQUESTER_KEY, username)
  } else {
    localStorage.removeItem(USERNAME_KEY)
    localStorage.removeItem(REQUESTER_KEY)
  }

  setOptionalStorage(EXPIRES_AT_KEY, session?.expires_at)
  setOptionalStorage(ISSUED_AT_KEY, session?.issued_at)
  setOptionalStorage(CREDENTIAL_SOURCE_KEY, session?.credential_source)
  setOptionalStorage(PASSWORD_UPDATED_AT_KEY, session?.password_updated_at)
  setOptionalStorage(
    MUST_CHANGE_PASSWORD_KEY,
    session?.must_change_password ? '1' : session?.must_change_password === false ? '0' : null
  )
}

export function setAuthSession(session) {
  const token = session?.access_token || session?.token
  if (!token) {
    throw new Error('登录返回缺少访问令牌')
  }

  localStorage.setItem(TOKEN_KEY, token)
  persistAuthMeta(session)
}

export function updateAuthSessionMeta(session) {
  if (!getAuthToken()) {
    return
  }
  persistAuthMeta(session)
}

export function clearAuthSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USERNAME_KEY)
  localStorage.removeItem(EXPIRES_AT_KEY)
  localStorage.removeItem(ISSUED_AT_KEY)
  localStorage.removeItem(CREDENTIAL_SOURCE_KEY)
  localStorage.removeItem(PASSWORD_UPDATED_AT_KEY)
  localStorage.removeItem(MUST_CHANGE_PASSWORD_KEY)
  localStorage.removeItem(REQUESTER_KEY)
}

export function isSessionExpired(nowMs = Date.now()) {
  const expiresAt = getAuthSessionMeta().expires_at
  return Boolean(expiresAt && nowMs >= expiresAt * 1000)
}

export function hasAuthSession() {
  const token = getAuthToken()
  if (!token) {
    return false
  }

  if (isSessionExpired()) {
    clearAuthSession()
    return false
  }

  return true
}

export function isPasswordChangeRequired() {
  return hasAuthSession() && getAuthSessionMeta().must_change_password
}

export function getAuthSessionRemainingSeconds(nowMs = Date.now()) {
  const expiresAt = getAuthSessionMeta().expires_at
  if (!expiresAt) {
    return null
  }
  return Math.max(0, Math.floor((expiresAt * 1000 - nowMs) / 1000))
}

export function buildLoginPath(reason = '', redirect = '') {
  const params = new URLSearchParams()
  if (reason) {
    params.set('reason', reason)
  }
  if (redirect) {
    params.set('redirect', redirect)
  }
  const query = params.toString()
  return query ? `/login?${query}` : '/login'
}

export function getCurrentAppPath() {
  if (typeof window === 'undefined') {
    return ''
  }
  return `${window.location.pathname}${window.location.search}` || '/'
}

export function handleAuthExpired(reason = 'expired', redirectTarget = '') {
  const currentPath = redirectTarget || getCurrentAppPath()
  clearAuthSession()

  if (typeof window === 'undefined') {
    return
  }

  const safeRedirect = currentPath && !currentPath.startsWith('/login') ? currentPath : ''
  const loginPath = buildLoginPath(reason, safeRedirect)
  if (getCurrentAppPath() !== loginPath) {
    window.location.replace(loginPath)
  }
}

export function resolveLoginReasonText(reason) {
  switch (reason) {
    case 'expired':
      return '登录已超时，请重新登录。'
    case 'password_changed':
      return '密码已更新，请使用新密码重新登录。'
    default:
      return ''
  }
}

export function evaluatePasswordStrength(password, username = '') {
  const normalizedPassword = String(password || '')
  const normalizedUsername = String(username || '').trim().toLowerCase()
  const loweredPassword = normalizedPassword.toLowerCase()
  const requirements = [
    { text: '长度至少 8 位', passed: normalizedPassword.length >= 8 },
    { text: '包含字母', passed: /[A-Za-z]/.test(normalizedPassword) },
    { text: '包含数字', passed: /\d/.test(normalizedPassword) },
    {
      text: '包含大小写字母，或至少 1 个特殊字符',
      passed:
        (/[a-z]/.test(normalizedPassword) && /[A-Z]/.test(normalizedPassword)) ||
        /[^A-Za-z0-9]/.test(normalizedPassword)
    },
    {
      text: '不包含账号名',
      passed: !normalizedUsername || !loweredPassword.includes(normalizedUsername)
    },
    {
      text: '避免常见弱口令片段',
      passed: !/(123456|123123|abc123|admin123|password|qwerty|000000)/i.test(normalizedPassword)
    }
  ]

  const passedCount = requirements.filter(item => item.passed).length
  const percentage = Math.max(10, Math.round((passedCount / requirements.length) * 100))
  let label = '弱'
  let tone = 'weak'

  if (passedCount >= 5) {
    label = '强'
    tone = 'strong'
  } else if (passedCount >= 3) {
    label = '中'
    tone = 'medium'
  }

  return {
    passed: requirements.every(item => item.passed),
    label,
    tone,
    percentage,
    requirements,
    message: requirements.find(item => !item.passed)?.text || '密码强度符合要求'
  }
}
