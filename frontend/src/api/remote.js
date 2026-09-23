import request from './request'

export function createRemoteSession(data) {
  return request({ url: '/remote/sessions', method: 'post', data })
}

export function getRemoteSession(id) {
  return request({ url: `/remote/sessions/${id}`, method: 'get' })
}

export function deleteRemoteSession(id) {
  return request({ url: `/remote/sessions/${id}`, method: 'delete' })
}

export function listRemoteSessions(params) {
  return request({ url: '/remote/sessions', method: 'get', params })
}