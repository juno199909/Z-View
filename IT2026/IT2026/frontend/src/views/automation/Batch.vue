<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">批量操作</h2>
        <div class="zv-page-subtitle">已选 <strong>{{ selectedTerminals.length }}</strong> 台终端 · 可执行命令 / 重启 / 关机 / 安装软件 / 执行脚本</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="RefreshLeft" @click="resetForm">重置</el-button>
        <el-button type="primary" :icon="Check" :disabled="selectedTerminals.length === 0" :loading="executing" @click="executeOperation">执行操作</el-button>
      </div>
    </div>

    <div class="zv-batch-grid">
      <!-- 左侧：选终端 -->
      <div class="zv-card zv-card-flex">
        <div class="zv-card-head">
          <div>
            <div class="zv-card-title">① 选择目标终端</div>
            <div class="zv-card-subtitle">双击或勾选要操作的终端</div>
          </div>
          <el-tag :type="selectedTerminals.length ? 'primary' : 'info'" effect="light" size="small">
            {{ selectedTerminals.length }} / {{ terminals.length }}
          </el-tag>
        </div>
        <el-table ref="batchTableRef" v-loading="loading" :data="terminals" @selection-change="handleSelectionChange" @row-dblclick="(row) => batchTableRef?.toggleRowSelection(row)" max-height="540" :row-style="{ cursor: 'pointer' }">
          <el-table-column type="selection" width="44" />
          <el-table-column label="主机" min-width="180">
            <template #default="{ row }">
              <div class="zv-host-cell">
                <div class="zv-host-name">{{ row.hostname || '-' }}</div>
                <div class="zv-host-ip">{{ row.ip_address || '-' }}</div>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <span class="zv-status-chip" :class="`is-${row.status}`">
                <span class="zv-status-dot" :class="`is-${row.status}`" />
                {{ row.status === 'online' ? '在线' : row.status === 'offline' ? '离线' : '未知' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="系统" prop="os_type" width="100" />
        </el-table>
      </div>

      <!-- 右侧：操作配置 -->
      <div class="zv-card zv-card-flex">
        <div class="zv-card-head">
          <div>
            <div class="zv-card-title">② 配置操作</div>
            <div class="zv-card-subtitle">选择操作类型并填写参数</div>
          </div>
        </div>

        <div class="zv-op-types">
          <div v-for="op in operationTypes" :key="op.value"
            class="zv-op-type"
            :class="{ 'is-active': operationType === op.value }"
            @click="operationType = op.value"
          >
            <div class="zv-op-icon" :style="{ background: op.gradient }">
              <el-icon :size="20"><component :is="op.icon" /></el-icon>
            </div>
            <div class="zv-op-label">{{ op.label }}</div>
          </div>
        </div>

        <div class="zv-op-config">
          <div v-if="operationType === 'command'">
            <h4 class="zv-op-title">执行命令</h4>
            <el-input v-model="commandText" type="textarea" :rows="4" placeholder="例如：ipconfig /all" />
            <div class="zv-op-tip">提示：命令将在所有选中的终端上并发执行</div>
          </div>

          <div v-if="operationType === 'restart'">
            <h4 class="zv-op-title">重启终端</h4>
            <el-alert type="warning" :closable="false" show-icon>
              <template #title>此操作将重启所有选中的终端，可能导致正在运行的程序中断</template>
            </el-alert>
            <el-input v-model="restartDelay" type="number" placeholder="0 表示立即" style="margin-top: 12px;">
              <template #prepend>延迟</template>
              <template #append>秒</template>
            </el-input>
          </div>

          <div v-if="operationType === 'shutdown'">
            <h4 class="zv-op-title">关闭终端</h4>
            <el-alert type="warning" :closable="false" show-icon>
              <template #title>此操作将关闭所有选中的终端，可能导致未保存的数据丢失</template>
            </el-alert>
            <el-input v-model="shutdownDelay" type="number" placeholder="0 表示立即" style="margin-top: 12px;">
              <template #prepend>延迟</template>
              <template #append>秒</template>
            </el-input>
          </div>

          <div v-if="operationType === 'software'">
            <h4 class="zv-op-title">安装软件</h4>
            <el-input v-model="softwareUrl" placeholder="软件下载地址（URL）" style="margin-bottom: 12px;" />
            <el-input v-model="installCommand" placeholder="安装命令，例如 msiexec /i software.msi /quiet" />
          </div>

          <div v-if="operationType === 'script'">
            <h4 class="zv-op-title">执行脚本</h4>
            <el-input v-model="scriptContent" type="textarea" :rows="8" placeholder="输入 PowerShell 或 Bash 脚本内容" />
          </div>
        </div>
      </div>
    </div>

    <!-- 执行历史 -->
    <div class="zv-card">
      <div class="zv-card-head">
        <div>
          <div class="zv-card-title">执行历史</div>
          <div class="zv-card-subtitle">最近批量操作记录</div>
        </div>
        <el-button text :icon="Refresh" @click="loadHistory">刷新</el-button>
      </div>
      <el-table :data="history" v-loading="historyLoading">
        <el-table-column label="操作类型" width="120">
          <template #default="{ row }">
            <el-tag size="small" effect="light" type="primary">{{ getOperationTypeText(row.operation_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="目标" width="100" align="center">
          <template #default="{ row }">{{ row.target_count }}</template>
        </el-table-column>
        <el-table-column label="成功" width="80" align="center">
          <template #default="{ row }"><span class="zv-num-success">{{ row.success_count }}</span></template>
        </el-table-column>
        <el-table-column label="失败" width="80" align="center">
          <template #default="{ row }"><span class="zv-num-danger">{{ row.failed_count }}</span></template>
        </el-table-column>
        <el-table-column label="命令/参数" min-width="300" show-overflow-tooltip>
          <template #default="{ row }"><span class="zv-mono">{{ row.parameters }}</span></template>
        </el-table-column>
        <el-table-column label="执行时间" width="170">
          <template #default="{ row }"><span class="zv-mono">{{ row.created_at }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="100" align="right">
          <template #default="{ row }">
            <el-button text type="primary" size="small" @click="viewDetail(row)">详情</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无执行历史" :image-size="80" /></template>
      </el-table>
    </div>

    <el-dialog v-model="detailDialogVisible" title="执行详情" width="760px" destroy-on-close>
      <div v-loading="detailLoading" class="zv-detail-list">
        <div v-for="item in detailResults" :key="item.id" class="zv-detail-item">
          <div class="zv-detail-head">
            <div>
              <strong>{{ item.hostname || '-' }}</strong>
              <span class="zv-detail-meta">({{ item.ip_address || '-' }})</span>
            </div>
            <el-tag :type="item.status === 'success' ? 'success' : 'danger'" size="small" effect="light">
              {{ item.status === 'success' ? '成功' : '失败' }}
            </el-tag>
          </div>
          <div class="zv-detail-time">执行时间: {{ item.executed_at || '-' }}</div>
          <pre class="zv-detail-output">{{ item.output || '无输出' }}</pre>
        </div>
        <el-empty v-if="!detailLoading && detailResults.length === 0" description="暂无执行结果" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check, Refresh, RefreshLeft, Document, SwitchButton, SwitchButton as Power, Box, Cpu, Operation } from '@element-plus/icons-vue'
import { getAssetList } from '@/api/asset'
import { executeBatchOperation, getBatchHistory, getBatchOperationResults } from '@/api/batch'

const loading = ref(false)
const executing = ref(false)
const historyLoading = ref(false)
const detailLoading = ref(false)
const terminals = ref([])
const selectedTerminals = ref([])
const batchTableRef = ref(null)
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
const currentHistoryId = ref(null)

const operationTypes = [
  { value: 'command',  label: '执行命令', icon: Document,     gradient: 'linear-gradient(135deg, #3b82f6, #2563eb)' },
  { value: 'restart',  label: '重启终端', icon: SwitchButton, gradient: 'linear-gradient(135deg, #f59e0b, #d97706)' },
  { value: 'shutdown', label: '关闭终端', icon: Power,        gradient: 'linear-gradient(135deg, #ef4444, #dc2626)' },
  { value: 'software', label: '安装软件', icon: Box,          gradient: 'linear-gradient(135deg, #10b981, #059669)' },
  { value: 'script',   label: '执行脚本', icon: Cpu,          gradient: 'linear-gradient(135deg, #8b5cf6, #7c3aed)' }
]

const loadTerminals = async () => {
  loading.value = true
  try {
    const res = await getAssetList({ page: 1, page_size: 100 })
    terminals.value = res.data || []
  } catch (error) {
    console.error('加载终端失败:', error)
  } finally {
    loading.value = false
  }
}

const loadHistory = async () => {
  historyLoading.value = true
  try {
    const res = await getBatchHistory({ page: 1, page_size: 20 })
    history.value = res.data || []
  } catch (error) {
    console.error('加载历史失败:', error)
  } finally {
    historyLoading.value = false
  }
}

const handleSelectionChange = (rows) => { selectedTerminals.value = rows }

const getOperationTypeText = (t) => ({ command: '命令', restart: '重启', shutdown: '关机', software: '软件', script: '脚本' }[t] || t)

const executeOperation = async () => {
  if (selectedTerminals.value.length === 0) {
    ElMessage.warning('请先选择终端')
    return
  }
  try {
    const opText = operationTypes.find(o => o.value === operationType.value).label
    await ElMessageBox.confirm(
      `确定要对 ${selectedTerminals.value.length} 台终端执行「${opText}」操作吗？`,
      '执行确认',
      { type: 'warning', confirmButtonText: '执行', cancelButtonText: '取消' }
    )
  } catch (error) {
    if (error === 'cancel') return
  }

  executing.value = true
  try {
    const params = {
      terminal_ids: selectedTerminals.value.map(t => t.id),
      operation_type: operationType.value,
      parameters: {}
    }
    if (operationType.value === 'command') params.parameters.command = commandText.value
    if (operationType.value === 'restart') params.parameters.delay = restartDelay.value
    if (operationType.value === 'shutdown') params.parameters.delay = shutdownDelay.value
    if (operationType.value === 'software') { params.parameters.url = softwareUrl.value; params.parameters.install_command = installCommand.value }
    if (operationType.value === 'script') params.parameters.script = scriptContent.value

    await executeBatchOperation(params)
    ElMessage.success('操作已下发')
    loadHistory()
  } catch (error) {
    ElMessage.error('操作下发失败')
  } finally {
    executing.value = false
  }
}

const resetForm = () => {
  selectedTerminals.value = []
  // 同步清空表格勾选框，避免 UI 与状态脱节
  batchTableRef.value?.clearSelection?.()
  operationType.value = 'command'
  commandText.value = ''
  restartDelay.value = 0
  shutdownDelay.value = 0
  softwareUrl.value = ''
  installCommand.value = ''
  scriptContent.value = ''
}

const viewDetail = async (row) => {
  currentHistoryId.value = row.id
  detailDialogVisible.value = true
  detailLoading.value = true
  try {
    const res = await getBatchOperationResults(row.id)
    detailResults.value = res.data || []
  } catch (error) {
    ElMessage.error('加载详情失败')
  } finally {
    detailLoading.value = false
  }
}

onMounted(() => { loadTerminals(); loadHistory() })
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1600px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-batch-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
  margin-bottom: 16px;

  @media (max-width: 1100px) {
    grid-template-columns: 1fr;
  }
}

.zv-card-flex {
  display: flex;
  flex-direction: column;
  min-height: 600px;
}

.zv-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 22px;
  border-bottom: 1px solid $border-color-light;
  background: $slate-50;
}

.zv-card-title { font-size: 15px; font-weight: 600; color: $text-primary; }
.zv-card-subtitle { font-size: 12px; color: $text-tertiary; margin-top: 2px; }

// ---- 操作类型选择 ----
.zv-op-types {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  padding: 18px 22px;
  border-bottom: 1px solid $border-color-light;
}

.zv-op-type {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 12px 8px;
  border-radius: $border-radius;
  border: 2px solid $border-color-light;
  cursor: pointer;
  transition: all $transition-base;

  &:hover {
    border-color: $brand-primary-100;
    background: $bg-hover;
  }

  &.is-active {
    border-color: $brand-primary;
    background: $brand-primary-50;
  }
}

.zv-op-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 4px 10px rgba(0, 0, 0, 0.10);
}

.zv-op-label {
  font-size: 12px;
  font-weight: 500;
  color: $text-primary;
}

// ---- 操作配置 ----
.zv-op-config {
  padding: 18px 22px;
  flex: 1;
}

.zv-op-title {
  font-size: 14px;
  font-weight: 600;
  color: $text-primary;
  margin: 0 0 12px 0;
}

.zv-op-tip {
  font-size: 12px;
  color: $text-tertiary;
  margin-top: 8px;
}

// ---- 单元格 ----
.zv-host-cell { line-height: 1.3; }
.zv-host-name { font-size: 13px; font-weight: 600; color: $text-primary; }
.zv-host-ip { font-size: 11px; color: $text-tertiary; font-family: $font-mono; margin-top: 2px; }

.zv-status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: $text-secondary;
  &.is-online  { color: $success-color; }
  &.is-offline { color: $danger-color; }
}
.zv-status-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

.zv-mono { font-family: $font-mono; font-size: 12px; color: $text-secondary; }
.zv-num-success { color: $success-color; font-weight: 600; font-family: $font-mono; }
.zv-num-danger  { color: $danger-color;  font-weight: 600; font-family: $font-mono; }

// ---- 详情 ----
.zv-detail-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.zv-detail-item {
  border: 1px solid $border-color-light;
  border-radius: $border-radius;
  padding: 14px 16px;
}

.zv-detail-head {
  display: flex;
  justify-content: space-between;
  margin-bottom: 6px;
}

.zv-detail-meta { color: $text-tertiary; font-size: 12px; margin-left: 8px; }
.zv-detail-time { font-size: 11px; color: $text-tertiary; margin-bottom: 8px; }
.zv-detail-output {
  background: $slate-50;
  padding: 10px 12px;
  border-radius: $border-radius;
  font-family: $font-mono;
  font-size: 12px;
  color: $text-primary;
  margin: 0;
  white-space: pre-wrap;
  max-height: 200px;
  overflow-y: auto;
}

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell { background: #fafbfc; color: $text-secondary; font-weight: 600; font-size: 12px; }
  tr:hover > td.el-table__cell { background: rgba(37, 99, 235, 0.03) !important; }
  td.el-table__cell { border-bottom: 1px solid $slate-100 !important; }
  .el-table__inner-wrapper::before { height: 0; }
}
</style>
