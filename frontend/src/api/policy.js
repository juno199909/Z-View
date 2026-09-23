import { policyRequest } from './request'

// 获取策略列表
export function getPolicies(params) {
  return policyRequest({
    url: '/policies',
    method: 'get',
    params
  })
}

// 获取策略详情
export function getPolicyDetail(id) {
  return policyRequest({
    url: `/policies/${id}`,
    method: 'get'
  })
}

// 创建策略
export function createPolicy(data) {
  return policyRequest({
    url: '/policies',
    method: 'post',
    data
  })
}

// 更新策略
export function updatePolicy(id, data) {
  return policyRequest({
    url: `/policies/${id}`,
    method: 'put',
    data
  })
}

// 立即执行策略
export function executePolicy(id) {
  return policyRequest({
    url: `/policies/${id}/execute`,
    method: 'post'
  })
}

// 删除策略
export function deletePolicy(id) {
  return policyRequest({
    url: `/policies/${id}`,
    method: 'delete'
  })
}

// 检查策略
export function checkPolicy(assetId, softwareName) {
  return policyRequest({
    url: `/policies/check/${assetId}`,
    method: 'get',
    params: { software_name: softwareName }
  })
}

// 获取策略日志
export function getPolicyLogs(params) {
  return policyRequest({
    url: '/policies/logs',
    method: 'get',
    params
  })
}
