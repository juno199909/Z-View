import request from './request'

export function getAlertStats() {
  return request({
    url: '/alerts/stats',
    method: 'get'
  })
}

export function getAlertList(params) {
  return request({
    url: '/alerts',
    method: 'get',
    params
  })
}

export function getAlertDetail(id) {
  return request({
    url: `/alerts/${id}/detail`,
    method: 'get'
  })
}

export function resolveAlertById(id) {
  return request({
    url: `/alerts/${id}/resolve`,
    method: 'put'
  })
}

export function batchResolveAlerts(data) {
  return request({
    url: '/alerts/resolve-batch',
    method: 'post',
    data
  })
}

export function exportAlerts(params) {
  return request({
    url: '/alerts/export',
    method: 'get',
    params,
    responseType: 'blob'
  })
}
