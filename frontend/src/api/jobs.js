import request from './request'

export function listAgentJobs(params) {
  return request({
    url: '/console/agent-jobs',
    method: 'get',
    params
  })
}

export function createAgentJob(data) {
  return request({
    url: '/console/agent-jobs',
    method: 'post',
    data
  })
}
