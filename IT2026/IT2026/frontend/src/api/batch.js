import request from './request'

export function executeBatchOperation(data) {
  return request({
    url: '/batch/execute',
    method: 'post',
    data
  })
}

export function getBatchHistory(params) {
  return request({
    url: '/batch/history',
    method: 'get',
    params
  })
}

export function getBatchOperationResults(id) {
  return request({
    url: `/batch/${id}/results`,
    method: 'get'
  })
}
