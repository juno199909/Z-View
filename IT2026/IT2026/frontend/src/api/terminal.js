import request from './request'

function sendTerminalCommand(id, command) {
  return request({
    url: `/assets/${id}/command`,
    method: 'post',
    data: { command }
  })
}

export function rebootTerminal(id) {
  return sendTerminalCommand(id, 'shutdown /r /t 5')
}

export function shutdownTerminal(id) {
  return sendTerminalCommand(id, 'shutdown /s /t 5')
}
