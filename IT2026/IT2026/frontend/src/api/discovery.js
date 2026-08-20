import request from './request'

// 获取发现任务列表
export function getDiscoveryTasks() {
  return request({
    url: '/discovery/tasks',
    method: 'get'
  })
}

// 获取任务详情
export function getTaskDetail(taskId) {
  return request({
    url: `/discovery/tasks/${taskId}`,
    method: 'get'
  })
}

// 取消任务
export function cancelTask(taskId) {
  return request({
    url: `/discovery/tasks/${taskId}/cancel`,
    method: 'post'
  })
}

// 启动Ping扫描
export function startPingScan(data) {
  return request({
    url: '/discovery/ping',
    method: 'post',
    data
  })
}

// 启动 SNMP 采集
export function startSnmpScan(data) {
  return request({
    url: '/discovery/snmp',
    method: 'post',
    data
  })
}

// 获取最近扫描记录
export function getRecentScans(params) {
  return request({
    url: '/discovery/recent',
    method: 'get',
    params
  })
}

// 导入扫描结果为资产
export function importDiscoveredAsset(data) {
  return request({
    url: '/discovery/import',
    method: 'post',
    data
  })
}
