<template>
  <div class="remote-shell-terminal">
    <div ref="termContainer" class="term-container"></div>
    <div class="term-status">
      <span :class="['dot', pendingId ? 'busy' : 'idle']"></span>
      <span>{{ pendingId ? '命令执行中（Ctrl+C 终止）' : '就绪' }}</span>
      <span class="sep">·</span>
      <span>单条命令超时 {{ timeoutSeconds }}s</span>
      <span class="sep">·</span>
      <span>输出上限 1MB</span>
      <span class="sep">·</span>
      <span class="audit-hint">所有命令将被审计记录</span>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const props = defineProps({
  send: { type: Function, required: true },
  connected: { type: Boolean, default: false },
  timeoutSeconds: { type: Number, default: 60 }
})

const termContainer = ref(null)
const pendingId = ref(null)

let term = null
let fitAddon = null
let resizeObserver = null
let seq = 0
let inputBuffer = ''
let history = []
let historyIndex = 0
let currentCwd = ''
let bannerPrinted = false

const writeText = (text) => {
  term?.write(text.replace(/\r?\n/g, '\r\n'))
}

const promptText = () => `PS ${currentCwd || ''}> `

const printPrompt = () => {
  term?.write(`\r\n${promptText()}`)
}

const printBanner = () => {
  if (bannerPrinted) return
  bannerPrinted = true
  writeText(
    'Z-View 远程终端 (PowerShell)\r\n' +
    '提示：每条命令独立执行，跨命令不保留工作目录/变量；Ctrl+C 终止当前命令。\r\n' +
    '安全提示：所有命令将被审计记录。\r\n'
  )
  printPrompt()
}

const ensurePrompt = () => {
  // 引擎首条 shell 消息可能早于终端打开（历史丢弃）——兜底补提示符
  if (!bannerPrinted) printBanner()
}

const backspace = () => {
  if (!inputBuffer.length) return
  inputBuffer = inputBuffer.slice(0, -1)
  // 整行重绘：避免 CJK 宽字符下的光标列计算误差
  term.write(`\r\x1b[K${promptText()}${inputBuffer}`)
}

const cancelInput = () => {
  term.write('^C')
  inputBuffer = ''
  printPrompt()
}

const submitCommand = () => {
  const command = inputBuffer.trim()
  term.write('\r\n')
  inputBuffer = ''
  if (!command) {
    printPrompt()
    return
  }
  if (history[history.length - 1] !== command) {
    history.push(command)
    if (history.length > 100) history.shift()
  }
  historyIndex = history.length
  seq += 1
  const id = `sh-${Date.now()}-${seq}`
  pendingId.value = id
  const sent = props.send({ type: 'shell_exec', id, command })
  if (!sent) {
    pendingId.value = null
    writeText('[发送失败：远程桌面未连接]')
    printPrompt()
  }
}

const historyPrev = () => {
  if (!history.length || historyIndex <= 0) return
  historyIndex -= 1
  inputBuffer = history[historyIndex] || ''
  term.write(`\r\x1b[K${promptText()}${inputBuffer}`)
}

const historyNext = () => {
  if (historyIndex >= history.length) return
  historyIndex += 1
  inputBuffer = historyIndex === history.length ? '' : (history[historyIndex] || '')
  term.write(`\r\x1b[K${promptText()}${inputBuffer}`)
}

const handleData = (data) => {
  if (!term) return
  if (!props.connected) return
  if (pendingId.value) {
    if (data === '\x03') {
      props.send({ type: 'shell_stop', id: pendingId.value })
    }
    return
  }
  if (data === '\x1b[A') {
    historyPrev()
    return
  }
  if (data === '\x1b[B') {
    historyNext()
    return
  }
  let pending = ''
  const flush = () => {
    if (pending) {
      inputBuffer += pending
      term.write(pending)
      pending = ''
    }
  }
  for (const ch of data) {
    if (ch === '\r') {
      flush()
      submitCommand()
    } else if (ch === '\x7f') {
      flush()
      backspace()
    } else if (ch === '\x03') {
      flush()
      cancelInput()
    } else if (ch === '\t') {
      // 基础版不支持补全，忽略
    } else if (ch.charCodeAt(0) >= 32) {
      pending += ch
    }
  }
  flush()
}

const handleEngineMessage = (message) => {
  if (!term) return
  ensurePrompt()
  const type = message?.type
  if (type === 'shell_output') {
    if (typeof message.data === 'string' && message.data.length) {
      writeText(message.data)
    }
    return
  }
  if (type === 'shell_exit') {
    const wasPending = pendingId.value && message.id === pendingId.value
    if (message.cwd) currentCwd = String(message.cwd)
    if (wasPending) pendingId.value = null
    if (wasPending) {
      if (message.timed_out) {
        writeText(`\r\n[命令超时（>${props.timeoutSeconds}s），已强制终止]`)
      }
      printPrompt()
    }
    return
  }
  if (type === 'shell_error') {
    const isPending = message.id && message.id === pendingId.value
    if (message.code === 'not_running' && !isPending) return
    if (isPending) pendingId.value = null
    writeText(`\r\n[错误] ${message.message || '命令执行失败'}`)
    if (isPending) printPrompt()
  }
}

const fitTerminal = () => {
  if (!term || !fitAddon) return
  try {
    fitAddon.fit()
  } catch (e) {
    // 容器尺寸为 0 时忽略
  }
}

const focus = () => {
  term?.focus()
}

watch(() => props.connected, (value) => {
  if (!term) return
  if (!value) {
    if (pendingId.value) pendingId.value = null
    writeText('\r\n[远程连接已断开，重连后请重新输入]')
    printPrompt()
  } else {
    writeText('\r\n[远程连接已恢复]')
    printPrompt()
  }
})

onMounted(() => {
  term = new Terminal({
    fontSize: 13,
    fontFamily: 'Consolas, "Courier New", monospace',
    cursorBlink: true,
    scrollback: 5000,
    theme: {
      background: '#16181d',
      foreground: '#d4d4d4',
      cursor: '#4fc1ff'
    }
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termContainer.value)
  fitTerminal()
  term.onData(handleData)
  term.attachCustomKeyEventHandler((event) => {
    // 阻止浏览器快捷键冲突（Ctrl+C 复制场景：有选中文本时放行复制，否则作为中断发送）
    if (event.type === 'keydown' && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
      return term.hasSelection()
    }
    return true
  })
  resizeObserver = new ResizeObserver(() => fitTerminal())
  resizeObserver.observe(termContainer.value)
  window.addEventListener('resize', fitTerminal)
  printBanner()
  focus()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', fitTerminal)
  resizeObserver?.disconnect()
  resizeObserver = null
  term?.dispose()
  term = null
  fitAddon = null
})

defineExpose({ handleEngineMessage, focus })
</script>

<style scoped>
.remote-shell-terminal {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.term-container {
  flex: 1;
  min-height: 0;
  padding: 4px 6px;
  background: #16181d;
  border-radius: 6px;
}

.term-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 2px 0;
  font-size: 12px;
  color: #909399;
}

.term-status .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.term-status .dot.idle {
  background: #67c23a;
}

.term-status .dot.busy {
  background: #e6a23c;
  animation: shell-blink 1s infinite;
}

.term-status .sep {
  color: #c0c4cc;
}

.term-status .audit-hint {
  color: #e6a23c;
}

@keyframes shell-blink {
  50% {
    opacity: 0.35;
  }
}
</style>
