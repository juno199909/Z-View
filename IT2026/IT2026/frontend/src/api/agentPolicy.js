import request from './request'

// 获取终端策略（管理台）
export function getAgentPolicies() {
  return request({
    url: '/console/agent-policies',
    method: 'get'
  })
}

// 更新终端策略（保存后在线终端将在下一个心跳周期自动同步）
export function updateAgentPolicies(data) {
  return request({
    url: '/console/agent-policies',
    method: 'put',
    data
  })
}
