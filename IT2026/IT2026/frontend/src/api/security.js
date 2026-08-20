import request from './request'

// 安全总览
export function getSecurityOverview() {
  return request({ url: '/security/overview', method: 'get' })
}

// 终端安全
export function getSecurityTerminals(params) {
  return request({ url: '/security/terminals', method: 'get', params })
}

export function getSecurityTerminalDetail(id) {
  return request({ url: `/security/terminals/${id}`, method: 'get' })
}

// 安全事件
export function getSecurityEvents(params) {
  return request({ url: '/security/events', method: 'get', params })
}

export function getSecurityEventDetail(id) {
  return request({ url: `/security/events/${id}`, method: 'get' })
}

export function handleSecurityEvent(id, data) {
  return request({ url: `/security/events/${id}/handle`, method: 'put', data })
}

export function batchHandleSecurityEvents(data) {
  return request({ url: '/security/events/batch-handle', method: 'post', data })
}

export function getSecurityEventStats(params) {
  return request({ url: '/security/events/stats', method: 'get', params })
}

// 防火墙
export function applyFirewallPolicy(data) {
  return request({ url: '/security/firewall/apply', method: 'post', data })
}

export function getFirewallStatus(assetId) {
  return request({ url: `/security/firewall/status/${assetId}`, method: 'get' })
}

export function getFirewallRules(params) {
  return request({ url: '/security/firewall/rules', method: 'get', params })
}

// USB 管控
export function getUsbDevices(params) {
  return request({ url: '/security/usb/devices', method: 'get', params })
}

export function getUsbEvents(params) {
  return request({ url: '/security/usb/events', method: 'get', params })
}

export function applyUsbPolicy(data) {
  return request({ url: '/security/usb/policy', method: 'post', data })
}

// 程序管控
export function getProcessLaunchLogs(params) {
  return request({ url: '/security/app-control/logs', method: 'get', params })
}

export function applyAppControlPolicy(data) {
  return request({ url: '/security/app-control/policy', method: 'post', data })
}

// 文件保护
export function getFileProtectBaselines(params) {
  return request({ url: '/security/file-protect/baselines', method: 'get', params })
}

export function applyFileProtectPolicy(data) {
  return request({ url: '/security/file-protect/policy', method: 'post', data })
}

export function getFileAnomalies(params) {
  return request({ url: '/security/file-protect/anomalies', method: 'get', params })
}

// 行为监控
export function getBehaviorEvents(params) {
  return request({ url: '/security/behavior/events', method: 'get', params })
}

// 策略中心
export function getSecurityPolicies(params) {
  return request({ url: '/security/policies', method: 'get', params })
}

export function createSecurityPolicy(data) {
  return request({ url: '/security/policies', method: 'post', data })
}

export function getSecurityPolicyDetail(id) {
  return request({ url: `/security/policies/${id}`, method: 'get' })
}

export function updateSecurityPolicy(id, data) {
  return request({ url: `/security/policies/${id}`, method: 'put', data })
}

export function deleteSecurityPolicy(id) {
  return request({ url: `/security/policies/${id}`, method: 'delete' })
}

export function bindSecurityPolicy(id, data) {
  return request({ url: `/security/policies/${id}/bind`, method: 'post', data })
}

export function unbindSecurityPolicy(id, bindingId) {
  return request({ url: `/security/policies/${id}/bind/${bindingId}`, method: 'delete' })
}

export function rollbackSecurityPolicy(id, data) {
  return request({ url: `/security/policies/${id}/rollback`, method: 'post', data })
}

export function getSecurityPolicyVersions(id) {
  return request({ url: `/security/policies/${id}/versions`, method: 'get' })
}

export function getSecurityPolicyExecResults(id, params) {
  return request({ url: `/security/policies/${id}/exec-results`, method: 'get', params })
}

// 远程安全运维
export function remoteScan(assetId) {
  return request({ url: `/security/remote/scan/${assetId}`, method: 'post' })
}

export function remoteKillProcess(assetId, data) {
  return request({ url: `/security/remote/kill-process/${assetId}`, method: 'post', data })
}

export function remoteIsolate(assetId) {
  return request({ url: `/security/remote/isolate/${assetId}`, method: 'post' })
}