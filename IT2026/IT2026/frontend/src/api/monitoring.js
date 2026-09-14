import request from './request'

// 监控中心 · 日志留存配置（V1.9.23）
export function getLogRetention() {
  return request({
    url: '/monitoring/log-retention',
    method: 'get'
  })
}

export function updateLogRetention(days) {
  return request({
    url: '/monitoring/log-retention',
    method: 'put',
    data: { days }
  })
}

export function runLogRetentionNow() {
  return request({
    url: '/monitoring/log-retention/run',
    method: 'post'
  })
}
