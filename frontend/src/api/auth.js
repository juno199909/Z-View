import request from './request'
export * from './auth-session'

export function login(data) {
  return request({
    url: '/auth/login',
    method: 'post',
    data
  })
}

export function getCurrentUser() {
  return request({
    url: '/auth/me',
    method: 'get'
  })
}

export function changePassword(data) {
  return request({
    url: '/auth/change-password',
    method: 'post',
    data
  })
}

export function listUsers() {
  return request({
    url: '/auth/users',
    method: 'get'
  })
}

export function createUser(data) {
  return request({
    url: '/auth/users',
    method: 'post',
    data
  })
}

export function updateUser(username, data) {
  return request({
    url: `/auth/users/${encodeURIComponent(username)}`,
    method: 'put',
    data
  })
}

export function resetUserPassword(username, data) {
  return request({
    url: `/auth/users/${encodeURIComponent(username)}/reset-password`,
    method: 'put',
    data
  })
}

export function deleteUser(username) {
  return request({
    url: `/auth/users/${encodeURIComponent(username)}`,
    method: 'delete'
  })
}
