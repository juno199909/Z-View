import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'

// P2 Remote Shell 域 composable（能力开关 + 消息发送，socket 注入方式与 useClipboard 一致）
export function useRemoteShell({
  remoteCapabilities,
  connectionStatus,
  sendSocketMessage,
  onFullscreenActivity
}) {
  const shellVisible = ref(false)
  const shellSupported = computed(() => Boolean(remoteCapabilities.value?.shell))
  const shellTimeoutSeconds = computed(() =>
    Math.max(5, Math.min(600, Number(remoteCapabilities.value?.shellTimeoutSeconds) || 60))
  )

  const openShellDrawer = () => {
    if (!shellSupported.value) {
      ElMessage.warning('被控端策略未启用远程 Shell，请在 系统设置 → 终端策略 中开启')
      return
    }
    onFullscreenActivity?.()
    shellVisible.value = true
  }

  const sendShellExec = (command, id) => {
    if (connectionStatus.value !== 'connected') {
      ElMessage.warning('远程桌面未连接，无法执行命令')
      return false
    }
    return sendSocketMessage({ type: 'shell_exec', id, command })
  }

  const sendShellStop = (id) => sendSocketMessage({ type: 'shell_stop', id })

  return {
    shellVisible,
    shellSupported,
    shellTimeoutSeconds,
    openShellDrawer,
    sendShellExec,
    sendShellStop
  }
}
