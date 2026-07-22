import request from './request'

export function getAssetList(params) {
  return request({
    url: '/assets',
    method: 'get',
    params
  })
}

export function getAssetDetail(id) {
  return request({
    url: `/assets/${id}/detail`,
    method: 'get'
  })
}

export function getAssetInfo(id) {
  return request({
    url: `/assets/${id}`,
    method: 'get'
  })
}

export function createAsset(data) {
  return request({
    url: '/assets',
    method: 'post',
    data
  })
}

export function updateAsset(id, data) {
  return request({
    url: `/assets/${id}`,
    method: 'put',
    data
  })
}

export function deleteAsset(id) {
  return request({
    url: `/assets/${id}`,
    method: 'delete'
  })
}

export function getAssetStats(params) {
  return request({
    url: '/assets/stats',
    method: 'get',
    params
  })
}

export function getAssetChanges(id, params) {
  return request({
    url: `/assets/${id}/changes`,
    method: 'get',
    params
  })
}

export function getAssetStatus(id) {
  return request({
    url: `/assets/${id}/status`,
    method: 'get'
  })
}

export function getAssetStatusHistory(id, params) {
  return request({
    url: `/assets/${id}/status/history`,
    method: 'get',
    params
  })
}

export function getAssetUptime(id, params) {
  return request({
    url: `/assets/${id}/uptime`,
    method: 'get',
    params
  })
}

export function batchDeleteAssets(ids) {
  return request({
    url: '/assets/batch-delete',
    method: 'post',
    data: { ids }
  })
}

export function exportAssets(params) {
  return request({
    url: '/assets/export',
    method: 'get',
    params,
    responseType: 'blob'
  })
}

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

export function updateAssetGroup(id, groupId) {
  return request({
    url: `/assets/${id}`,
    method: 'put',
    data: {
      group_id: groupId ?? null
    }
  })
}

export function getInstalledSoftwareInventory() {
  return request({
    url: '/software/all',
    method: 'get'
  })
}

export function getSoftwareStats(params) {
  return request({
    url: '/software/stats',
    method: 'get',
    params
  })
}
