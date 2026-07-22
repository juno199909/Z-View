import request from './request'

export function getLogStats(params) {
  return request({
    url: '/logs/stats',
    method: 'get',
    params
  })
}

export function getLogList(params) {
  return request({
    url: '/logs',
    method: 'get',
    params
  })
}
