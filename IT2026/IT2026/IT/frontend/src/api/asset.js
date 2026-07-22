import request from './request'

// 获取资产列表
export function getAssetList(params) {
  return request({
    url: '/assets',
    method: 'get',
    params
  })
}

// 获取资产详情
export function getAssetDetail(id) {
  return request({
    url: `/assets/${id}/detail`,
    method: 'get'
  })
}

// 获取资产基本信息
export function getAssetInfo(id) {
  return request({
    url: `/assets/${id}`,
    method: 'get'
  })
}

// 创建资产
export function createAsset(data) {
  return request({
    url: '/assets',
    method: 'post',
    data
  })
}

// 更新资产
export function updateAsset(id, data) {
  return request({
    url: `/assets/${id}`,
    method: 'put',
    data
  })
}

// 删除资产
export function deleteAsset(id) {
  return request({
    url: `/assets/${id}`,
    method: 'delete'
  })
}

// 获取资产统计
export function getAssetStats(params) {
  return request({
    url: '/assets/stats',
    method: 'get',
    params
  })
}

// 获取资产变更历史
export function getAssetChanges(id, params) {
  return request({
    url: `/assets/${id}/changes`,
    method: 'get',
    params
  })
}

// 获取资产详细状态
export function getAssetStatus(id) {
  return request({
    url: `/assets/${id}/status`,
    method: 'get'
  })
}

// 获取资产状态历史
export function getAssetStatusHistory(id, params) {
  return request({
    url: `/assets/${id}/status/history`,
    method: 'get',
    params
  })
}

// 获取资产在线率
export function getAssetUptime(id, params) {
  return request({
    url: `/assets/${id}/uptime`,
    method: 'get',
    params
  })
}

// 批量删除资产
export function batchDeleteAssets(ids) {
  return request({
    url: '/assets/batch-delete',
    method: 'post',
    data: { ids }
  })
}

// 导出资产
export function exportAssets(params) {
  return request({
    url: '/assets/export',
    method: 'get',
    params,
    responseType: 'blob'
  })
}

// 远程控制
export function remoteControl(id, data) {
  return request({
    url: `/assets/${id}/remote-control`,
    method: 'post',
    data
  })
}

export function executeAssetCommand(id, data) {
  return request({
    url: `/assets/${id}/command`,
    method: 'post',
    data
  })
}

export function triggerAssetReport(id, data = {}) {
  return request({
    url: `/assets/${id}/trigger-report`,
    method: 'post',
    data
  })
}

// 更新资产分组
export function updateAssetGroup(id, groupId) {
  return request({
    url: `/assets/${id}`,
    method: 'put',
    data: {
      group_id: groupId ?? null
    }
  })
}

// 获取全量软件清单
export function getInstalledSoftwareInventory() {
  return request({
    url: '/software/all',
    method: 'get'
  })
}

// 获取软件安装统计 Top N
export function getSoftwareStats(params) {
  return request({
    url: '/software/stats',
    method: 'get',
    params
  })
}
