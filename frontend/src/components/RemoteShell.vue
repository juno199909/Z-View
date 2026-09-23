<template>
  <el-dialog
    v-model="visible"
    title="远程终端"
    width="80%"
    :close-on-click-modal="false"
    @close="handleClose"
  >
    <div class="shell-container">
      <!-- 输出区域 -->
      <div class="output-area" ref="outputArea">
        <div v-for="(item, index) in history" :key="index" class="command-block">
          <!-- 命令行 -->
          <div class="command-line">
            <span class="prompt">{{ hostname }}$</span>
            <span class="command">{{ item.command }}</span>
          </div>

          <!-- 输出 -->
          <div v-if="item.stdout" class="stdout">{{ item.stdout }}</div>
          <div v-if="item.stderr" class="stderr">{{ item.stderr }}</div>
          <div v-if="item.error" class="error">❌ {{ item.error }}</div>
        </div>

        <!-- 加载中 -->
        <div v-if="loading" class="loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          执行中...
        </div>
      </div>

      <!-- 输入区域 -->
      <div class="input-area">
        <span class="prompt">{{ hostname }}$</span>
        <el-input
          v-model="command"
          placeholder="输入命令并按回车执行（如: ipconfig, dir, tasklist）"
          @keyup.enter="executeCommand"
          :disabled="loading"
          ref="commandInput"
        />
        <el-button type="primary" @click="executeCommand" :loading="loading" :icon="Promotion">
          执行
        </el-button>
      </div>

      <!-- 快捷命令 -->
      <div class="quick-commands">
        <el-tag
          v-for="cmd in quickCommands"
          :key="cmd"
          @click="command = cmd"
          style="cursor: pointer; margin-right: 10px;"
        >
          {{ cmd }}
        </el-tag>
      </div>
    </div>

    <template #footer>
      <el-button @click="clearHistory">清空历史</el-button>
      <el-button type="info" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, Promotion } from '@element-plus/icons-vue'
import { executeAssetCommand } from '@/api/asset'

const props = defineProps({
  modelValue: Boolean,
  assetId: {
    type: [String, Number],
    default: null
  },
  ipAddress: String,
  hostname: String
})

const emit = defineEmits(['update:modelValue'])

const visible = ref(false)
const command = ref('')
const history = ref([])
const loading = ref(false)
const outputArea = ref(null)
const commandInput = ref(null)

// 快捷命令
const quickCommands = [
  'ipconfig',
  'tasklist',
  'netstat -ano',
  'systeminfo',
  'dir',
  'whoami',
  'hostname'
]

watch(() => props.modelValue, (val) => {
  visible.value = val
  if (val) {
    nextTick(() => {
      commandInput.value?.focus()
    })
  }
})

watch(visible, (val) => {
  emit('update:modelValue', val)
})

const executeCommand = async () => {
  if (!command.value.trim()) {
    ElMessage.warning('请输入命令')
    return
  }
  if (!props.assetId) {
    ElMessage.error('资产ID不可用，无法执行命令')
    return
  }

  loading.value = true
  const currentCommand = command.value.trim()

  try {
    const result = await executeAssetCommand(props.assetId, {
      command: currentCommand
    })

    history.value.push({
      command: currentCommand,
      stdout: result.stdout || '',
      stderr: result.stderr || '',
      error: result.error || '',
      returncode: result.returncode,
      timestamp: new Date()
    })

    // 清空输入
    command.value = ''

    // 滚动到底部
    nextTick(() => {
      if (outputArea.value) {
        outputArea.value.scrollTop = outputArea.value.scrollHeight
      }
    })

  } catch (error) {
    history.value.push({
      command: currentCommand,
      error: error.response?.data?.error || error.message || '连接失败',
      timestamp: new Date()
    })
  } finally {
    loading.value = false
  }
}

const clearHistory = () => {
  history.value = []
}

const handleClose = () => {
  visible.value = false
}
</script>

<style scoped>
.shell-container {
  display: flex;
  flex-direction: column;
  height: 500px;
}

.output-area {
  flex: 1;
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 15px;
  overflow-y: auto;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
  font-size: 14px;
  line-height: 1.6;
  border-radius: 4px;
  margin-bottom: 15px;
}

.command-block {
  margin-bottom: 15px;
}

.command-line {
  margin-bottom: 5px;
}

.prompt {
  color: #4ec9b0;
  font-weight: bold;
  margin-right: 8px;
}

.command {
  color: #dcdcaa;
}

.stdout {
  color: #d4d4d4;
  white-space: pre-wrap;
  margin-left: 20px;
}

.stderr {
  color: #f48771;
  white-space: pre-wrap;
  margin-left: 20px;
}

.error {
  color: #f44336;
  margin-left: 20px;
}

.loading {
  color: #67c23a;
}

.input-area {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}

.input-area .prompt {
  color: #4ec9b0;
  font-weight: bold;
  font-family: 'Consolas', 'Monaco', monospace;
}

.quick-commands {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
</style>
