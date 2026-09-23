import request from './request'

// 获取分组列表
export function getGroups() {
  return request({
    url: '/groups',
    method: 'get'
  })
}

// 创建分组
export function createGroup(data) {
  return request({
    url: '/groups',
    method: 'post',
    data
  })
}

// 更新分组
export function updateGroup(id, data) {
  return request({
    url: `/groups/${id}`,
    method: 'put',
    data
  })
}

// 删除分组
export function deleteGroup(id) {
  return request({
    url: `/groups/${id}`,
    method: 'delete'
  })
}
