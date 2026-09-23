import axios from 'axios'
import { ElMessage } from 'element-plus'
import { getAuthToken, handleAuthExpired, isSessionExpired } from './auth-session'

async function extractErrorMessage(error) {
  const responseData = error.response?.data

  if (responseData instanceof Blob) {
    try {
      const text = await responseData.text()
      if (text) {
        const parsed = JSON.parse(text)
        return parsed.detail || parsed.message || parsed.error || error.message || '请求失败'
      }
    } catch {
      return error.message || '请求失败'
    }
  }

  return (
    responseData?.detail ||
    responseData?.message ||
    responseData?.error ||
    error.message ||
    '请求失败'
  )
}

function attachInterceptors(service) {
  service.interceptors.request.use(
    config => {
      const requestUrl = String(config?.url || '')
      const isLoginRequest = requestUrl.includes('/auth/login')

      if (!isLoginRequest && getAuthToken() && isSessionExpired()) {
        handleAuthExpired('expired')
        const expiredError = new Error('登录已过期，请重新登录')
        expiredError.__skipGlobalErrorMessage = true
        return Promise.reject(expiredError)
      }

      const token = getAuthToken()
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
      return config
    },
    error => {
      console.error('请求错误:', error)
      return Promise.reject(error)
    }
  )

  service.interceptors.response.use(
    response => response.data,
    async error => {
      if (error?.__skipGlobalErrorMessage) {
        return Promise.reject(error)
      }

      const requestUrl = String(error?.config?.url || '')
      const isLoginRequest = requestUrl.includes('/auth/login')
      const msg = await extractErrorMessage(error)
      if (!(error.response?.status === 401 && !isLoginRequest)) {
        ElMessage.error(msg)
      }

      if (error.response?.status === 401 && !isLoginRequest) {
        handleAuthExpired('expired')
      }

      return Promise.reject(error)
    }
  )

  return service
}

export function createRequestClient(baseURL, overrides = {}) {
  return attachInterceptors(
    axios.create({
      baseURL,
      timeout: 30000,
      ...overrides
    })
  )
}

const request = createRequestClient('/api/v1')

export const softwareRequest = createRequestClient('/software-api/api/v1')
export const policyRequest = createRequestClient('/policy-api/api/v1')

export default request
