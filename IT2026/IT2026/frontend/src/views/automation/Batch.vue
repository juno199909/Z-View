<template>
  <div class="app-container">
    <el-card>
      <template #header>
        <h2>批量操作</h2>
      </template>

      <!-- 选择终端 -->
      <div class="section">
        <h3>1. 选择目标终端</h3>
        <el-table
          ref="multipleTable"
          :data="terminals"
          @selection-change="handleSelectionChange"
          v-loading="loading"
        >
          <el-table-column type="selection" width="55" />
          <el-table-column label="主机名" prop="hostname" width="150" />
          <el-table-column label="IP地址" prop="ip_address" width="150" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'online' ? 'success' : 'danger'" size="small">
                {{ row.status === 'online' ? '在线' : '离线' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作系统" prop="os_type" width="120" />
          <el-table-column label="CPU" width="80">
            <template #default="{ row }">
              {{ row.cpu_usage ? row.cpu_usage + '%' : '-' }}
            </template>
          </el-table-column>
          <el-table-column label="内存" width="80">
            <template #default="{ row }">
              {{ row.memory_usage ? row.memory_usage + '%' : '-' }}
            </template>
          </el-table-column>
        </el-table>
        <div style="margin-top: 10px;">
          <el-tag type="info">已选择 {{ selectedTerminals.length }} 台终端</el-tag>
        </div>
      </div>

      <!-- 选择操作 -->
      <div class="section">
        <h3>2. 选择操作类型</h3>
        <el-radio-group v-model="operationType">
          <el-radio value="command">执行命令</el-radio>
          <el-radio value="restart">重启终端</el-radio>
          <el-radio value="shutdown">关闭终端</el-radio>
          <el-radio value="software">安装软件</el-radio>
          <el-radio value="script">执行脚本</el-radio>
        </el-radio-group>
      </div>

      <!-- 操作参数 -->
      <div class="section">
        <h3>3. 操作参数</h3>

        <!-- 执行命令 -->
        <div v-if="operationType === 'command'">
          <el-input
            v-model="commandText"
            type="textarea"
            :rows="4"
            placeholder="输入要执行的命令，例如: ipconfig /all"
          />
          <div style="margin-top: 10px; color: #909399; font-size: 12px;">
            提示：命令将在所有选中的终端上执行
          </div>
        </div>

        <!-- 重启终端 -->
        <div v-if="operationType === 'restart'">
          <el-alert type="warning" :closable="false">
            <template #title>
              <div>
                <strong>警告：</strong>此操作将重启所有选中的终端，可能导致正在运行的程序中断
              </div>
            </template>
          </el-alert>
          <el-input
            v-model="restartDelay"
            type="number"
            placeholder="延迟秒数（0表示立即重启）"
            style="width: 300px; margin-top: 10px;"
          >
            <template #prepend>延迟</template>
            <template #append>秒</template>
          </el-input>
        </div>

        <!-- 关闭终端 -->
        <div v-if="operationType === 'shutdown'">
          <el-alert type="warning" :closable="false">
            <template #title>
              <div>
                <strong>警告：</strong>此操作将关闭所有选中的终端，可能导致未保存的数据丢失
              </div>
            </template>
          </el-alert>
          <el-input
            v-model="shutdownDelay"
            type="number"
            placeholder="延迟秒数（0表示立即关机）"
            style="width: 300px; margin-top: 10px;"
          >
            <template #prepend>延迟</template>
            <template #append>秒</template>
          </el-input>
        </div>

        <!-- 安装软件 -->
        <div v-if="operationType === 'software'">
          <el-input
            v-model="softwareUrl"
            placeholder="软件下载地址（URL）"
            style="margin-bottom: 10px;"
          />
          <el-input
            v-model="installCommand"
            placeholder="安装命令，例如: msiexec /i software.msi /quiet"
          />
        </div>

        <!-- 执行脚本 -->
        <div v-if="operationType === 'script'">
          <el-input
            v-model="scriptContent"
            type="textarea"
            :rows="8"
            placeholder="输入PowerShell或Bash脚本内容"
          />
        </div>
      </div>

      <!-- 执行按钮 -->
      <div class="section">
        <el-button
          type="primary"
          size="large"
          :disabled="selectedTerminals.length === 0"
          :loading="executing"
          @click="executeOperation"
        >
          <el-icon><Check /></el-icon>
          执行操作
        </el-button>
        <el-button size="large" @click="resetForm">
          <el-icon><Refresh /></el-icon>
          重置
        </el-button>
      </div>
    </el-card>

    <!-- 执行历史 -->
    <el-card style="margin-top: 20px;">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <h3>执行历史</h3>
          <el-button :icon="Refresh" @click="loadHistory">刷新</el-button>
        </div>
      </template>

      <el-table :data="history" v-loading="historyLoading">
        <el-table-column label="操作类型" width="120">
          <template #default="{ row }">
            <el-tag size="small">{{ getOperationTypeText(row.operation_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="目标数量" width="100" align="center">
          <template #default="{ row }">
            {{ row.target_count }}
          </template>
        </el-table-column>
        <el-table-column label="成功" width="80" align="center">
          <template #default="{ row }">
            <span style="color: #67c23a;">{{ row.success_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="失败" width="80" align="center">
          <template #default="{ row }">
            <span style="color: #f56c6c;">{{ row.failed_count }}</span>
          </template>
        </el-table-column>
        <el-table-column label="命令/参数" min-width="300" show-overflow-tooltip>
          <template #default="{ row }">
            {{ row.parameters }}
          </template>
        </el-table-column>
        <el-table-column label="执行时间" width="160">
          <template #default="{ row }">
            {{ row.created_at }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button type="primary" link size="small" @click="viewDetail(row)">
              查看详情
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="detailDialogVisible"
      title="执行详情"
      width="760px"
      destroy-on-close
    >
      <div v-loading="detailLoading" class="detail-list">
        <div
          v-for="item in detailResults"
          :key="item.id"
          class="detail-item"
        >
          <div class="detail-item__header">
            <div>
              <strong>{{ item.hostname || '-' }}</strong>
              <span class="detail-item__meta">({{ item.ip_address || '-' }})</span>
            </div>
            <el-tag :type="item.status === 'success' ? 'success' : 'danger'" size="small">
              {{ item.status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </div>
          <div class="detail-item__time">执行时间: {{ item.executed_at || '-' }}</div>
          <pre class="detail-item__output">{{ item.output || '无输出' }}</pre>
        </div>
        <el-empty v-if="!detailLoading && detailResults.length === 0" description="暂无执行结果" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Refresh } from '@element-plus/icons-vue'
import { getAssetList } from '@/api/asset'
import {
  executeBatchOperation,
  getBatchHistory,
  getBatchOperationResults
} from '@/api/batch'

const loading = ref(false)
const executing = ref(false)
const historyLoading = ref(false)
const detailLoading = ref(false)
const terminals = ref([])
const selectedTerminals = ref([])
const operationType = ref('command')
const commandText = ref('')
const restartDelay = ref(0)
const shutdownDelay = ref(0)
const softwareUrl = ref('')
const installCommand = ref('')
const scriptContent = ref('')
const history = ref([])
const detailDialogVisible = ref(false)
const detailResults = ref([])
const multipleTable = ref(null)
const ASSET_FETCH_PAGE_SIZE = 100

const loadTerminals = async () => {
  try {
    loading.value = true
    const firstPage = await getAssetList({ page: 1, page_size: ASSET_FETCH_PAGE_SIZE })
    const total = Number(firstPage.total || 0)
    const allTerminals = [...(firstPage.data || [])]
    const totalPages = Math.ceil(total / ASSET_FETCH_PAGE_SIZE)

    if (totalPages > 1) {
      const remainingRequests = []
      for (let page = 2; page <= totalPages; page += 1) {
        remainingRequests.push(
          getAssetList({ page, page_size: ASSET_FETCH_PAGE_SIZE })
        )
      }

      const remainingPages = await Promise.all(remainingRequests)
      remainingPages.forEach((pageData) => {
        allTerminals.push(...(pageData.data || []))
      })
    }

    terminals.value = allTerminals
  } catch (error) {
    console.error('加载终端列表失败:', error)
    ElMessage.error('加载终端列表失败')
  } finally {
    loading.value = false
  }
}

const handleSelectionChange = (selection) => {
  selectedTerminals.value = selection
}

const validateParameters = () => {
  if (operationType.value === 'command' && !commandText.value.trim()) {
    ElMessage.warning('请输入要执行的命令')
    return false
  }

  if (operationType.value === 'software') {
    if (!softwareUrl.value.trim()) {
      ElMessage.warning('请输入软件下载地址')
      return false
    }
  }

  if (operationType.value === 'script' && !scriptContent.value.trim()) {
    ElMessage.warning('请输入要执行的脚本内容')
    return false
  }

  return true
}

const executeOperation = async () => {
  if (selectedTerminals.value.length === 0) {
    ElMessage.warning('请先选择目标终端')
    return
  }

  if (!validateParameters()) {
    return
  }

  try {
    await ElMessageBox.confirm(
      `确认对 ${selectedTerminals.value.length} 台终端执行操作？`,
      '确认操作',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    executing.value = true

    // 构建参数
    const parameters = {}
    if (operationType.value === 'command') {
      parameters.command = commandText.value.trim()
    } else if (operationType.value === 'restart') {
      parameters.delay = Number(restartDelay.value) || 0
    } else if (operationType.value === 'shutdown') {
      parameters.delay = Number(shutdownDelay.value) || 0
    } else if (operationType.value === 'software') {
      parameters.url = softwareUrl.value.trim()
      parameters.install_command = installCommand.value.trim()
    } else if (operationType.value === 'script') {
      parameters.script = scriptContent.value
    }

    // 调用后端API执行批量操作
    const response = await executeBatchOperation({
      operation_type: operationType.value,
      terminal_ids: selectedTerminals.value.map(t => t.id),
      parameters
    })

    ElMessage.success(`操作已完成，成功 ${response.success_count} 台，失败 ${response.failed_count} 台`)

    // 刷新历史记录
    await loadHistory()
    resetForm()

  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      console.error('执行失败:', error)
      ElMessage.error('执行失败: ' + (error.response?.data?.detail || error.message))
    }
  } finally {
    executing.value = false
  }
}

const resetForm = () => {
  commandText.value = ''
  restartDelay.value = 0
  shutdownDelay.value = 0
  softwareUrl.value = ''
  installCommand.value = ''
  scriptContent.value = ''
  selectedTerminals.value = []
  multipleTable.value?.clearSelection()
}

const loadHistory = async () => {
  historyLoading.value = true
  try {
    const response = await getBatchHistory({ page: 1, page_size: 20 })
    history.value = response.data || []
  } catch (error) {
    console.error('加载历史失败:', error)
  } finally {
    historyLoading.value = false
  }
}

const viewDetail = async (row) => {
  try {
    detailDialogVisible.value = true
    detailLoading.value = true
    const response = await getBatchOperationResults(row.id)
    detailResults.value = response.data || []
  } catch (error) {
    console.error('加载详情失败:', error)
    ElMessage.error('加载详情失败')
    detailDialogVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

const getOperationTypeText = (type) => {
  const map = {
    command: '执行命令',
    restart: '重启终端',
    shutdown: '关闭终端',
    software: '安装软件',
    script: '执行脚本'
  }
  return map[type] || type
}

onMounted(() => {
  loadTerminals()
  loadHistory()
})
</script>

<style scoped>
.section {
  margin-bottom: 30px;
  padding-bottom: 20px;
  border-bottom: 1px solid #ebeef5;
}

.section:last-of-type {
  border-bottom: none;
}

h3 {
  margin-bottom: 15px;
  color: #303133;
}

.detail-list {
  max-height: 520px;
  overflow-y: auto;
}

.detail-item {
  margin-bottom: 14px;
  padding: 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fff;
}

.detail-item:last-child {
  margin-bottom: 0;
}

.detail-item__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.detail-item__meta,
.detail-item__time {
  color: #909399;
  font-size: 12px;
}

.detail-item__time {
  margin-top: 6px;
}

.detail-item__output {
  margin: 10px 0 0;
  padding: 10px;
  border-radius: 6px;
  background: #f5f7fa;
  color: #303133;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
