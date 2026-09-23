// -*- coding: utf-8 -*-
// useClipboard：远控剪贴板同步 composable（P1-02 从 WebRemoteDesktop.vue 提取）
// 依赖注入：socketApi = { send(msg) }，remoteCapabilities / connectionStatus 为组件 ref
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

export function useClipboard({ remoteCapabilities, connectionStatus, sendSocketMessage, onFullscreenActivity }) {
  const clipboardVisible = ref(false)
  const clipboardState = ref({
    localText: '',
    remoteText: ''
  })
  const localClipboardAvailable = typeof navigator !== 'undefined'
    && Boolean(navigator.clipboard)
    && typeof navigator.clipboard.readText === 'function'
    && typeof navigator.clipboard.writeText === 'function'
  const remoteClipboardShortcutReadDelayMs = 180

  const readLocalClipboard = async (options = {}) => {
    const { silent = false } = options
    if (!localClipboardAvailable) {
      if (!silent) {
        ElMessage.warning('当前浏览器或页面上下文不支持本地剪贴板读取')
      }
      return false
    }

    try {
      clipboardState.value.localText = await navigator.clipboard.readText()
      if (!silent) {
        ElMessage.success('已读取本地剪贴板')
      }
      return true
    } catch (error) {
      if (!silent) {
        ElMessage.error(`读取本地剪贴板失败: ${error.message}`)
      }
      return false
    }
  }

  const writeLocalClipboard = async (text, options = {}) => {
    const { silent = false } = options
    if (!localClipboardAvailable) {
      if (!silent) {
        ElMessage.warning('当前浏览器或页面上下文不支持本地剪贴板写入')
      }
      return false
    }

    try {
      await navigator.clipboard.writeText(text)
      if (!silent) {
        ElMessage.success('已写入本地剪贴板')
      }
      return true
    } catch (error) {
      if (!silent) {
        ElMessage.error(`写入本地剪贴板失败: ${error.message}`)
      }
      return false
    }
  }

  const requestRemoteClipboard = (options = {}) => {
    const { silent = false } = options
    if (!remoteCapabilities.value.clipboardText) {
      if (!silent) {
        ElMessage.warning('当前被控端未启用文本剪贴板同步')
      }
      return false
    }
    if (!sendSocketMessage({ type: 'clipboard_get' })) {
      if (!silent) {
        ElMessage.warning('远程桌面未连接，无法读取远端剪贴板')
      }
      return false
    }
    return true
  }

  const pushClipboardToRemote = async (options = {}) => {
    const { preferSystemClipboard = false, silent = false } = options
    if (!remoteCapabilities.value.clipboardText) {
      if (!silent) {
        ElMessage.warning('当前被控端未启用文本剪贴板同步')
      }
      return false
    }
    if (preferSystemClipboard) {
      await readLocalClipboard({ silent: true })
    }

    if (!sendSocketMessage({
      type: 'clipboard_set',
      text: clipboardState.value.localText || ''
    })) {
      if (!silent) {
        ElMessage.warning('远程桌面未连接，无法同步剪贴板')
      }
      return false
    }

    if (!silent) {
      ElMessage.success('正在推送本地剪贴板到被控端')
    }
    return true
  }

  const copyRemoteClipboardToLocal = async () => {
    if (!clipboardState.value.remoteText) {
      ElMessage.warning('远端剪贴板当前没有可复制的文本')
      return
    }
    await writeLocalClipboard(clipboardState.value.remoteText)
  }

  const openClipboardDialog = async () => {
    if (onFullscreenActivity) {
      onFullscreenActivity()
    }
    clipboardVisible.value = true
    await readLocalClipboard({ silent: true })
    if (remoteCapabilities.value.clipboardText) {
      requestRemoteClipboard({ silent: true })
    }
  }

  const scheduleRemoteClipboardPull = () => {
    if (!remoteCapabilities.value.clipboardText) {
      return
    }

    window.setTimeout(() => {
      if (connectionStatus.value === 'connected') {
        requestRemoteClipboard({ silent: true })
      }
    }, remoteClipboardShortcutReadDelayMs)
  }

  const isClipboardShortcut = (event, key) => {
    return (event.ctrlKey || event.metaKey) && String(event.key || '').toLowerCase() === key
  }

  return {
    clipboardVisible,
    clipboardState,
    localClipboardAvailable,
    remoteClipboardShortcutReadDelayMs,
    readLocalClipboard,
    writeLocalClipboard,
    requestRemoteClipboard,
    pushClipboardToRemote,
    copyRemoteClipboardToLocal,
    openClipboardDialog,
    scheduleRemoteClipboardPull,
    isClipboardShortcut
  }
}
