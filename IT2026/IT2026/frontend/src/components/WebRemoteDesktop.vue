<template>
  <div class="web-remote-desktop">
    <el-dialog
      v-model="dialogVisible"
      title="Web远程桌面"
      width="90%"
      destroy-on-close
      append-to-body
      modal-class="web-remote-desktop-overlay"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :before-close="handleDialogBeforeClose"
      @closed="handleDialogClosed"
    >
      <div class="remote-container">
        <!-- 工具栏 -->
        <div class="toolbar">
          <el-space>
            <el-tag :type="connectionStatus === 'connected' ? 'success' : 'info'">
              {{ getStatusText() }}
            </el-tag>
            <el-tag size="small" type="warning" effect="plain">{{ transportType === 'udp' ? 'UDP' : 'TCP' }}</el-tag>
            <el-tag v-if="h264Active" size="small" type="success" effect="plain">H.264 {{ fps }}FPS</el-tag>
            <span class="info">{{ targetInfo }}</span>
            <el-button-group>
              <el-button size="small" :icon="FullScreen" @click="toggleFullscreen">
                全屏
              </el-button>
              <el-button size="small" :icon="Refresh" @click="reconnect">
                重连
              </el-button>
              <el-button size="small" :icon="DocumentCopy" @click="openClipboardDialog">
                剪贴板
              </el-button>
              <el-button size="small" :icon="FolderOpened" @click="openFileTransferDialog">
                文件传输
              </el-button>
              <el-button size="small" :icon="Setting" @click="showSettings">
                设置
              </el-button>
              <el-button size="small" type="danger" plain @click="handleClose">
                关闭
              </el-button>
            </el-button-group>
          </el-space>
        </div>

        <!-- 远程桌面画布 -->
        <div
          class="desktop-container"
          :class="{ 'is-fullscreen': isFullscreen }"
          ref="desktopContainer"
        >
          <el-button
            v-if="isFullscreen && !fullscreenToolbarVisible"
            class="fullscreen-toolbar-handle"
            size="small"
            type="primary"
            plain
            @click.stop="showFullscreenToolbar"
          >
            显示菜单
          </el-button>
          <div
            v-if="isFullscreen && fullscreenToolbarVisible"
            class="fullscreen-toolbar"
          >
            <el-space wrap>
              <el-tag size="small" :type="connectionStatus === 'connected' ? 'success' : 'info'">
                {{ getStatusText() }}
              </el-tag>
              <el-tag size="small" type="warning" effect="plain">{{ transportType === 'udp' ? 'UDP' : 'TCP' }}</el-tag>
              <el-tag v-if="h264Active" size="small" type="success" effect="plain">H.264 {{ fps }}FPS</el-tag>
              <span class="fullscreen-info">{{ targetInfo }}</span>
              <span class="fullscreen-field">桌面</span>
              <el-select
                :model-value="getDesktopResolutionValue(sessionSettings)"
                size="small"
                style="width: 150px"
                :teleported="false"
                popper-class="remote-fullscreen-select-popper"
                @change="handleFullscreenDesktopResolutionChange"
              >
                <el-option
                  v-for="option in getDesktopResolutionOptions(sessionSettings.desktopWidth, sessionSettings.desktopHeight)"
                  :key="`${option.width}x${option.height}`"
                  :label="option.label"
                  :value="`${option.width}x${option.height}`"
                />
              </el-select>
              <span class="fullscreen-field">预设</span>
              <el-select
                :model-value="sessionSettings.preset"
                size="small"
                style="width: 108px"
                :teleported="false"
                popper-class="remote-fullscreen-select-popper"
                @change="handleFullscreenPresetChange"
              >
                <el-option
                  v-for="option in presetOptions"
                  :key="option.value"
                  :label="option.label"
                  :value="option.value"
                />
              </el-select>
              <span class="fullscreen-field">推流</span>
              <el-select
                :model-value="sessionSettings.scalePercent"
                size="small"
                style="width: 170px"
                :disabled="h264Active"
                :teleported="false"
                popper-class="remote-fullscreen-select-popper"
                @change="handleFullscreenResolutionChange"
              >
                <el-option
                  v-for="option in getResolutionScaleOptions(sessionSettings.scalePercent)"
                  :key="option"
                  :label="getResolutionOptionLabel(option)"
                  :value="option"
                />
              </el-select>
              <span class="fullscreen-field">滚轮</span>
              <el-input-number
                :model-value="sessionSettings.wheelSpeed"
                size="small"
                style="width: 118px"
                :min="0.5"
                :max="3"
                :step="0.1"
                :precision="1"
                controls-position="right"
                @change="handleFullscreenWheelSpeedChange"
              />
              <span class="fullscreen-field">灵敏度</span>
              <el-input-number
                :model-value="sessionSettings.mouseSensitivity"
                size="small"
                style="width: 118px"
                :min="0.5"
                :max="2"
                :step="0.1"
                :precision="1"
                controls-position="right"
                @change="handleFullscreenMouseSensitivityChange"
              />
              <span class="fullscreen-field">重连</span>
              <el-switch
                :model-value="sessionSettings.autoReconnect"
                inline-prompt
                active-text="开"
                inactive-text="关"
                @change="handleFullscreenAutoReconnectChange"
              />
              <el-button size="small" text @click="showSettings">
                更多设置
              </el-button>
              <el-button size="small" text @click="openClipboardDialog">
                剪贴板
              </el-button>
              <el-button size="small" text @click="openFileTransferDialog">
                文件传输
              </el-button>
              <el-button size="small" text @click="hideFullscreenToolbar">
                隐藏菜单
              </el-button>
              <el-button size="small" :icon="FullScreen" @click="toggleFullscreen">
                退出全屏
              </el-button>
              <el-button size="small" type="danger" plain @click="handleClose">
                关闭远程桌面
              </el-button>
            </el-space>
          </div>

          <canvas
            ref="desktopCanvas"
            tabindex="0"
            :style="canvasDisplayStyle"
            @pointerdown="handlePointerDown"
            @pointermove="handlePointerMove"
            @pointerup="handlePointerUp"
            @pointercancel="handlePointerCancel"
            @lostpointercapture="handleLostPointerCapture"
            @wheel="handleWheel"
            @contextmenu="handleContextMenu"
            @keydown="handleKeyDown"
            @keyup="handleKeyUp"
          ></canvas>

          <!-- 连接中遮罩 -->
          <div v-if="connectionStatus === 'connecting'" class="connecting-mask">
            <el-icon class="is-loading" :size="50"><Loading /></el-icon>
            <p>正在连接到远程桌面...</p>
          </div>

          <!-- 错误提示 -->
          <div v-if="connectionStatus === 'error'" class="error-mask">
            <el-icon :size="50" color="#F56C6C"><CircleClose /></el-icon>
            <p>连接失败: {{ errorMessage }}</p>
            <el-button type="primary" @click="reconnect">重试</el-button>
          </div>

          <!-- 未连接提示 -->
          <div v-if="connectionStatus === 'disconnected'" class="disconnected-mask">
            <el-icon :size="50"><Monitor /></el-icon>
            <p>远程桌面已断开连接</p>
            <el-button type="primary" @click="connect">重新连接</el-button>
          </div>
        </div>

        <!-- 状态栏 -->
        <div class="statusbar">
          <el-space>
            <span>桌面分辨率: {{ desktopResolution }}</span>
            <span>推流分辨率: {{ streamResolution }}</span>
            <span>帧率: {{ fps }} FPS</span>
            <span>延迟: {{ latency }}ms</span>
            <span>流量: {{ bandwidth }}</span>
            <span>传输预设: {{ getPresetLabel(sessionSettings.preset) }}</span>
            <span>压缩质量: {{ sessionSettings.quality }}</span>
            <span>输出缩放: {{ sessionSettings.scalePercent }}%</span>
            <span>模式: {{ sessionSettings.adaptive ? '自适应' : '手动' }}</span>
            <span>滚轮: {{ sessionSettings.wheelSpeed.toFixed(1) }}x</span>
            <span>灵敏度: {{ sessionSettings.mouseSensitivity.toFixed(1) }}x</span>
            <span>重连: {{ sessionSettings.autoReconnect ? '开启' : '关闭' }}</span>
            <span v-if="sessionWarningText" class="warning-text">告警: {{ sessionWarningText }}</span>
          </el-space>
        </div>
      </div>

      <template #footer>
        <el-button @click="handleClose">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="settingsVisible"
      title="远程桌面设置"
      width="420px"
      :append-to="dialogAppendTarget"
      :append-to-body="dialogAppendTarget === 'body'"
    >
        <el-form label-width="88px">
        <el-form-item label="传输预设">
          <el-segmented
            v-model="settingsForm.preset"
            :options="presetOptions"
            @change="handlePresetChange"
          />
        </el-form-item>
        <el-form-item label="压缩质量">
          <el-slider v-model="settingsForm.quality" :min="35" :max="90" :step="5" show-input @change="markPresetCustom" />
        </el-form-item>
        <el-form-item label="帧率">
          <el-slider v-model="settingsForm.fps" :min="4" :max="30" :step="1" show-input @change="markPresetCustom" />
        </el-form-item>
        <el-form-item label="桌面分辨率">
          <el-select
            :model-value="getDesktopResolutionValue(settingsForm)"
            style="width: 100%"
            @change="handleSettingsDesktopResolutionChange"
          >
            <el-option
              v-for="option in getDesktopResolutionOptions(settingsForm.desktopWidth, settingsForm.desktopHeight)"
              :key="`${option.width}x${option.height}`"
              :label="option.label"
              :value="`${option.width}x${option.height}`"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="推流分辨率">
          <el-select
            :model-value="settingsForm.scalePercent"
            style="width: 100%"
            @change="handleSettingsResolutionChange"
          >
            <el-option
              v-for="option in getResolutionScaleOptions(settingsForm.scalePercent)"
              :key="option"
              :label="getResolutionOptionLabel(option)"
              :value="option"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="输出缩放">
          <el-slider v-model="settingsForm.scalePercent" :min="40" :max="100" :step="5" show-input @change="markPresetCustom" />
        </el-form-item>
        <el-form-item label="传输模式">
          <el-switch
            v-model="settingsForm.adaptive"
            inline-prompt
            active-text="自适应"
            inactive-text="手动"
          />
        </el-form-item>
        <el-form-item label="滚轮速度">
          <el-slider v-model="settingsForm.wheelSpeed" :min="0.5" :max="3" :step="0.1" show-input @change="markPresetCustom" />
        </el-form-item>
        <el-form-item label="鼠标灵敏度">
          <el-slider v-model="settingsForm.mouseSensitivity" :min="0.5" :max="2" :step="0.1" show-input @change="markPresetCustom" />
        </el-form-item>
        <el-form-item label="自动重连">
          <el-switch
            v-model="settingsForm.autoReconnect"
            inline-prompt
            active-text="开"
            inactive-text="关"
          />
        </el-form-item>
        <div class="settings-hint">
          桌面分辨率会修改被控端真实 Windows 显示模式；推流分辨率只影响传输画面的输出尺寸；压缩质量只影响 JPEG 编码质量。自适应模式会在带宽或负载压力较高时自动降低帧率、压缩质量和推流分辨率。
        </div>
      </el-form>

      <template #footer>
        <el-button @click="resetSettingsForm">重置</el-button>
        <el-button @click="settingsVisible = false">取消</el-button>
        <el-button type="primary" @click="applySettings">应用</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="clipboardVisible"
      title="远程剪贴板"
      width="760px"
      :append-to="dialogAppendTarget"
      :append-to-body="dialogAppendTarget === 'body'"
    >
      <div class="clipboard-panel">
        <div class="clipboard-toolbar">
          <el-space wrap>
            <el-button @click="readLocalClipboard()">读取本地剪贴板</el-button>
            <el-button @click="requestRemoteClipboard()">读取远端剪贴板</el-button>
            <el-button type="primary" @click="pushClipboardToRemote()">推送到远端</el-button>
            <el-button @click="copyRemoteClipboardToLocal">复制到本地</el-button>
          </el-space>
        </div>
        <div class="clipboard-grid">
          <div class="clipboard-column">
            <div class="clipboard-title">本地文本</div>
            <el-input
              v-model="clipboardState.localText"
              type="textarea"
              :rows="10"
              resize="none"
              placeholder="这里显示本地剪贴板文本，也可以直接手工输入后推送到被控端"
            />
          </div>
          <div class="clipboard-column">
            <div class="clipboard-title">远端文本</div>
            <el-input
              v-model="clipboardState.remoteText"
              type="textarea"
              :rows="10"
              resize="none"
              readonly
              placeholder="点击“读取远端剪贴板”后显示被控端文本"
            />
          </div>
        </div>
        <div class="clipboard-hint">
          当前支持文本剪贴板双向同步。常用快捷键也会做最佳努力同步：Ctrl+V 会先推送本地文本，Ctrl+C 或 Ctrl+X 后会自动回读远端文本。
        </div>
      </div>
    </el-dialog>

    <el-dialog
      v-model="fileTransferVisible"
      title="文件传输"
      width="980px"
      :append-to="dialogAppendTarget"
      :append-to-body="dialogAppendTarget === 'body'"
    >
      <div class="file-transfer-panel">
        <div class="file-transfer-toolbar">
          <el-space wrap>
            <el-button type="primary" :icon="UploadFilled" @click="triggerFilePicker">
              上传文件
            </el-button>
            <el-button
              v-if="remoteCapabilities.directoryUpload"
              :icon="FolderOpened"
              @click="triggerFolderPicker"
            >
              上传文件夹
            </el-button>
            <el-button :icon="Refresh" @click="requestRemoteFileList">
              刷新列表
            </el-button>
            <el-button
              v-if="transferRecords.length"
              :icon="CircleClose"
              @click="clearTransferHistory"
            >
              清空记录
            </el-button>
            <span class="transfer-directory">
              远端目录: {{ fileTransferState.transferDirectory || remoteCapabilities.transferDirectory || '加载中...' }}
            </span>
          </el-space>
        </div>

        <div
          class="transfer-dropzone"
          :class="{ 'is-active': fileDropActive }"
          @dragenter.prevent="handleTransferDragEnter"
          @dragover.prevent="handleTransferDragOver"
          @dragleave.prevent="handleTransferDragLeave"
          @drop.prevent="handleTransferDrop"
        >
          <div class="transfer-dropzone-title">拖拽文件到这里可直接上传</div>
          <div class="transfer-dropzone-hint">
            支持批量文件；Chromium 内核浏览器下可直接拖入文件夹。也可以使用“上传文件夹”保留目录结构。
          </div>
        </div>

        <div class="transfer-overview">
          <div class="transfer-overview-card">
            <div class="transfer-overview-label">活动任务</div>
            <div class="transfer-overview-value">{{ activeTransfers.length }}</div>
          </div>
          <div class="transfer-overview-card">
            <div class="transfer-overview-label">最近上传</div>
            <div class="transfer-overview-text">{{ fileTransferState.uploadStatus || '暂无' }}</div>
          </div>
          <div class="transfer-overview-card">
            <div class="transfer-overview-label">最近下载</div>
            <div class="transfer-overview-text">{{ fileTransferState.downloadStatus || '暂无' }}</div>
          </div>
        </div>

        <div class="file-list-header">
          <span>活动传输</span>
          <span class="file-list-loading" v-if="activeTransfers.length">
            支持取消正在传输中的上传和下载任务
          </span>
        </div>

        <el-table
          :data="activeTransfers"
          border
          max-height="220"
          size="small"
          empty-text="当前没有活动中的传输任务"
        >
          <el-table-column label="方向" width="90">
            <template #default="{ row }">
              <el-tag :type="row.direction === 'upload' ? 'primary' : 'success'" size="small">
                {{ row.direction === 'upload' ? '上传' : '下载' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="displayName" label="文件" min-width="260" />
          <el-table-column label="大小" width="120">
            <template #default="{ row }">
              {{ formatBytes(row.totalBytes) }}
            </template>
          </el-table-column>
          <el-table-column label="进度" min-width="220">
            <template #default="{ row }">
              <el-progress :percentage="Math.round(row.progress || 0)" :stroke-width="12" />
            </template>
          </el-table-column>
          <el-table-column label="状态" min-width="180">
            <template #default="{ row }">
              {{ row.message || formatTransferStatus(row.status) }}
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="remoteCapabilities.cancelTransfer"
                link
                type="danger"
                @click="cancelTransfer(row)"
              >
                取消
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="file-list-header">
          <span>最近记录</span>
          <span class="file-list-loading" v-if="transferRecords.length">
            保留最近 {{ transferHistory.length }} 条传输历史
          </span>
        </div>

        <el-table
          :data="transferHistory"
          border
          max-height="220"
          size="small"
          empty-text="暂无传输记录"
        >
          <el-table-column label="方向" width="90">
            <template #default="{ row }">
              <el-tag :type="row.direction === 'upload' ? 'primary' : 'success'" size="small">
                {{ row.direction === 'upload' ? '上传' : '下载' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="displayName" label="文件" min-width="240" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="transferStatusTagType(row.status)" size="small">
                {{ formatTransferStatus(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="进度" width="90">
            <template #default="{ row }">
              {{ Math.round(row.progress || 0) }}%
            </template>
          </el-table-column>
          <el-table-column label="时间" width="180">
            <template #default="{ row }">
              {{ formatClientTimestamp(row.updatedAt) }}
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="220">
            <template #default="{ row }">
              {{ row.message || '-' }}
            </template>
          </el-table-column>
        </el-table>

        <div class="file-list-header">
          <span>被控端可下载文件</span>
          <span v-if="fileTransferState.remoteLoading" class="file-list-loading">正在刷新...</span>
        </div>

        <el-table
          :data="remoteFiles"
          border
          height="360"
          size="small"
          empty-text="远端传输目录暂无文件"
        >
          <el-table-column prop="name" label="文件名" min-width="280" />
          <el-table-column label="大小" width="140">
            <template #default="{ row }">
              {{ formatBytes(row.size) }}
            </template>
          </el-table-column>
          <el-table-column label="更新时间" width="190">
            <template #default="{ row }">
              {{ formatTimestamp(row.modified_at) }}
            </template>
          </el-table-column>
          <el-table-column prop="relative_path" label="相对路径" min-width="180" />
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" :icon="Download" @click="downloadRemoteFile(row)">
                下载
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>

    <input
      ref="uploadInput"
      class="hidden-upload-input"
      type="file"
      multiple
      @change="handleFileSelection"
    />
    <input
      ref="uploadFolderInput"
      class="hidden-upload-input"
      type="file"
      multiple
      webkitdirectory
      directory
      @change="handleFolderSelection"
    />
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  FullScreen, Refresh, Setting, Loading, CircleClose, Monitor,
  DocumentCopy, FolderOpened, UploadFilled, Download
} from '@element-plus/icons-vue'
import { getAuthToken } from '@/api/auth-session'
import { createRemoteSession, deleteRemoteSession } from '@/api/remote'

const props = defineProps({
  assetId: {
    type: [String, Number],
    required: true
  },
  ipAddress: {
    type: String,
    required: true
  },
  hostname: {
    type: String,
    default: ''
  },
  visible: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['close', 'request-close', 'update:visible'])

const dialogVisible = ref(props.visible)
const desktopContainer = ref(null)
const desktopCanvas = ref(null)
const connectionStatus = ref('disconnected') // disconnected, connecting, connected, error
const errorMessage = ref('')
const targetInfo = ref('')
const isFullscreen = ref(false)
const awaitingConsent = ref(false)
const remoteScreenSize = ref({
  width: 1920,
  height: 1080
})
const canvasDisplayStyle = ref({
  width: '1920px',
  height: '1080px'
})
const fullscreenToolbarVisible = ref(false)
const settingsVisible = ref(false)
const clipboardVisible = ref(false)
const fileTransferVisible = ref(false)
const isClosing = ref(false)
const closeNotified = ref(false)
const uploadInput = ref(null)
const uploadFolderInput = ref(null)
const remoteFiles = ref([])
const remoteCapabilities = ref({
  clipboardText: false,
  fileTransfer: true,
  directoryUpload: false,
  cancelTransfer: false,
  transferDirectory: '',
  desktopResolutionControl: false,
  desktopResolutions: [],
  maxFileSize: 0,
  chunkSize: 96 * 1024
})
const clipboardState = ref({
  localText: '',
  remoteText: ''
})
const fileTransferState = ref({
  transferDirectory: '',
  remoteLoading: false,
  uploadProgress: 0,
  uploadStatus: '',
  downloadProgress: 0,
  downloadStatus: ''
})
const transferRecords = ref([])
const fileDropActive = ref(false)
const transferRecordMap = new Map()
const pendingDownloads = new Map()
const activeUploadControllers = new Map()
const uploadStatusWaiters = new Map()
const uploadQueue = []
const activeTransferStatuses = new Set(['queued', 'started', 'progress', 'canceling'])
let processingUploadQueue = false
let dragCounter = 0
let fullscreenToolbarTimer = null
const localClipboardAvailable = typeof navigator !== 'undefined'
  && Boolean(navigator.clipboard)
  && typeof navigator.clipboard.readText === 'function'
  && typeof navigator.clipboard.writeText === 'function'
const remoteClipboardShortcutReadDelayMs = 180
const transferHistory = computed(() => transferRecords.value.slice(0, 20))
const activeTransfers = computed(() => (
  transferRecords.value.filter(record => activeTransferStatuses.has(record.status))
))
const dialogAppendTarget = computed(() => (
  isFullscreen.value && desktopContainer.value ? desktopContainer.value : 'body'
))
const clearUploadStatusWaiters = (reason = '远程桌面连接已断开，上传状态确认失败') => {
  uploadStatusWaiters.forEach(waiters => {
    waiters.forEach(waiter => {
      if (waiter.timer) {
        clearTimeout(waiter.timer)
      }
      waiter.reject(new Error(reason))
    })
  })
  uploadStatusWaiters.clear()
}

const rejectUploadStatusWaiters = (transferId, reason) => {
  const waiters = uploadStatusWaiters.get(transferId) || []
  waiters.forEach(waiter => {
    if (waiter.timer) {
      clearTimeout(waiter.timer)
    }
    waiter.reject(new Error(reason))
  })
  uploadStatusWaiters.delete(transferId)
}

// 状态信息
const desktopResolution = ref('1920x1080')
const streamResolution = ref('1728x972')
const fps = ref(0)
const latency = ref(0)
const bandwidth = ref('0 KB/s')
const sessionWarningText = ref('')
const defaultSessionSettings = (overrides = {}) => ({
  quality: 60,
  fps: 60,
  scalePercent: 60,
  adaptive: true,
  profile: 'interactive',
  wheelSpeed: 1,
  mouseSensitivity: 1,
  preset: 'balanced',
  desktopWidth: 0,
  desktopHeight: 0,
  autoReconnect: false,
  ...overrides
})
const sessionSettings = ref(defaultSessionSettings())
const settingsForm = ref(defaultSessionSettings())
const presetOptions = [
  { label: '流畅', value: 'smooth' },
  { label: '均衡', value: 'balanced' },
  { label: '高清', value: 'high' },
  { label: '自定义', value: 'custom' }
]
const baseResolutionScaleOptions = [50, 60, 70, 80, 90, 100]

const formatResolutionText = (width, height, fallback = '-') => {
  const normalizedWidth = Math.max(0, Math.round(Number(width) || 0))
  const normalizedHeight = Math.max(0, Math.round(Number(height) || 0))
  if (!normalizedWidth || !normalizedHeight) {
    return fallback
  }
  return `${normalizedWidth}x${normalizedHeight}`
}

const buildScaledResolutionText = (scalePercent, fallback = '-') => {
  const width = Math.max(0, Math.round(remoteScreenSize.value.width * (scalePercent / 100)))
  const height = Math.max(0, Math.round(remoteScreenSize.value.height * (scalePercent / 100)))
  return formatResolutionText(width, height, fallback)
}

const parseDesktopResolutionValue = (value, fallbackWidth = 0, fallbackHeight = 0) => {
  const text = String(value || '').trim()
  const match = text.match(/^(\d+)\s*x\s*(\d+)$/i)
  if (!match) {
    return {
      width: Math.max(0, Math.round(Number(fallbackWidth) || 0)),
      height: Math.max(0, Math.round(Number(fallbackHeight) || 0))
    }
  }

  return {
    width: Math.max(0, Math.round(Number(match[1]) || 0)),
    height: Math.max(0, Math.round(Number(match[2]) || 0))
  }
}

const getDesktopResolutionValue = (settings) => {
  const width = Math.max(0, Math.round(Number(settings?.desktopWidth) || 0))
  const height = Math.max(0, Math.round(Number(settings?.desktopHeight) || 0))
  return width > 0 && height > 0 ? `${width}x${height}` : ''
}

const getDesktopResolutionOptions = (currentWidth, currentHeight) => {
  const options = Array.isArray(remoteCapabilities.value.desktopResolutions)
    ? remoteCapabilities.value.desktopResolutions
      .map(option => ({
        width: Math.max(0, Math.round(Number(option?.width) || 0)),
        height: Math.max(0, Math.round(Number(option?.height) || 0))
      }))
      .filter(option => option.width > 0 && option.height > 0)
    : []

  const fallbackWidth = Math.max(
    0,
    Math.round(Number(currentWidth) || 0),
    Math.round(Number(remoteScreenSize.value.width) || 0)
  )
  const fallbackHeight = Math.max(
    0,
    Math.round(Number(currentHeight) || 0),
    Math.round(Number(remoteScreenSize.value.height) || 0)
  )

  if (fallbackWidth > 0 && fallbackHeight > 0) {
    options.push({ width: fallbackWidth, height: fallbackHeight })
  }

  return Array.from(
    new Map(
      options.map(option => [
        `${option.width}x${option.height}`,
        {
          width: option.width,
          height: option.height,
          label: formatResolutionText(option.width, option.height)
        }
      ])
    ).values()
  ).sort((left, right) => {
    if (left.width === right.width) {
      return left.height - right.height
    }
    return left.width - right.width
  })
}

const hoverMoveThrottle = 8
const dragMoveThrottle = 8
const dragStartThreshold = 4
const releaseDedupWindowMs = 1200
let lastHoverMoveAt = 0
let lastDragMoveAt = 0
const pointerState = {
  activePointerId: null,
  activeButton: null,
  dragActive: false,
  dragMessageSent: false,
  pressedButtons: new Set(),
  pressClientPosition: null,
  lastDragClientPosition: null,
  pendingDragClientDeltaX: 0,
  pendingDragClientDeltaY: 0,
  dragRemainderX: 0,
  dragRemainderY: 0,
  lastPosition: null,
  ignoreLostCapture: new Set(),
  recentRelease: null
}

// WebSocket连接
let ws = null
let currentSessionId = null
let ctx = null
let heartbeatTimer = null
let lastFrameTime = 0
let frameCounter = 0
let lastPingSentAt = 0
let latestFrameId = 0
let lastRenderedFrameId = 0
// H.264（WebCodecs）状态：与 JPEG 队列互不干扰
let h264Mode = false
let videoDecoder = null
let h264DecodeFailStreak = 0
let h264LastSeq = 0
let isDecodingFrame = false
let wsCandidates = []
let wsCandidateIndex = 0
// 能力声明：引擎的消息循环在会话建立后 ~10s 才就绪，onopen 立即发送会丢失。
// 改为收到引擎第一条消息后再声明，并在直连静默时重发兜底。
let capabilitiesSent = false
let capabilitiesRetryTimer = null
let silentCheckTimer = null
// WebTransport (UDP/QUIC) 自适应：连续 2 次失败后 5 分钟内回落 WebSocket(TCP)
let wtFailCount = 0
let wtDisabledUntil = 0

const clearSilentCheckTimer = () => {
  if (silentCheckTimer) {
    clearTimeout(silentCheckTimer)
    silentCheckTimer = null
  }
}

const clearCapabilitiesRetry = () => {
  if (capabilitiesRetryTimer) {
    clearInterval(capabilitiesRetryTimer)
    capabilitiesRetryTimer = null
  }
}

const announceCapabilities = (force = false) => {
  if (capabilitiesSent && !force) {
    return
  }
  const webcodecs = typeof window !== 'undefined' && typeof window.VideoDecoder === 'function'
  capabilitiesSent = true
  sendSocketMessage({ type: 'viewer_capabilities', webcodecs })
  // 引擎消息循环未就绪时首条声明可能被丢弃：短暂间隔重发，收到 codec_switch/h264 帧即停
  clearCapabilitiesRetry()
  let retries = 0
  capabilitiesRetryTimer = setInterval(() => {
    retries += 1
    if (h264Mode || retries >= 3 || wsCandidateIndex > 0 || !isSocketOpen()) {
      clearCapabilitiesRetry()
      return
    }
    sendSocketMessage({ type: 'viewer_capabilities', webcodecs })
  }, 1500)
}

let pendingFrame = null
let resizeObserver = null
let reconnectTimer = null
let reconnectAttempts = 0
let reconnectSuppressed = false
let remoteSessionInitialized = false
let lastSessionWarningKey = ''
let lastSessionWarningAt = 0
const maxReconnectAttempts = 6
const reconnectDelays = [1000, 2000, 3000, 5000, 8000, 12000]
const reconnectSuppressCloseCodes = new Set([1000, 1001, 4000, 4003, 4400, 4401, 4404, 4409])
const reconnectSuppressReasonTokens = [
  'remote_control_rejected',
  'consent_denied',
  'consent_rejected',
  'consent_timeout',
  'unauthorized',
  'offline',
  'not_installed',
  'session_closed',
  'session_ended'
]

const logRemoteDesktop = (...args) => {
  console.info('[remote-desktop][dialog]', ...args)
}

const shouldSuppressReconnectOnClose = (event) => {
  const closeCode = Number(event?.code || 0)
  const closeReason = String(event?.reason || '').trim().toLowerCase()

  if (reconnectSuppressCloseCodes.has(closeCode)) {
    return true
  }

  if (closeReason && reconnectSuppressReasonTokens.some(token => closeReason.includes(token))) {
    return true
  }

  return false
}

const shouldSuppressReconnectOnSessionError = (message) => {
  const errorText = String(message || '').trim().toLowerCase()
  if (!errorText) {
    return false
  }

  return reconnectSuppressReasonTokens.some(token => errorText.includes(token))
    || errorText.includes('拒绝')
    || errorText.includes('超时')
    || errorText.includes('离线')
    || errorText.includes('未安装')
    || errorText.includes('无权限')
}

const markRemoteSessionInitialized = () => {
  clearSilentCheckTimer()
  remoteSessionInitialized = true
}

const showSessionWarning = (message) => {
  const warningText = String(message?.message || '远程桌面会话存在异常').trim()
  if (!warningText) {
    return
  }

  const warningCode = String(message?.code || 'session_warning').trim()
  const warningKey = `${warningCode}:${warningText}`
  const now = Date.now()
  if (warningKey === lastSessionWarningKey && now - lastSessionWarningAt < 10000) {
    return
  }

  lastSessionWarningKey = warningKey
  lastSessionWarningAt = now
  sessionWarningText.value = warningText
  console.warn('[remote-desktop][session-warning]', message)
  ElMessage.warning(warningText)
}

onMounted(() => {
  logRemoteDesktop('mounted', {
    assetId: props.assetId,
    ipAddress: props.ipAddress,
    visible: props.visible
  })
  targetInfo.value = `${props.hostname || props.ipAddress} (${props.ipAddress})`
  syncFullscreenState()

  // 等待DOM渲染完成后初始化Canvas
  nextTick(() => {
    initCanvas()
    setupCanvasResizeHandling()

    // 再等一下确保Canvas已经ready
    setTimeout(() => {
      connect()
    }, 100)
  })
})

onBeforeUnmount(() => {
  logRemoteDesktop('before unmount')
  teardownCanvasResizeHandling()
  void exitFullscreenIfNeeded()
  resetPointerState()
  disconnect({ suppressReconnect: true })
  cleanupRemoteDialogArtifacts()
  teardownH264()
  // 释放后端远控会话，避免会话只能等超时回收
  if (currentSessionId) {
    deleteRemoteSession(currentSessionId).catch(() => {})
    currentSessionId = null
  }
})

watch(() => props.visible, (visible) => {
  logRemoteDesktop('props.visible changed', visible)
  if (visible) {
    closeNotified.value = false
  }
  dialogVisible.value = visible
  if (!visible) {
    void performCloseCleanup()
  }
})

watch(dialogVisible, (visible) => {
  logRemoteDesktop('dialogVisible changed', visible)
  if (visible && visible !== props.visible) {
    emit('update:visible', visible)
  }
})

const refreshCanvasContext = () => {
  const canvas = desktopCanvas.value
  if (!canvas) {
    return
  }

  ctx = canvas.getContext('2d', {
    alpha: false,
    desynchronized: true
  })

  if (ctx) {
    ctx.imageSmoothingEnabled = false
  }
}

const getCanvasBoxMetrics = (canvas) => {
  const style = window.getComputedStyle(canvas)
  const borderLeft = Number.parseFloat(style.borderLeftWidth || '0') || 0
  const borderRight = Number.parseFloat(style.borderRightWidth || '0') || 0
  const borderTop = Number.parseFloat(style.borderTopWidth || '0') || 0
  const borderBottom = Number.parseFloat(style.borderBottomWidth || '0') || 0

  return {
    borderLeft,
    borderRight,
    borderTop,
    borderBottom,
    horizontalBorder: borderLeft + borderRight,
    verticalBorder: borderTop + borderBottom
  }
}

const showFullscreenToolbar = () => {
  if (!isFullscreen.value) {
    return
  }

  fullscreenToolbarVisible.value = true
}

const hideFullscreenToolbar = () => {
  fullscreenToolbarVisible.value = false
}

const clearFullscreenToolbarTimer = () => {
  if (fullscreenToolbarTimer) {
    clearTimeout(fullscreenToolbarTimer)
    fullscreenToolbarTimer = null
  }
}

const handleFullscreenActivity = () => {
  showFullscreenToolbar()
}

const syncFullscreenState = () => {
  const nextFullscreen = document.fullscreenElement === desktopContainer.value
  const fullscreenChanged = isFullscreen.value !== nextFullscreen

  isFullscreen.value = nextFullscreen

  if (nextFullscreen) {
    if (fullscreenChanged) {
      fullscreenToolbarVisible.value = false
    }
    return
  }

  fullscreenToolbarVisible.value = false
}

const updateCanvasLayout = () => {
  const container = desktopContainer.value
  const canvas = desktopCanvas.value
  if (!container || !canvas) {
    return
  }

  const remoteWidth = Math.max(1, canvas.width || 1920)
  const remoteHeight = Math.max(1, canvas.height || 1080)
  const { horizontalBorder, verticalBorder } = getCanvasBoxMetrics(canvas)
  const availableWidth = Math.max(1, container.clientWidth - horizontalBorder)
  const availableHeight = Math.max(1, container.clientHeight - verticalBorder)
  const scale = Math.min(availableWidth / remoteWidth, availableHeight / remoteHeight)

  canvasDisplayStyle.value = {
    width: `${Math.max(1, Math.floor(remoteWidth * scale))}px`,
    height: `${Math.max(1, Math.floor(remoteHeight * scale))}px`
  }
}

const applyRemoteResolution = (width, height) => {
  const canvas = desktopCanvas.value
  if (!canvas) {
    return
  }

  const nextWidth = Math.max(1, Math.round(Number(width) || canvas.width || 1920))
  const nextHeight = Math.max(1, Math.round(Number(height) || canvas.height || 1080))

  if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
    canvas.width = nextWidth
    canvas.height = nextHeight
    refreshCanvasContext()
  }

  updateCanvasLayout()
}

const handleViewportResize = () => {
  syncFullscreenState()
  updateCanvasLayout()
}

const setupCanvasResizeHandling = () => {
  const container = desktopContainer.value
  if (!container) {
    return
  }

  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => {
      updateCanvasLayout()
    })
    resizeObserver.observe(container)
  }

  window.addEventListener('resize', handleViewportResize)
  document.addEventListener('fullscreenchange', handleViewportResize)
  updateCanvasLayout()
}

const teardownCanvasResizeHandling = () => {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }

  window.removeEventListener('resize', handleViewportResize)
  document.removeEventListener('fullscreenchange', handleViewportResize)
  clearFullscreenToolbarTimer()
}

const initCanvas = () => {
  const canvas = desktopCanvas.value
  if (!canvas) {
    console.error('❌ Canvas元素未找到，ref可能未绑定')
    return
  }

  console.log('✅ 初始化Canvas:', canvas)

  // 初始Canvas尺寸（会根据远程桌面分辨率自动调整）
  canvas.width = 1920
  canvas.height = 1080

  refreshCanvasContext()
  updateCanvasLayout()

  // 设置Canvas为可聚焦，以接收键盘事件
  canvas.focus()

  // 绘制初始提示
  ctx.fillStyle = '#f5f5f5'
  ctx.fillRect(0, 0, canvas.width, canvas.height)
  ctx.fillStyle = '#909399'
  ctx.font = '20px Arial'
  ctx.textAlign = 'center'
  ctx.fillText('正在连接远程桌面...', canvas.width / 2, canvas.height / 2)

  console.log('✅ Canvas初始化完成，初始尺寸:', canvas.width, 'x', canvas.height)
}

const connect = async () => {
  clearReconnectTimer()
  reconnectSuppressed = false
  connectionStatus.value = 'connecting'
  errorMessage.value = ''
  lastFrameTime = 0
  frameCounter = 0
  lastPingSentAt = 0
  latestFrameId = 0
  lastRenderedFrameId = 0
  isDecodingFrame = false
  pendingFrame = null
  fps.value = 0
  latency.value = 0
  bandwidth.value = '0 KB/s'
  sessionWarningText.value = ''
  lastSessionWarningKey = ''
  lastSessionWarningAt = 0
  remoteFiles.value = []
  remoteCapabilities.value = {
    clipboardText: false,
    fileTransfer: true,
    directoryUpload: false,
    cancelTransfer: false,
    transferDirectory: '',
    desktopResolutionControl: false,
    desktopResolutions: [],
    maxFileSize: 0,
    chunkSize: 96 * 1024
  }
  fileTransferState.value = {
    transferDirectory: '',
    remoteLoading: false,
    uploadProgress: 0,
    uploadStatus: '',
    downloadProgress: 0,
    downloadStatus: ''
  }
  transferRecords.value = []
  transferRecordMap.clear()
  pendingDownloads.clear()
  activeUploadControllers.clear()
  clearUploadStatusWaiters()
  uploadQueue.splice(0, uploadQueue.length)
  fileDropActive.value = false
  dragCounter = 0
  processingUploadQueue = false
  sessionSettings.value = defaultSessionSettings({
    autoReconnect: sessionSettings.value.autoReconnect
  })
  settingsForm.value = defaultSessionSettings({
    autoReconnect: settingsForm.value.autoReconnect
  })
  desktopResolution.value = formatResolutionText(remoteScreenSize.value.width, remoteScreenSize.value.height, '等待同步')
  streamResolution.value = buildScaledResolutionText(sessionSettings.value.scalePercent, '等待首帧')

  try {
    const token = getAuthToken()
    if (!token) {
      throw new Error('当前登录已失效，请重新登录后再发起远程桌面')
    }

    if (!props.assetId) {
      throw new Error('缺少终端资产标识，无法建立远程桌面代理连接')
    }

    // 新流程：先创建会话拿 session_token + ws_url
    const sessionInfo = await createRemoteSession({ asset_id: props.assetId, fps_limit: sessionSettings.value.fps || 60 })
    currentSessionId = sessionInfo.session_id
    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const relayUrl = `${wsProtocol}://${window.location.host}${sessionInfo.ws_url}`
    // 传输候选：WebTransport (UDP/QUIC) 优先，失败/超时自适应回落 WebSocket (TCP)。
    // HTTPS 页面下 ws:// 直连属于混合内容会被浏览器拦截，自动走 wss 中继。
    if (window.WebTransport && sessionInfo.wt_url && sessionInfo.wt_cert_hash && Date.now() > wtDisabledUntil) {
      try {
        const adapter = await openWebTransportAdapter(sessionInfo)
        wtFailCount = 0
        ws = adapter
        return
      } catch (e) {
        wtFailCount += 1
        if (wtFailCount >= 2) {
          wtDisabledUntil = Date.now() + 5 * 60 * 1000
          wtFailCount = 0
        }
        console.warn('WebTransport 不可用，回落 WebSocket(TCP):', e)
      }
    }
    const isHttpsPage = window.location.protocol === 'https:'
    wsCandidates = (!isHttpsPage && sessionInfo.direct_ws_url)
      ? [sessionInfo.direct_ws_url, relayUrl]
      : [relayUrl]
    wsCandidateIndex = 0
    openSocketFromCandidates(sessionInfo)
    return
  } catch (error) {
    console.error('连接失败:', error)
    connectionStatus.value = 'error'
    errorMessage.value = error.message
  }
}

// ============ WebTransport (UDP/QUIC) 传输适配 ============

const hexToBytes = (hex) => {
  const clean = (hex || '').replace(/[^0-9a-fA-F]/g, '')
  const out = new Uint8Array(clean.length / 2)
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(clean.substr(i * 2, 2), 16)
  }
  return out
}

const concatU8 = (a, b) => {
  const out = new Uint8Array(a.length + b.length)
  out.set(a, 0)
  out.set(b, a.length)
  return out
}

const openWebTransportAdapter = async (sessionInfo) => {
  // 自签证书通过 serverCertificateHashes 信任（Chrome 97+，要求 ECDSA P-256、有效期 ≤14 天）
  // 长期证书（3 年）不满足 serverCertificateHashes 的 14 天限制，
  // 改走系统信任库校验：客户端机器导入 zview-root.cer（受信任的根证书颁发机构）一次即可
  const wt = new WebTransport(sessionInfo.wt_url)
  // 自适应切换的关键：4 秒内未就绪即放弃，快速回落 TCP
  await Promise.race([
    wt.ready,
    new Promise((_, reject) => setTimeout(() => reject(new Error('webtransport ready timeout')), 4000))
  ])

  const stream = await wt.createBidirectionalStream()
  const writer = stream.writable.getWriter()
  const reader = stream.readable.getReader()

  // WT 流是无消息边界的字节流 → 与网关约定长度前缀帧：[4B len][1B type(0=text,1=binary)][payload]
  let closed = false
  let buffer = new Uint8Array(0)

  const markOpen = () => {
    ws = adapter
    remoteSessionInitialized = false
    reconnectSuppressed = false
    connectionStatus.value = 'connecting'
    awaitingConsent.value = true
    reconnectAttempts = 0
  }

  const adapter = {
    readyState: WebSocket.OPEN,
    send: (data) => {
      if (closed) return
      let payload
      let ftype
      if (typeof data === 'string') {
        payload = new TextEncoder().encode(data)
        ftype = 0
      } else {
        payload = data instanceof Uint8Array ? data : new Uint8Array(data)
        ftype = 1
      }
      const frame = new Uint8Array(5 + payload.length)
      new DataView(frame.buffer).setUint32(0, payload.length)
      frame[4] = ftype
      frame.set(payload, 5)
      writer.write(frame).catch(() => {})
    },
    close: () => {
      closed = true
      try {
        wt.close()
      } catch (e) {
        // ignore
      }
    }
  }

  markOpen()
  markSessionConnected()

  ;(async () => {
    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer = concatU8(buffer, value)
        while (buffer.length >= 5) {
          const len = (buffer[0] << 24) | (buffer[1] << 16) | (buffer[2] << 8) | buffer[3]
          if (buffer.length < 5 + len) break
          const ftype = buffer[4]
          const payload = buffer.slice(5, 5 + len)
          buffer = buffer.slice(5 + len)
          if (ftype === 0) {
            handleMessage(new TextDecoder().decode(payload))
          } else {
            handleBinaryFrame(payload.buffer)
          }
        }
      }
    } catch (e) {
      console.warn('WebTransport 流读取结束:', e)
    }
    if (ws === adapter && !closed) {
      // WT 断线：与 WS 断线一致走自适应重连（会再次尝试 WT，失败则 TCP）
      ws = null
      teardownH264()
      if (!reconnectSuppressed && sessionSettings.value.autoReconnect) {
        scheduleReconnect()
      }
    }
  })()

  return adapter
}

const openSocketFromCandidates = (sessionInfo) => {
  try {
    const token = getAuthToken()
    if (!token) {
      throw new Error('当前登录已失效，请重新登录后再发起远程桌面')
    }
    const wsProtocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(wsCandidates[wsCandidateIndex])
    socket.binaryType = 'arraybuffer'
    ws = socket
    remoteSessionInitialized = false
    reconnectSuppressed = false
    // 直连静默看门狗：必须在 socket 创建时设置（防火墙 DROP 时 onopen 永远不触发，
    // WebSocket 会停在 CONNECTING 状态直到 TCP 超时）——4 秒未打开即回落下一候选
    clearSilentCheckTimer()
    silentCheckTimer = setTimeout(() => {
      if (ws === socket && socket.readyState !== WebSocket.OPEN) {
        console.warn('候选连接超时未打开，回落下一候选:', wsCandidates[wsCandidateIndex])
        try {
          socket.close()
        } catch (e) {
          // 触发 onclose/onerror 走候选回落
        }
      }
    }, 4000)

    socket.onopen = () => {
      if (ws !== socket) return

      console.log('WebSocket连接已建立')
      connectionStatus.value = 'connecting'
      awaitingConsent.value = true
      reconnectAttempts = 0
      errorMessage.value = '等待被控端确认远程控制请求...'
      // 能力声明延后到收到引擎首条消息（引擎消息循环建立需约 10s，提前发会丢失）
      capabilitiesSent = false
      clearCapabilitiesRetry()
    }

    socket.onmessage = (event) => {
      if (ws !== socket) return

      // 二进制帧协议：控制消息走 text(JSON)，屏幕帧走 binary(ArrayBuffer)
      if (event.data instanceof ArrayBuffer) {
        handleBinaryFrame(event.data)
      } else {
        handleMessage(event.data)
      }
    }

    socket.onerror = (error) => {
      if (ws !== socket) return

      // 直连候选失败且会话尚未初始化 → 尝试下一个候选（回落平台中继）
      if (wsCandidateIndex < wsCandidates.length - 1 && !remoteSessionInitialized) {
        console.warn('直连失败，回落平台中继:', wsCandidates[wsCandidateIndex])
        wsCandidateIndex += 1
        openSocketFromCandidates()
        return
      }

      console.error('WebSocket错误:', error)
      awaitingConsent.value = false
      clearUploadStatusWaiters()
      connectionStatus.value = 'error'
      errorMessage.value = '无法通过平台建立远程桌面连接，请检查目标终端在线状态、Agent安装状态以及用户会话远控角色'
      if (!reconnectSuppressed) {
        scheduleReconnect()
      }
    }

    socket.onclose = (event) => {
      if (ws !== socket) return

      console.log('WebSocket连接已关闭')
      // 直连候选在初始化前断开且未报错 → 尝试下一个候选
      if (
        wsCandidateIndex < wsCandidates.length - 1 &&
        !remoteSessionInitialized &&
        event.code !== 1000 &&
        !shouldSuppressReconnectOnClose(event)
      ) {
        console.warn('直连未完成初始化，回落平台中继:', wsCandidates[wsCandidateIndex])
        wsCandidateIndex += 1
        openSocketFromCandidates()
        return
      }
      if (shouldSuppressReconnectOnClose(event)) {
        reconnectSuppressed = true
      }
      awaitingConsent.value = false
      stopHeartbeat()
      teardownH264()
      ws = null
      clearUploadStatusWaiters()
      const closeReason = String(event?.reason || '').trim()
      logRemoteDesktop('socket closed', {
        code: event?.code,
        reason: closeReason || '',
        autoReconnect: sessionSettings.value.autoReconnect,
        remoteSessionInitialized
      })
      if (closeReason) {
        connectionStatus.value = 'error'
        errorMessage.value = closeReason
      }
      if (!remoteSessionInitialized && !reconnectSuppressed) {
        reconnectSuppressed = true
        if (!closeReason) {
          connectionStatus.value = 'error'
          errorMessage.value = '远程桌面在授权或初始化阶段中断，请手动重试'
        }
      }
      if (connectionStatus.value !== 'error') {
        connectionStatus.value = 'disconnected'
      }
      if (!reconnectSuppressed) {
        scheduleReconnect()
      }
    }

  } catch (error) {
    console.error('连接失败:', error)
    connectionStatus.value = 'error'
    errorMessage.value = error.message
  }
}

const disconnect = (options = {}) => {
  const { suppressReconnect = false } = options
  if (suppressReconnect) {
    reconnectSuppressed = true
  }
  remoteSessionInitialized = false
  sessionWarningText.value = ''
  lastSessionWarningKey = ''
  lastSessionWarningAt = 0
  resetPointerState()
  stopHeartbeat()
  awaitingConsent.value = false
  clearReconnectTimer()
  lastPingSentAt = 0
  pendingDownloads.clear()
  activeUploadControllers.clear()
  clearUploadStatusWaiters()
  uploadQueue.splice(0, uploadQueue.length)
  fileDropActive.value = false
  dragCounter = 0
  fileTransferState.value.remoteLoading = false
  if (ws) {
    ws.close()
    ws = null
  }
}

const reconnect = () => {
  clearReconnectTimer()
  disconnect({ suppressReconnect: true })
  setTimeout(() => {
    connect()
  }, 500)
}

const clearReconnectTimer = () => {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
}

const scheduleReconnect = () => {
  if (!sessionSettings.value.autoReconnect || reconnectTimer || !dialogVisible.value) {
    return
  }

  if (reconnectAttempts >= maxReconnectAttempts) {
    return
  }

  const delay = reconnectDelays[Math.min(reconnectAttempts, reconnectDelays.length - 1)]
  reconnectAttempts += 1
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    if (!dialogVisible.value || !sessionSettings.value.autoReconnect) {
      return
    }
    connect()
  }, delay)
}

const startReceivingFrames = () => {
  // 真实接收画面，通过WebSocket接收
  console.log('开始接收远程桌面画面')
}

const startHeartbeat = () => {
  stopHeartbeat()

  // 每5秒发送一次心跳
  heartbeatTimer = setInterval(() => {
    if (isSocketOpen()) {
      lastPingSentAt = Date.now()
      sendSocketMessage({
        type: 'ping',
        timestamp: lastPingSentAt
      })
    }
  }, 5000)
}

const stopHeartbeat = () => {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
}

const markSessionConnected = () => {
  awaitingConsent.value = false
  if (connectionStatus.value !== 'connected') {
    connectionStatus.value = 'connected'
  }
  if (errorMessage.value) {
    errorMessage.value = ''
  }
}

const handleBinaryFrame = async (arrayBuffer) => {
  announceCapabilities()
  // 二进制帧协议：
  // 0x02 JPEG：[1B type][4B frameId][4B width][4B height][4B payloadLen][jpeg bytes]
  // 0x03 H264：[1B type][4B seq][4B width][4B height][4B payloadLen][1B keyframe][payload bytes]
  try {
    const view = new DataView(arrayBuffer)
    if (arrayBuffer.byteLength < 17) return
    const frameType = view.getUint8(0)
    const frameId = view.getUint32(1)
    const width = view.getUint32(5)
    const height = view.getUint32(9)
    const payloadLen = view.getUint32(13)
    if (arrayBuffer.byteLength < 17 + payloadLen) return

    if (frameType === 0x03) {
      // H.264：Annex-B 裸流，解码后渲染并回 ACK（背压依据）
      const keyframe = view.getUint8(17) === 1
      const payload = new Uint8Array(arrayBuffer, 18, payloadLen)
      markRemoteSessionInitialized()
      markSessionConnected()
      h264Mode = true
      clearCapabilitiesRetry()
      const decoder = ensureH264Decoder()
      if (!decoder || decoder.state !== 'configured') {
        // 解码器不可用：丢弃并要求关键帧/回退
        sendFrameAck(frameId)
        return
      }
      const now = Date.now()
      if (lastFrameTime > 0) {
        fps.value = Math.round(1000 / Math.max(1, now - lastFrameTime))
      }
      lastFrameTime = now
      frameCounter++
      streamResolution.value = formatResolutionText(width, height, streamResolution.value)
      if (frameCounter % 10 === 0) {
        bandwidth.value = `${Math.round(payloadLen / 1024 * Math.max(fps.value, 1))} KB/s`
      }
      const chunk = new EncodedVideoChunk({
        type: keyframe ? 'key' : 'delta',
        timestamp: frameId * 1000,
        data: payload
      })
      decoder.decode(chunk)
      sendFrameAck(frameId)
      return
    }

    // JPEG (0x02)
    const jpegBytes = new Uint8Array(arrayBuffer, 17, payloadLen)

    markRemoteSessionInitialized()
    markSessionConnected()
    latestFrameId += 1

    // 更新FPS
    const now = Date.now()
    if (lastFrameTime > 0) {
      const delta = now - lastFrameTime
      fps.value = Math.round(1000 / delta)
    }
    lastFrameTime = now
    frameCounter++

    // 更新推流分辨率
    streamResolution.value = formatResolutionText(width, height, streamResolution.value)
    // 更新带宽
    if (frameCounter % 10 === 0) {
      const dataSize = payloadLen / 1024
      bandwidth.value = `${Math.round(dataSize * fps.value)} KB/s`
    }

    // 用 createImageBitmap 解码（比 new Image + base64 快且无 base64 开销）
    const blob = new Blob([jpegBytes], { type: 'image/jpeg' })
    const bitmap = await createImageBitmap(blob)
    drawBitmapFrame(bitmap, width, height, frameId)
  } catch (e) {
    console.warn('[remote-desktop] binary frame decode failed', e)
  }
}

const handleMessage = (data) => {
  try {
    announceCapabilities()
    const message = JSON.parse(data)

    if (message.type === 'frame') {
      markRemoteSessionInitialized()
      markSessionConnected()
      latestFrameId += 1
      queueFrame(message, latestFrameId)

      // 更新FPS
      const now = Date.now()
      if (lastFrameTime > 0) {
        const delta = now - lastFrameTime
        fps.value = Math.round(1000 / delta)
      }
      lastFrameTime = now
      frameCounter++

      // 更新当前推流分辨率
      streamResolution.value = formatResolutionText(message.width, message.height, streamResolution.value)

      // 更新带宽
      if (frameCounter % 10 === 0) {
        const dataSize = message.data.length * 0.75 / 1024 // Base64转KB
        bandwidth.value = `${Math.round(dataSize * fps.value)} KB/s`
      }
    } else if (message.type === 'codec_switch') {
      // 编解码切换通知：h264 → 初始化 WebCodecs；jpeg → 清理 H.264 状态
      markRemoteSessionInitialized()
      markSessionConnected()
      if (message.codec === 'h264') {
        h264Mode = true
        h264LastSeq = 0
        const decoder = ensureH264Decoder()
        if (!decoder) {
          // 浏览器不支持 WebCodecs：通知服务端回退 JPEG
          sendSocketMessage({ type: 'viewer_capabilities', webcodecs: false })
          h264Mode = false
        }
      } else {
        teardownH264()
      }
    } else if (message.type === 'h264') {
      handleH264Frame(message)
    } else if (message.type === 'screen_info') {
      markRemoteSessionInitialized()
      markSessionConnected()
      remoteScreenSize.value = {
        width: Math.max(1, Math.round(Number(message.width) || remoteScreenSize.value.width)),
        height: Math.max(1, Math.round(Number(message.height) || remoteScreenSize.value.height))
      }
      const currentDesktopWidth = Math.max(
        1,
        Math.round(
          Number(message.desktop_width)
          || Number(message.primary_width)
          || remoteScreenSize.value.width
        )
      )
      const currentDesktopHeight = Math.max(
        1,
        Math.round(
          Number(message.desktop_height)
          || Number(message.primary_height)
          || remoteScreenSize.value.height
        )
      )
      applyRemoteResolution(message.width, message.height)
      desktopResolution.value = formatResolutionText(currentDesktopWidth, currentDesktopHeight, desktopResolution.value)
      sessionSettings.value = {
        ...sessionSettings.value,
        desktopWidth: currentDesktopWidth,
        desktopHeight: currentDesktopHeight
      }
      settingsForm.value = {
        ...settingsForm.value,
        desktopWidth: currentDesktopWidth,
        desktopHeight: currentDesktopHeight
      }
      streamResolution.value = buildScaledResolutionText(sessionSettings.value.scalePercent, streamResolution.value)
    } else if (message.type === 'session_settings') {
      markRemoteSessionInitialized()
      markSessionConnected()
      const nextSettings = {
        quality: normalizeNumber(message.quality, sessionSettings.value.quality),
        fps: normalizeNumber(message.fps, sessionSettings.value.fps),
        scalePercent: normalizeNumber(
          message.scale_percent,
          normalizeNumber(
            Math.round((Number(message.scale) || (sessionSettings.value.scalePercent / 100)) * 100),
            sessionSettings.value.scalePercent
          )
        ),
        adaptive: Boolean(message.adaptive),
        profile: message.profile || sessionSettings.value.profile,
        wheelSpeed: normalizeNumber(message.wheel_speed, sessionSettings.value.wheelSpeed),
        mouseSensitivity: normalizeNumber(message.mouse_sensitivity, sessionSettings.value.mouseSensitivity),
        preset: message.preset || sessionSettings.value.preset,
        desktopWidth: Math.max(
          1,
          Math.round(
            Number(message.desktop_width)
            || sessionSettings.value.desktopWidth
            || remoteScreenSize.value.width
          )
        ),
        desktopHeight: Math.max(
          1,
          Math.round(
            Number(message.desktop_height)
            || sessionSettings.value.desktopHeight
            || remoteScreenSize.value.height
          )
        ),
        autoReconnect: sessionSettings.value.autoReconnect
      }
      sessionSettings.value = nextSettings
      settingsForm.value = { ...nextSettings }
      streamResolution.value = buildScaledResolutionText(nextSettings.scalePercent, streamResolution.value)
    } else if (message.type === 'remote_capabilities') {
      markRemoteSessionInitialized()
      markSessionConnected()
      remoteCapabilities.value = {
        clipboardText: Boolean(message.clipboard_text),
        fileTransfer: Boolean(message.file_transfer),
        directoryUpload: Boolean(message.directory_upload),
        cancelTransfer: Boolean(message.cancel_transfer),
        transferDirectory: message.transfer_directory || '',
        desktopResolutionControl: Boolean(message.desktop_resolution_control),
        desktopResolutions: Array.isArray(message.desktop_resolutions) ? message.desktop_resolutions : [],
        maxFileSize: normalizeNumber(message.max_file_size, remoteCapabilities.value.maxFileSize),
        chunkSize: normalizeNumber(message.chunk_size, remoteCapabilities.value.chunkSize)
      }
      fileTransferState.value.transferDirectory = remoteCapabilities.value.transferDirectory
    } else if (message.type === 'settings_result') {
      if (message.category === 'desktop_resolution') {
        const currentDesktopWidth = Math.max(
          1,
          Math.round(Number(message.desktop_width) || sessionSettings.value.desktopWidth || remoteScreenSize.value.width)
        )
        const currentDesktopHeight = Math.max(
          1,
          Math.round(Number(message.desktop_height) || sessionSettings.value.desktopHeight || remoteScreenSize.value.height)
        )
        desktopResolution.value = formatResolutionText(currentDesktopWidth, currentDesktopHeight, desktopResolution.value)
        sessionSettings.value = {
          ...sessionSettings.value,
          desktopWidth: currentDesktopWidth,
          desktopHeight: currentDesktopHeight
        }
        settingsForm.value = {
          ...settingsForm.value,
          desktopWidth: currentDesktopWidth,
          desktopHeight: currentDesktopHeight
        }
        if (!message.success) {
          ElMessage.error(message.message || '桌面分辨率切换失败')
        }
      }
    } else if (message.type === 'clipboard_data') {
      clipboardState.value.remoteText = String(message.text || '')
      if (clipboardVisible.value) {
        ElMessage.success(message.message || '已读取远端剪贴板')
      }
    } else if (message.type === 'clipboard_result') {
      if (message.success) {
        if (message.operation === 'set') {
          clipboardState.value.remoteText = clipboardState.value.localText
        }
        ElMessage.success(message.message || '剪贴板操作成功')
      } else {
        ElMessage.error(message.message || '剪贴板操作失败')
      }
    } else if (message.type === 'file_list') {
      remoteFiles.value = Array.isArray(message.files) ? message.files : []
      fileTransferState.value.remoteLoading = false
      fileTransferState.value.transferDirectory = message.transfer_directory || fileTransferState.value.transferDirectory
    } else if (message.type === 'file_transfer_status') {
      handleFileTransferStatus(message)
    } else if (message.type === 'file_download_chunk') {
      handleDownloadChunk(message)
    } else if (message.type === 'file_download_complete') {
      handleDownloadComplete(message)
    } else if (message.type === 'consent_required') {
      awaitingConsent.value = true
      connectionStatus.value = 'connecting'
      errorMessage.value = `等待 ${message.target || props.hostname || props.ipAddress} 终端用户确认远程控制请求...`
    } else if (message.type === 'consent_result') {
      awaitingConsent.value = false
      if (message.approved) {
        markSessionConnected()
        ElMessage.success(message.message || '被控端已接受远程控制请求')
        startReceivingFrames()
        startHeartbeat()
      } else {
        connectionStatus.value = 'error'
        errorMessage.value = message.message || '被控端拒绝了本次远程控制请求'
        reconnectSuppressed = true
        if (ws) {
          ws.close()
        }
      }
    } else if (message.type === 'session_ready') {
      markRemoteSessionInitialized()
      markSessionConnected()
      startReceivingFrames()
      startHeartbeat()
    } else if (message.type === 'session_error') {
      awaitingConsent.value = false
      connectionStatus.value = 'error'
      errorMessage.value = message.message || '远程桌面会话启动失败'
      if (!remoteSessionInitialized) {
        reconnectSuppressed = true
      }
      if (shouldSuppressReconnectOnSessionError(message.message)) {
        reconnectSuppressed = true
      }
    } else if (message.type === 'session_warning') {
      showSessionWarning(message)
    } else if (message.type === 'pong') {
      const sentAt = typeof message.timestamp === 'number' ? message.timestamp : lastPingSentAt
      if (sentAt > 0) {
        latency.value = Math.max(0, Date.now() - sentAt)
      }
    }
  } catch (error) {
    console.error('处理消息错误:', error)
  }
}

const isSocketOpen = () => Boolean(ws && ws.readyState === WebSocket.OPEN)

const sendSocketMessage = (payload) => {
  if (!isSocketOpen()) {
    return false
  }
  ws.send(JSON.stringify(payload))
  return true
}

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
  handleFullscreenActivity()
  clipboardVisible.value = true
  await readLocalClipboard({ silent: true })
  if (remoteCapabilities.value.clipboardText) {
    requestRemoteClipboard({ silent: true })
  }
}

const openFileTransferDialog = () => {
  if (!remoteCapabilities.value.fileTransfer) {
    ElMessage.warning('当前被控端未启用文件传输能力')
    return
  }
  handleFullscreenActivity()
  fileTransferVisible.value = true
  requestRemoteFileList()
}

const requestRemoteFileList = () => {
  fileTransferState.value.remoteLoading = true
  if (!sendSocketMessage({
    type: 'file_list_request',
    limit: 100
  })) {
    fileTransferState.value.remoteLoading = false
    ElMessage.warning('远程桌面未连接，无法获取文件列表')
    return false
  }
  return true
}

const triggerFilePicker = () => {
  uploadInput.value?.click()
}

const triggerFolderPicker = () => {
  uploadFolderInput.value?.click()
}

const wait = (delay) => new Promise(resolve => setTimeout(resolve, delay))

const waitForSocketDrain = async (limit = 512 * 1024) => {
  while (isSocketOpen() && ws.bufferedAmount > limit) {
    await wait(20)
  }
}

const arrayBufferToBase64 = (buffer) => {
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize)
    binary += String.fromCharCode(...chunk)
  }
  return window.btoa(binary)
}

const base64ToUint8Array = (value) => {
  const binary = window.atob(value)
  const length = binary.length
  const bytes = new Uint8Array(length)
  for (let index = 0; index < length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes
}

const createTransferId = (prefix) => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`

const normalizeTransferPath = (value, fallbackName = 'transfer.bin') => {
  const normalized = String(value || fallbackName)
    .replace(/\\/g, '/')
    .split('/')
    .map(segment => segment.trim())
    .filter(segment => segment && segment !== '.' && segment !== '..')
    .join('/')
  return normalized || fallbackName
}

const createTransferRecord = (payload) => ({
  transferId: payload.transferId,
  direction: payload.direction || 'upload',
  status: payload.status || 'queued',
  progress: normalizeNumber(payload.progress, 0),
  fileName: payload.fileName || '未命名文件',
  displayName: payload.displayName || payload.relativePath || payload.fileName || '未命名文件',
  relativePath: payload.relativePath || '',
  totalBytes: normalizeNumber(payload.totalBytes, 0),
  transferredBytes: normalizeNumber(payload.transferredBytes, 0),
  message: payload.message || '',
  createdAt: payload.createdAt || Date.now(),
  updatedAt: payload.updatedAt || Date.now()
})

const waitForUploadStatus = (transferId, expectedStatuses, options = {}) => {
  const statuses = Array.isArray(expectedStatuses) ? expectedStatuses : [expectedStatuses]
  const timeoutMs = Math.max(2000, normalizeNumber(options.timeoutMs, 15000))
  const timeoutMessage = options.timeoutMessage || '等待上传状态回执超时'
  const currentRecord = transferRecordMap.get(transferId)
  const currentStatus = String(currentRecord?.status || '')

  if (currentStatus && statuses.includes(currentStatus)) {
    return Promise.resolve(currentRecord)
  }

  if (currentStatus === 'failed' || currentStatus === 'canceled') {
    return Promise.reject(new Error(currentRecord?.message || (currentStatus === 'canceled' ? '上传已取消' : '上传失败')))
  }

  return new Promise((resolve, reject) => {
    const waiter = {
      statuses: new Set(statuses.filter(Boolean)),
      resolve,
      reject,
      timer: null
    }

    waiter.timer = window.setTimeout(() => {
      const waiters = uploadStatusWaiters.get(transferId) || []
      const remaining = waiters.filter(item => item !== waiter)
      if (remaining.length) {
        uploadStatusWaiters.set(transferId, remaining)
      } else {
        uploadStatusWaiters.delete(transferId)
      }
      reject(new Error(timeoutMessage))
    }, timeoutMs)

    const waiters = uploadStatusWaiters.get(transferId) || []
    waiters.push(waiter)
    uploadStatusWaiters.set(transferId, waiters)
  })
}

const settleUploadStatusWaiters = (message) => {
  const transferId = String(message?.transfer_id || '')
  if (!transferId || !uploadStatusWaiters.has(transferId)) {
    return
  }

  const status = String(message?.status || '')
  const waiters = uploadStatusWaiters.get(transferId) || []
  const remaining = []

  waiters.forEach(waiter => {
    const isTerminalFailure = status === 'failed' || status === 'canceled'
    const isMatch = waiter.statuses.has(status)

    if (!isTerminalFailure && !isMatch) {
      remaining.push(waiter)
      return
    }

    if (waiter.timer) {
      clearTimeout(waiter.timer)
    }

    if (isTerminalFailure) {
      waiter.reject(new Error(message?.message || (status === 'canceled' ? '上传已取消' : '上传失败')))
    } else {
      waiter.resolve(message)
    }
  })

  if (remaining.length) {
    uploadStatusWaiters.set(transferId, remaining)
  } else {
    uploadStatusWaiters.delete(transferId)
  }
}

const buildTransferStatusText = ({
  direction = 'upload',
  status = 'progress',
  backendMessage = '',
  bytes = 0,
  totalBytes = 0
}) => {
  if (status === 'failed' || status === 'canceled') {
    return backendMessage || (status === 'canceled' ? '传输已取消' : '传输失败')
  }

  if (status === 'queued') {
    return direction === 'upload' ? '等待上传队列' : '等待下载队列'
  }

  if (status === 'canceling') {
    return direction === 'upload' ? '正在取消上传' : '正在取消下载'
  }

  if (status === 'completed') {
    return direction === 'upload' ? '上传完成' : '下载完成'
  }

  if (status === 'started') {
    return direction === 'upload' ? '已开始接收文件' : '已开始发送文件'
  }

  if (status === 'progress') {
    if (totalBytes > 0) {
      return `${direction === 'upload' ? '正在上传' : '正在下载'} ${formatBytes(bytes)} / ${formatBytes(totalBytes)}`
    }
    return direction === 'upload' ? '正在上传文件' : '正在下载文件'
  }

  return backendMessage || '传输处理中'
}

const upsertTransferRecord = (transferId, patch = {}) => {
  if (!transferId) {
    return null
  }

  const existingRecord = transferRecordMap.get(transferId)
  const nextRecord = createTransferRecord({
    ...(existingRecord || {}),
    ...patch,
    transferId,
    createdAt: existingRecord?.createdAt || patch.createdAt || Date.now(),
    updatedAt: patch.updatedAt || Date.now()
  })

  transferRecordMap.set(transferId, nextRecord)

  if (!existingRecord) {
    transferRecords.value = [nextRecord, ...transferRecords.value]
    return nextRecord
  }

  const index = transferRecords.value.findIndex(record => record.transferId === transferId)
  if (index === -1) {
    transferRecords.value = [nextRecord, ...transferRecords.value]
    return nextRecord
  }

  const nextRecords = transferRecords.value.slice()
  nextRecords.splice(index, 1, nextRecord)
  transferRecords.value = nextRecords
  return nextRecord
}

const clearTransferHistory = () => {
  transferRecords.value = transferRecords.value.filter(record => activeTransferStatuses.has(record.status))
  transferRecordMap.clear()
  transferRecords.value.forEach(record => {
    transferRecordMap.set(record.transferId, record)
  })
}

const buildUploadDescriptors = (files) => {
  return Array.from(files || []).map(file => ({
    file,
    relativePath: normalizeTransferPath(file.webkitRelativePath || file.name, file.name)
  }))
}

const handleFileSelection = async (event) => {
  const descriptors = buildUploadDescriptors(event.target?.files || [])
  if (event.target) {
    event.target.value = ''
  }
  await enqueueUploads(descriptors)
}

const handleFolderSelection = async (event) => {
  const descriptors = buildUploadDescriptors(event.target?.files || [])
  if (event.target) {
    event.target.value = ''
  }
  await enqueueUploads(descriptors)
}

const handleTransferDragEnter = () => {
  dragCounter += 1
  fileDropActive.value = true
}

const handleTransferDragOver = () => {
  fileDropActive.value = true
}

const handleTransferDragLeave = () => {
  dragCounter = Math.max(0, dragCounter - 1)
  if (dragCounter === 0) {
    fileDropActive.value = false
  }
}

const readEntryFile = (entry, relativePrefix = '') => new Promise((resolve, reject) => {
  entry.file((file) => {
    resolve([{
      file,
      relativePath: normalizeTransferPath(
        relativePrefix ? `${relativePrefix}/${file.name}` : file.name,
        file.name
      )
    }])
  }, reject)
})

const readDirectoryEntries = (entry) => new Promise((resolve, reject) => {
  const reader = entry.createReader()
  const results = []

  const readNext = () => {
    reader.readEntries((entries) => {
      if (!entries.length) {
        resolve(results)
        return
      }
      results.push(...entries)
      readNext()
    }, reject)
  }

  readNext()
})

const walkDroppedEntry = async (entry, relativePrefix = '') => {
  if (!entry) {
    return []
  }

  if (entry.isFile) {
    return readEntryFile(entry, relativePrefix)
  }

  if (!entry.isDirectory) {
    return []
  }

  const nextPrefix = relativePrefix ? `${relativePrefix}/${entry.name}` : entry.name
  const children = await readDirectoryEntries(entry)
  const descriptors = []
  for (const child of children) {
    descriptors.push(...(await walkDroppedEntry(child, nextPrefix)))
  }
  return descriptors
}

const collectDroppedDescriptors = async (dataTransfer) => {
  const items = Array.from(dataTransfer?.items || [])
  if (items.length) {
    const descriptors = []
    let usedEntries = false

    for (const item of items) {
      const entry = typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null
      if (entry) {
        usedEntries = true
        descriptors.push(...(await walkDroppedEntry(entry)))
      }
    }

    if (usedEntries && descriptors.length) {
      return descriptors
    }
  }

  return buildUploadDescriptors(dataTransfer?.files || [])
}

const handleTransferDrop = async (event) => {
  dragCounter = 0
  fileDropActive.value = false

  const descriptors = await collectDroppedDescriptors(event.dataTransfer)
  if (!descriptors.length) {
    ElMessage.warning('未检测到可上传的文件')
    return
  }

  await enqueueUploads(descriptors)
}

const enqueueUploads = async (descriptors) => {
  if (!isSocketOpen()) {
    ElMessage.warning('远程桌面未连接，无法上传文件')
    return
  }

  const maxSize = Math.max(0, normalizeNumber(remoteCapabilities.value.maxFileSize, 0))
  let acceptedCount = 0

  for (const descriptor of descriptors) {
    const file = descriptor.file
    const relativePath = normalizeTransferPath(descriptor.relativePath, file.name)
    const displayName = relativePath
    const transferId = createTransferId('upload')

    if (maxSize > 0 && file.size > maxSize) {
      upsertTransferRecord(transferId, {
        direction: 'upload',
        status: 'failed',
        fileName: file.name,
        displayName,
        relativePath,
        totalBytes: file.size,
        transferredBytes: 0,
        message: `超过大小限制，最大允许 ${formatTransferLimit(maxSize)}`
      })
      continue
    }

    const controller = { canceled: false }
    activeUploadControllers.set(transferId, controller)
    uploadQueue.push({
      transferId,
      file,
      relativePath,
      fileName: file.name,
      displayName,
      controller
    })
    upsertTransferRecord(transferId, {
      direction: 'upload',
      status: 'queued',
      fileName: file.name,
      displayName,
      relativePath,
      totalBytes: file.size,
      transferredBytes: 0,
      message: '等待上传队列'
    })
    acceptedCount += 1
  }

  if (acceptedCount === 0) {
    ElMessage.warning('没有可上传的有效文件')
    return
  }

  fileTransferState.value.uploadProgress = 0
  fileTransferState.value.uploadStatus = `已加入上传队列: ${acceptedCount} 个文件`
  await processUploadQueue()
}

const processUploadQueue = async () => {
  if (processingUploadQueue) {
    return
  }

  processingUploadQueue = true
  try {
    while (uploadQueue.length) {
      const task = uploadQueue.shift()
      if (!task) {
        continue
      }
      if (task.controller.canceled) {
        upsertTransferRecord(task.transferId, {
          status: 'canceled',
          progress: 0,
          message: '上传已取消'
        })
        activeUploadControllers.delete(task.transferId)
        continue
      }

      await uploadFileToRemote(task)
    }
  } finally {
    processingUploadQueue = false
  }
}

const uploadFileToRemote = async (task) => {
  const { file, relativePath, transferId, fileName, displayName, controller } = task
  fileTransferState.value.uploadProgress = 0
  fileTransferState.value.uploadStatus = `正在初始化上传: ${displayName}`
  upsertTransferRecord(transferId, {
    status: 'started',
    progress: 0,
    message: '正在初始化上传'
  })

  const startAck = waitForUploadStatus(transferId, ['started', 'progress'], {
    timeoutMs: 15000,
    timeoutMessage: `等待远端开始接收 ${displayName} 超时`
  })

  if (!sendSocketMessage({
    type: 'file_upload_start',
    transfer_id: transferId,
    file_name: fileName,
    relative_path: relativePath,
    file_size: file.size
  })) {
    upsertTransferRecord(transferId, {
      status: 'failed',
      message: '无法启动文件上传'
    })
    rejectUploadStatusWaiters(transferId, '无法启动文件上传')
    activeUploadControllers.delete(transferId)
    ElMessage.warning('无法启动文件上传')
    return
  }

  fileTransferState.value.uploadStatus = `等待远端确认上传任务: ${displayName}`
  const chunkSize = Math.max(32 * 1024, Math.round(normalizeNumber(remoteCapabilities.value.chunkSize, 96 * 1024)))
  let offset = 0
  let chunkIndex = 0

  try {
    await startAck

    while (offset < file.size) {
      if (controller.canceled) {
        sendSocketMessage({
          type: 'file_upload_cancel',
          transfer_id: transferId
        })
        upsertTransferRecord(transferId, {
          status: 'canceling',
          message: '正在取消上传'
        })
        return
      }

      const chunk = file.slice(offset, offset + chunkSize)
      const data = arrayBufferToBase64(await chunk.arrayBuffer())
      const progressAck = waitForUploadStatus(transferId, 'progress', {
        timeoutMs: 30000,
        timeoutMessage: `等待远端确认 ${displayName} 的上传进度超时`
      })
      if (!sendSocketMessage({
        type: 'file_upload_chunk',
        transfer_id: transferId,
        chunk_index: chunkIndex,
        data
      })) {
        sendSocketMessage({
          type: 'file_upload_cancel',
          transfer_id: transferId
        })
        upsertTransferRecord(transferId, {
          status: 'failed',
          progress: Math.min(99, Math.round((offset / Math.max(file.size, 1)) * 100)),
          transferredBytes: offset,
          message: '上传中连接已断开'
        })
        rejectUploadStatusWaiters(transferId, '上传中连接已断开')
        ElMessage.error('上传中连接已断开')
        return
      }

      offset += chunk.size
      chunkIndex += 1
      fileTransferState.value.uploadStatus = `等待远端确认数据块: ${displayName}`
      await waitForSocketDrain()
      await progressAck
    }

    const completedAck = waitForUploadStatus(transferId, 'completed', {
      timeoutMs: 20000,
      timeoutMessage: `等待远端确认 ${displayName} 上传完成超时`
    })
    if (!sendSocketMessage({
      type: 'file_upload_finish',
      transfer_id: transferId
    })) {
      upsertTransferRecord(transferId, {
        status: 'failed',
        message: '无法提交上传完成请求'
      })
      rejectUploadStatusWaiters(transferId, '无法提交上传完成请求')
      ElMessage.error(`上传 ${displayName} 失败: 无法提交上传完成请求`)
      return
    }

    fileTransferState.value.uploadStatus = `正在等待远端校验文件: ${displayName}`
    await completedAck
  } catch (error) {
    if (controller.canceled) {
      return
    }

    sendSocketMessage({
      type: 'file_upload_cancel',
      transfer_id: transferId
    })
    fileTransferState.value.uploadStatus = `上传失败: ${error.message}`
    upsertTransferRecord(transferId, {
      status: 'failed',
      progress: Math.min(99, Math.round((offset / Math.max(file.size, 1)) * 100)),
      transferredBytes: offset,
      message: `上传失败: ${error.message}`
    })
    ElMessage.error(`上传 ${displayName} 失败: ${error.message}`)
  } finally {
    if (!controller.canceled) {
      activeUploadControllers.delete(transferId)
    }
  }
}

const handleFileTransferStatus = (message) => {
  const direction = message.direction || 'upload'
  const progress = Math.max(0, Math.min(100, Math.round(normalizeNumber(message.progress, 0))))
  const fileName = message.file?.name || message.file_name || '文件'
  const relativePath = message.file?.relative_path || message.relative_path || ''
  const displayName = relativePath || fileName
  const totalBytes = normalizeNumber(message.total_bytes ?? message.file?.size, 0)
  const transferredBytes = normalizeNumber(message.bytes ?? message.bytes_sent, 0)
  const text = buildTransferStatusText({
    direction,
    status: message.status || 'progress',
    backendMessage: message.message || '',
    bytes: transferredBytes,
    totalBytes
  })

  upsertTransferRecord(message.transfer_id, {
    direction,
    status: message.status || 'progress',
    progress,
    fileName,
    displayName,
    relativePath,
    totalBytes,
    transferredBytes,
    message: text
  })

  if (direction === 'upload') {
    settleUploadStatusWaiters({
      ...message,
      message: text
    })
  }

  if (direction === 'upload') {
    fileTransferState.value.uploadProgress = progress
    fileTransferState.value.uploadStatus = `${text}${displayName ? `: ${displayName}` : ''}`
    if (message.status === 'completed') {
      requestRemoteFileList()
    }
  } else {
    fileTransferState.value.downloadProgress = progress
    fileTransferState.value.downloadStatus = `${text}${displayName ? `: ${displayName}` : ''}`
  }

  if (direction === 'download' && (message.status === 'failed' || message.status === 'canceled')) {
    pendingDownloads.delete(message.transfer_id)
  }
  if (direction === 'upload' && (message.status === 'failed' || message.status === 'completed' || message.status === 'canceled')) {
    activeUploadControllers.delete(message.transfer_id)
  }

  if (message.status === 'failed') {
    ElMessage.error(text)
  } else if (message.status === 'completed') {
    ElMessage.success(text)
  } else if (message.status === 'canceled') {
    ElMessage.warning(text)
  }
}

const downloadRemoteFile = (file) => {
  if (!file?.relative_path) {
    ElMessage.warning('缺少远端文件路径，无法下载')
    return
  }

  const transferId = createTransferId('download')
  pendingDownloads.set(transferId, {
    fileName: file.name,
    chunks: [],
    totalBytes: file.size,
    relativePath: file.relative_path
  })
  upsertTransferRecord(transferId, {
    direction: 'download',
    status: 'started',
    fileName: file.name,
    displayName: file.relative_path || file.name,
    relativePath: file.relative_path,
    totalBytes: file.size,
    transferredBytes: 0,
    message: '正在请求下载'
  })
  fileTransferState.value.downloadProgress = 0
  fileTransferState.value.downloadStatus = `正在请求下载: ${file.name}`

  if (!sendSocketMessage({
    type: 'file_download_request',
    transfer_id: transferId,
    relative_path: file.relative_path
  })) {
    pendingDownloads.delete(transferId)
    upsertTransferRecord(transferId, {
      status: 'failed',
      message: '远程桌面未连接，无法下载文件'
    })
    ElMessage.warning('远程桌面未连接，无法下载文件')
  }
}

const handleDownloadChunk = (message) => {
  const transfer = pendingDownloads.get(message.transfer_id)
  if (!transfer) {
    return
  }

  transfer.chunks.push(base64ToUint8Array(message.data || ''))
  const progress = Math.max(0, Math.min(100, Math.round(normalizeNumber(message.progress, 0))))
  const bytesSent = normalizeNumber(message.bytes_sent, 0)
  fileTransferState.value.downloadProgress = progress
  fileTransferState.value.downloadStatus = `正在下载 ${transfer.fileName} (${formatBytes(bytesSent)} / ${formatBytes(transfer.totalBytes)})`
  upsertTransferRecord(message.transfer_id, {
    direction: 'download',
    status: 'progress',
    progress,
    fileName: transfer.fileName,
    displayName: transfer.relativePath || transfer.fileName,
    relativePath: transfer.relativePath,
    totalBytes: transfer.totalBytes,
    transferredBytes: bytesSent,
    message: `正在下载 ${formatBytes(bytesSent)} / ${formatBytes(transfer.totalBytes)}`
  })
}

const handleDownloadComplete = (message) => {
  const transfer = pendingDownloads.get(message.transfer_id)
  if (!transfer) {
    return
  }

  const blob = new Blob(transfer.chunks, { type: 'application/octet-stream' })
  const objectUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = message.file_name || transfer.fileName || 'download.bin'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(objectUrl)

  pendingDownloads.delete(message.transfer_id)
  fileTransferState.value.downloadProgress = 100
  fileTransferState.value.downloadStatus = `下载完成: ${message.file_name || transfer.fileName}`
  upsertTransferRecord(message.transfer_id, {
    direction: 'download',
    status: 'completed',
    progress: 100,
    fileName: message.file_name || transfer.fileName,
    displayName: transfer.relativePath || message.file_name || transfer.fileName,
    relativePath: transfer.relativePath,
    totalBytes: transfer.totalBytes,
    transferredBytes: transfer.totalBytes,
    message: '下载完成'
  })
}

const cancelTransfer = (record) => {
  if (!record?.transferId || !remoteCapabilities.value.cancelTransfer) {
    return
  }

  if (record.direction === 'upload') {
    const controller = activeUploadControllers.get(record.transferId)
    if (controller) {
      controller.canceled = true
    }
    sendSocketMessage({
      type: 'file_upload_cancel',
      transfer_id: record.transferId
    })
    upsertTransferRecord(record.transferId, {
      status: 'canceling',
      message: '正在取消上传'
    })
    return
  }

  sendSocketMessage({
    type: 'file_download_cancel',
    transfer_id: record.transferId
  })
  upsertTransferRecord(record.transferId, {
    status: 'canceling',
    message: '正在取消下载'
  })
}

const formatTransferStatus = (status) => {
  const statusMap = {
    queued: '排队中',
    started: '已开始',
    progress: '传输中',
    canceling: '取消中',
    completed: '已完成',
    failed: '失败',
    canceled: '已取消'
  }
  return statusMap[status] || '处理中'
}

const transferStatusTagType = (status) => {
  if (status === 'completed') {
    return 'success'
  }
  if (status === 'failed') {
    return 'danger'
  }
  if (status === 'canceled') {
    return 'warning'
  }
  if (status === 'queued' || status === 'canceling') {
    return 'info'
  }
  return 'primary'
}

const formatClientTimestamp = (timestamp) => {
  if (!timestamp) {
    return '-'
  }
  return new Date(timestamp).toLocaleString()
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

const formatBytes = (value) => {
  const bytes = Math.max(0, normalizeNumber(value, 0))
  if (bytes < 1024) {
    return `${Math.round(bytes)} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

const formatTransferLimit = (value) => {
  const bytes = Math.max(0, normalizeNumber(value, 0))
  if (bytes <= 0) {
    return '不限'
  }
  return formatBytes(bytes)
}

const formatTimestamp = (timestamp) => {
  const numericTimestamp = normalizeNumber(timestamp, 0)
  if (!numericTimestamp) {
    return '-'
  }
  return new Date(numericTimestamp * 1000).toLocaleString()
}

const queueFrame = (message, frameId) => {
  if (frameId <= lastRenderedFrameId) {
    return
  }

  if (isDecodingFrame) {
    pendingFrame = { message, frameId }
    return
  }

  drawFrame(message, frameId)
}

const flushPendingFrame = () => {
  if (!pendingFrame) {
    return
  }

  const nextFrame = pendingFrame
  pendingFrame = null
  drawFrame(nextFrame.message, nextFrame.frameId)
}

// ============ H.264（WebCodecs）路径 ============

const resetH264Decoder = () => {
  try {
    if (videoDecoder && videoDecoder.state !== 'closed') {
      videoDecoder.close()
    }
  } catch (e) {
    // ignore
  }
  videoDecoder = null
}

const teardownH264 = () => {
  h264Mode = false
  h264DecodeFailStreak = 0
  h264LastSeq = 0
  resetH264Decoder()
  clearCapabilitiesRetry()
  clearSilentCheckTimer()
}

const sendFrameAck = (seq) => {
  if (seq > 0) {
    sendSocketMessage({ type: 'frame_ack', seq })
  }
}

const renderVideoFrame = (frame) => {
  if (!ctx || !desktopCanvas.value) {
    frame.close()
    return
  }
  applyRemoteResolution(frame.displayWidth, frame.displayHeight)
  ctx.drawImage(frame, 0, 0, frame.displayWidth, frame.displayHeight)
  frame.close()
}

const handleH264Frame = (message) => {
  markRemoteSessionInitialized()
  markSessionConnected()
  const seq = Number(message.seq) || 0
  if (seq <= h264LastSeq) {
    // 乱序/重复帧：直接确认丢弃
    sendFrameAck(seq)
    return
  }
  h264LastSeq = seq

  // 更新分辨率与带宽显示
  applyRemoteResolution(Number(message.width) || 0, Number(message.height) || 0)
  streamResolution.value = formatResolutionText(message.width, message.height, streamResolution.value)
  const now = Date.now()
  if (lastFrameTime > 0) {
    fps.value = Math.round(1000 / Math.max(1, now - lastFrameTime))
  }
  lastFrameTime = now
  frameCounter++
  if (frameCounter % 10 === 0) {
    const dataSize = (message.data || '').length * 0.75 / 1024
    bandwidth.value = `${Math.round(dataSize * Math.max(fps.value, 1))} KB/s`
  }

  try {
    let decoder = videoDecoder
    if (!decoder || decoder.state !== 'configured') {
      sendFrameAck(seq)
      return
    }
    // 解码积压（>2 帧）且收到关键帧 → 重置解码器直跳最新画面（旧帧链整体丢弃）
    if (message.keyframe && decoder.decodeQueueSize > 2) {
      decodeStats.dropped += decoder.decodeQueueSize
      resetH264Decoder()
      decoder = ensureH264Decoder()
      if (!decoder || decoder.state !== 'configured') {
        sendFrameAck(seq)
        return
      }
    }
    const binary = atob(message.data)
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i++) {
      bytes[i] = binary.charCodeAt(i)
    }
    const chunk = new EncodedVideoChunk({
      type: message.keyframe ? 'key' : 'delta',
      timestamp: seq * 1000,
      data: bytes
    })
    decoder.decode(chunk)
    // 渲染由 decoder output 回调完成，此处按帧确认（背压依据）
    sendFrameAck(seq)
  } catch (e) {
    console.error('❌ H.264 解码失败:', e)
    h264DecodeFailStreak += 1
    sendFrameAck(seq)
    if (h264DecodeFailStreak <= 2) {
      // 请求关键帧重建参考链
      sendSocketMessage({ type: 'request_keyframe' })
    } else if (h264DecodeFailStreak >= 6) {
      // 连续失败：整体回退 JPEG
      sendSocketMessage({ type: 'viewer_capabilities', webcodecs: false })
      teardownH264()
    }
  }
}

const ensureH264Decoder = () => {
  if (videoDecoder && videoDecoder.state === 'configured') return videoDecoder
  if (typeof window === 'undefined' || typeof window.VideoDecoder !== 'function') {
    return null
  }
  if (videoDecoder && videoDecoder.state === 'closed') {
    videoDecoder = null
  }
  if (!videoDecoder) {
    try {
      videoDecoder = new VideoDecoder({
        output: (frame) => renderVideoFrame(frame),
        error: (e) => {
          console.error('❌ VideoDecoder error:', e)
          h264DecodeFailStreak += 1
          if (h264DecodeFailStreak >= 6) {
            sendSocketMessage({ type: 'viewer_capabilities', webcodecs: false })
            teardownH264()
          }
        }
      })
      // Annex-B 裸流（无 description），x264 默认 High Profile
      videoDecoder.configure({ codec: 'avc1.640028' })
      h264DecodeFailStreak = 0
    } catch (e) {
      console.error('❌ VideoDecoder 初始化失败:', e)
      return null
    }
  }
  return videoDecoder
}


const drawBitmapFrame = (bitmap, width, height, frameId) => {
  if (!ctx || !desktopCanvas.value) return
  if (frameId < lastRenderedFrameId) return
  const canvas = desktopCanvas.value
  applyRemoteResolution(width, height)
  ctx.clearRect(0, 0, width, height)
  ctx.drawImage(bitmap, 0, 0, width, height)
  lastRenderedFrameId = frameId
  if (bitmap && bitmap.close) bitmap.close()
}

const drawFrame = (message, frameId) => {
  if (!ctx || !desktopCanvas.value) {
    console.error('❌ Canvas未初始化')
    return
  }

  isDecodingFrame = true

  try {
    const img = new Image()

    img.onload = () => {
      if (frameId < lastRenderedFrameId) {
        isDecodingFrame = false
        flushPendingFrame()
        return
      }

      const canvas = desktopCanvas.value

      // 获取远程桌面的实际分辨率
      const remoteWidth = message.width
      const remoteHeight = message.height

      applyRemoteResolution(remoteWidth, remoteHeight)

      // 清空画布
      ctx.clearRect(0, 0, remoteWidth, remoteHeight)

      // 1:1绘制图片，不缩放
      ctx.drawImage(img, 0, 0, remoteWidth, remoteHeight)
      lastRenderedFrameId = frameId
      isDecodingFrame = false
      flushPendingFrame()
    }

    img.onerror = (error) => {
      console.error('❌ 图像加载失败:', error)
      isDecodingFrame = false
      flushPendingFrame()
    }

    img.src = 'data:image/jpeg;base64,' + message.data
  } catch (error) {
    console.error('❌ 绘制错误:', error)
    isDecodingFrame = false
    flushPendingFrame()
  }
}

const drawDemoFrame = () => {
  // 删除演示代码，使用真实画面
}

const isMousePointer = (event) => !event.pointerType || event.pointerType === 'mouse'

const resetPointerState = () => {
  pointerState.activePointerId = null
  pointerState.activeButton = null
  pointerState.dragActive = false
  pointerState.dragMessageSent = false
  pointerState.pressedButtons.clear()
  pointerState.pressClientPosition = null
  pointerState.lastDragClientPosition = null
  pointerState.pendingDragClientDeltaX = 0
  pointerState.pendingDragClientDeltaY = 0
  pointerState.dragRemainderX = 0
  pointerState.dragRemainderY = 0
  pointerState.lastPosition = null
  pointerState.ignoreLostCapture.clear()
  if (
    pointerState.recentRelease &&
    pointerState.recentRelease.expiresAt <= Date.now()
  ) {
    pointerState.recentRelease = null
  }
  lastHoverMoveAt = 0
  lastDragMoveAt = 0
}

const rememberRecentRelease = (pointerId, button) => {
  if (pointerId === null || pointerId === undefined || button === null || button === undefined) {
    return
  }

  pointerState.recentRelease = {
    pointerId,
    button,
    expiresAt: Date.now() + releaseDedupWindowMs
  }
}

const isDuplicateRelease = (pointerId, button) => {
  const recentRelease = pointerState.recentRelease
  if (!recentRelease) {
    return false
  }

  if (recentRelease.expiresAt <= Date.now()) {
    pointerState.recentRelease = null
    return false
  }

  return recentRelease.pointerId === pointerId && recentRelease.button === button
}

const hasExceededDragThreshold = (event) => {
  const start = pointerState.pressClientPosition
  if (!start || typeof event?.clientX !== 'number' || typeof event?.clientY !== 'number') {
    return false
  }

  const deltaX = event.clientX - start.x
  const deltaY = event.clientY - start.y
  return Math.hypot(deltaX, deltaY) >= dragStartThreshold
}

const releasePointerCapture = (pointerId) => {
  const canvas = desktopCanvas.value
  if (!canvas || pointerId === null || pointerId === undefined) {
    return
  }

  if (typeof canvas.hasPointerCapture === 'function' && canvas.hasPointerCapture(pointerId)) {
    canvas.releasePointerCapture(pointerId)
  }
}

const truncateTowardsZero = (value) => {
  if (!Number.isFinite(value)) {
    return 0
  }
  return value < 0 ? Math.ceil(value) : Math.floor(value)
}

const queueDragDelta = (event) => {
  const previousPosition = pointerState.lastDragClientPosition || pointerState.pressClientPosition
  if (!previousPosition || typeof event?.clientX !== 'number' || typeof event?.clientY !== 'number') {
    return
  }

  pointerState.pendingDragClientDeltaX += event.clientX - previousPosition.x
  pointerState.pendingDragClientDeltaY += event.clientY - previousPosition.y
  pointerState.lastDragClientPosition = {
    x: event.clientX,
    y: event.clientY
  }
}

const consumeDragDelta = () => {
  const canvas = desktopCanvas.value
  if (!canvas) {
    return null
  }

  const renderWidth = Math.max(1, canvas.clientWidth)
  const renderHeight = Math.max(1, canvas.clientHeight)
  const remoteWidth = Math.max(1, canvas.width)
  const remoteHeight = Math.max(1, canvas.height)

  const totalRemoteDeltaX = (
    (pointerState.pendingDragClientDeltaX / renderWidth) * remoteWidth
  ) + pointerState.dragRemainderX
  const totalRemoteDeltaY = (
    (pointerState.pendingDragClientDeltaY / renderHeight) * remoteHeight
  ) + pointerState.dragRemainderY

  const relativeDeltaX = truncateTowardsZero(totalRemoteDeltaX)
  const relativeDeltaY = truncateTowardsZero(totalRemoteDeltaY)

  pointerState.pendingDragClientDeltaX = 0
  pointerState.pendingDragClientDeltaY = 0
  pointerState.dragRemainderX = totalRemoteDeltaX - relativeDeltaX
  pointerState.dragRemainderY = totalRemoteDeltaY - relativeDeltaY

  if (relativeDeltaX === 0 && relativeDeltaY === 0) {
    return null
  }

  return {
    relativeDeltaX,
    relativeDeltaY
  }
}

const flushPendingDragMove = (event, force = false) => {
  if (!pointerState.dragActive) {
    return
  }

  if (!force) {
    const now = Date.now()
    if (now - lastDragMoveAt < dragMoveThrottle) {
      return
    }
    lastDragMoveAt = now
  }

  const relativeDelta = consumeDragDelta()
  if (!relativeDelta) {
    if (!(force && !pointerState.dragMessageSent)) {
      return
    }

    sendMouseEvent('drag_move', event, {
      buttonOverride: pointerState.activeButton ?? event?.button ?? 0,
      relativeDeltaX: 0,
      relativeDeltaY: 0
    })
    pointerState.dragMessageSent = true
    return
  }

  sendMouseEvent('drag_move', event, {
    buttonOverride: pointerState.activeButton ?? event?.button ?? 0,
    relativeDeltaX: relativeDelta.relativeDeltaX,
    relativeDeltaY: relativeDelta.relativeDeltaY
  })
  pointerState.dragMessageSent = true
}

const handlePointerDown = (event) => {
  if (!isMousePointer(event)) {
    return
  }

  event.preventDefault()
  desktopCanvas.value?.focus()
  pointerState.recentRelease = null
  pointerState.ignoreLostCapture.delete(event.pointerId)
  pointerState.activePointerId = event.pointerId
  pointerState.activeButton = event.button
  pointerState.dragActive = false
  pointerState.dragMessageSent = false
  pointerState.pressedButtons.add(event.button)
  pointerState.pressClientPosition = {
    x: event.clientX,
    y: event.clientY
  }
  pointerState.lastDragClientPosition = {
    x: event.clientX,
    y: event.clientY
  }
  pointerState.pendingDragClientDeltaX = 0
  pointerState.pendingDragClientDeltaY = 0
  pointerState.dragRemainderX = 0
  pointerState.dragRemainderY = 0
  desktopCanvas.value?.setPointerCapture?.(event.pointerId)
  sendMouseEvent('button_down', event, { buttonOverride: event.button })
}

const handlePointerMove = (event) => {
  if (!isMousePointer(event)) {
    return
  }

  const isPressedPointer = pointerState.activePointerId === event.pointerId && pointerState.pressedButtons.size > 0
  if (isPressedPointer && event.buttons === 0) {
    finishPointerInteraction(event)
    return
  }

  if (isPressedPointer) {
    if (!pointerState.dragActive) {
      pointerState.dragActive = hasExceededDragThreshold(event)
    }

    if (!pointerState.dragActive) {
      return
    }

    queueDragDelta(event)
    flushPendingDragMove(event)
    return
  }

  if (!pointerState.dragActive) {
    const now = Date.now()
    if (now - lastHoverMoveAt < hoverMoveThrottle) {
      return
    }
    lastHoverMoveAt = now
  }

  sendMouseEvent('move', event, {
    buttonOverride: pointerState.activeButton ?? event.button
  })
}

const handlePointerUp = (event) => {
  if (!isMousePointer(event)) {
    return
  }

  event.preventDefault()
  finishPointerInteraction(event, event.button)
}

const handlePointerCancel = (event) => {
  if (!isMousePointer(event)) {
    return
  }

  const canceledButton = typeof event.button === 'number' && event.button >= 0
    ? event.button
    : null
  finishPointerInteraction(event, canceledButton, true)
}

const handleLostPointerCapture = (event) => {
  if (!isMousePointer(event)) {
    return
  }

  if (pointerState.ignoreLostCapture.delete(event.pointerId)) {
    return
  }

  finishPointerInteraction(event, event.button, true)
}

const finishPointerInteraction = (event, button = null, releaseOnly = false) => {
  const targetButton = button ?? pointerState.activeButton
  const pointerId = event?.pointerId ?? pointerState.activePointerId
  if (targetButton === null || targetButton === undefined) {
    resetPointerState()
    return
  }

  if (isDuplicateRelease(pointerId, targetButton)) {
    return
  }

  event?.preventDefault?.()
  flushPendingDragMove(event, true)
  sendMouseEvent('button_up', event, {
    buttonOverride: targetButton,
    // 点击释放优先沿用最近一次已确认的远端坐标，避免 pointerup 再次换算时
    // 因为 pointer capture、轻微抖动或缩放导致 down/up 落在两个不同位置。
    useLastPosition: true
  })
  rememberRecentRelease(pointerId, targetButton)

  pointerState.pressedButtons.delete(targetButton)
  if (pointerId !== null && pointerId !== undefined) {
    pointerState.ignoreLostCapture.add(pointerId)
  }
  releasePointerCapture(pointerState.activePointerId)

  if (pointerState.pressedButtons.size === 0) {
    resetPointerState()
  } else {
    pointerState.activeButton = Array.from(pointerState.pressedButtons)[0] ?? null
  }
}

const handleWheel = (event) => {
  event.preventDefault()
  sendMouseEvent('wheel', event, { wheelSteps: normalizeWheelSteps(event) })
}

const handleContextMenu = (event) => {
  event.preventDefault()
}

// 键盘事件处理
const handleKeyDown = async (event) => {
  event.preventDefault()
  console.log('⌨️ 键盘按下:', event.key, 'code:', event.code)
  if (!event.repeat && isClipboardShortcut(event, 'v')) {
    await pushClipboardToRemote({
      preferSystemClipboard: true,
      silent: true
    })
  }
  sendKeyboardEvent('keydown', event)
}

const handleKeyUp = (event) => {
  event.preventDefault()
  console.log('⌨️ 键盘松开:', event.key, 'code:', event.code)
  sendKeyboardEvent('keyup', event)
  if (isClipboardShortcut(event, 'c') || isClipboardShortcut(event, 'x')) {
    scheduleRemoteClipboardPull()
  }
}

const sendKeyboardEvent = (action, event) => {
  if (connectionStatus.value !== 'connected' || !ws || ws.readyState !== WebSocket.OPEN) {
    console.log('❌ 无法发送键盘事件，连接状态:', connectionStatus.value)
    return
  }

  // 映射特殊键
  const keyMap = {
    'Enter': 'enter',
    'Backspace': 'backspace',
    'Tab': 'tab',
    'Escape': 'esc',
    'Shift': 'shift',
    'Control': 'ctrl',
    'Alt': 'alt',
    'CapsLock': 'capslock',
    'Delete': 'delete',
    'ArrowUp': 'up',
    'ArrowDown': 'down',
    'ArrowLeft': 'left',
    'ArrowRight': 'right',
    'Home': 'home',
    'End': 'end',
    'PageUp': 'pageup',
    'PageDown': 'pagedown',
    'Insert': 'insert',
    'Space': 'space',
    ' ': 'space'
  }

  let key = event.key

  // 检查是否是特殊键
  if (keyMap[key]) {
    key = keyMap[key]
  } else if (key.length === 1) {
    // 单个字符保持原样
    key = key.toLowerCase()
  } else if (key.startsWith('F') && key.length <= 3) {
    // F1-F12 功能键
    key = key.toLowerCase()
  }

  const message = {
    type: 'keyboard',
    action: action,
    key: key,
    code: event.code,
    ctrlKey: event.ctrlKey,
    shiftKey: event.shiftKey,
    altKey: event.altKey
  }

  console.log('📤 发送键盘事件:', message)
  sendSocketMessage(message)
}

const sendMouseEvent = (type, event, options = {}) => {
  if (connectionStatus.value !== 'connected' || !ws || ws.readyState !== WebSocket.OPEN) {
    console.log('❌ 无法发送鼠标事件，连接状态:', connectionStatus.value, 'WebSocket状态:', ws?.readyState)
    return
  }

  const canvas = desktopCanvas.value
  if (!canvas) {
    return
  }

  const resolvedPosition = resolveCanvasPosition(event, options)
  if (!resolvedPosition) {
    return
  }

  const {
    canvasX,
    canvasY,
    normalized_x: normalizedX,
    normalized_y: normalizedY
  } = resolvedPosition

  const button = options.buttonOverride ?? event?.button ?? pointerState.activeButton ?? 0
  const message = {
    type: 'mouse',
    action: type,
    normalized_x: normalizedX,
    normalized_y: normalizedY,
    button,
    buttons: event?.buttons ?? getButtonsMaskFromState(),
    deltaY: event?.deltaY || 0,
    deltaMode: event?.deltaMode || 0,
    wheel_steps: options.wheelSteps ?? 0,
    delta_x: options.relativeDeltaX ?? 0,
    delta_y: options.relativeDeltaY ?? 0
  }

  if (type === 'button_down' || type === 'button_up') {
    console.log('📤 发送鼠标事件:', type, {
      归一化: `${normalizedX.toFixed(3)}, ${normalizedY.toFixed(3)}`,
      Canvas: `${Math.floor(canvasX)}, ${Math.floor(canvasY)}`
    })
  }

  sendSocketMessage(message)
}

const resolveCanvasPosition = (event, options = {}) => {
  const canvas = desktopCanvas.value
  if (!canvas) {
    return null
  }

  if (options.useLastPosition && pointerState.lastPosition) {
    return pointerState.lastPosition
  }

  if (!event || typeof event.clientX !== 'number' || typeof event.clientY !== 'number') {
    return pointerState.lastPosition
  }

  const rect = canvas.getBoundingClientRect()
  const renderWidth = canvas.clientWidth
  const renderHeight = canvas.clientHeight
  if (!rect.width || !rect.height || !renderWidth || !renderHeight) {
    return pointerState.lastPosition
  }

  const { borderLeft, borderTop } = getCanvasBoxMetrics(canvas)
  const canvasWidth = canvas.width
  const canvasHeight = canvas.height
  const usableWidth = Math.max(1, renderWidth)
  const usableHeight = Math.max(1, renderHeight)
  const maxCanvasX = Math.max(0, canvasWidth - 1)
  const maxCanvasY = Math.max(0, canvasHeight - 1)

  const offsetX = event.clientX - rect.left - borderLeft
  const offsetY = event.clientY - rect.top - borderTop
  const rawCanvasX = (offsetX / usableWidth) * canvasWidth
  const rawCanvasY = (offsetY / usableHeight) * canvasHeight
  const canvasX = Math.max(0, Math.min(rawCanvasX, maxCanvasX))
  const canvasY = Math.max(0, Math.min(rawCanvasY, maxCanvasY))
  // 归一化必须按位图全宽/全高换算：按 (size-1) 会放大约 size/(size-1)，
  // 在被控端放大回屏幕尺寸时产生最高约 2px 的系统性右/下偏移。
  const normalized_x = canvasWidth > 0 ? Math.max(0, Math.min(canvasX / canvasWidth, 1)) : 0
  const normalized_y = canvasHeight > 0 ? Math.max(0, Math.min(canvasY / canvasHeight, 1)) : 0

  const position = {
    canvasX,
    canvasY,
    normalized_x,
    normalized_y
  }

  pointerState.lastPosition = position
  return position
}

const getResolutionScaleOptions = (currentPercent) => {
  const normalizedCurrent = Math.max(
    40,
    Math.min(100, Math.round(normalizeNumber(currentPercent, sessionSettings.value.scalePercent)))
  )
  return Array.from(new Set([...baseResolutionScaleOptions, normalizedCurrent])).sort((left, right) => left - right)
}

const getResolutionOptionLabel = (scalePercent) => {
  const width = Math.max(1, Math.round(remoteScreenSize.value.width * (scalePercent / 100)))
  const height = Math.max(1, Math.round(remoteScreenSize.value.height * (scalePercent / 100)))
  const prefix = scalePercent >= 100 ? '原始' : `${scalePercent}%`
  return `${prefix} (${width}x${height})`
}

const getButtonsMaskFromState = () => {
  let mask = 0
  if (pointerState.pressedButtons.has(0)) {
    mask |= 1
  }
  if (pointerState.pressedButtons.has(2)) {
    mask |= 2
  }
  if (pointerState.pressedButtons.has(1)) {
    mask |= 4
  }
  return mask
}

const normalizeWheelSteps = (event) => {
  let pixelDelta = event.deltaY
  if (event.deltaMode === 1) {
    pixelDelta *= 40
  } else if (event.deltaMode === 2) {
    pixelDelta *= 400
  }

  if (pixelDelta === 0) {
    return 0
  }

  const normalizedDelta = Math.abs(pixelDelta) / 96
  const magnitude = Math.max(1, Math.min(12, Math.round(normalizedDelta)))
  return pixelDelta < 0 ? magnitude : -magnitude
}

const handlePresetChange = (preset) => {
  applyPresetToSettings(preset)
}

const getPresetSettings = (preset) => {
  if (preset === 'smooth') {
    return {
      preset: 'smooth',
      quality: 55,
      fps: 12,
      scalePercent: 70,
      adaptive: true
    }
  }

  if (preset === 'balanced') {
    return {
      preset: 'balanced',
      quality: 75,
      fps: 18,
      scalePercent: 90,
      adaptive: true
    }
  }

  if (preset === 'high') {
    return {
      preset: 'high',
      quality: 90,
      fps: 24,
      scalePercent: 100,
      adaptive: false
    }
  }

  return {
    preset: 'custom'
  }
}

const applyPresetToSettings = (preset) => {
  Object.assign(settingsForm.value, getPresetSettings(preset || settingsForm.value.preset))
}

const markPresetCustom = () => {
  if (settingsForm.value.preset !== 'custom') {
    settingsForm.value.preset = 'custom'
  }
}

const normalizeSettingsState = (source, fallback = sessionSettings.value) => ({
  quality: Math.max(35, Math.min(90, Math.round(normalizeNumber(source.quality, fallback.quality)))),
  fps: Math.max(4, Math.min(30, Math.round(normalizeNumber(source.fps, fallback.fps)))),
  scalePercent: Math.max(40, Math.min(100, Math.round(normalizeNumber(source.scalePercent, fallback.scalePercent)))),
  adaptive: Boolean(source.adaptive),
  profile: source.profile || fallback.profile || 'interactive',
  wheelSpeed: Math.max(0.5, Math.min(3, normalizeNumber(source.wheelSpeed, fallback.wheelSpeed))),
  mouseSensitivity: Math.max(0.5, Math.min(2, normalizeNumber(source.mouseSensitivity, fallback.mouseSensitivity))),
  preset: source.preset || fallback.preset || 'custom',
  desktopWidth: Math.max(
    1,
    Math.round(
      normalizeNumber(
        source.desktopWidth,
        fallback.desktopWidth || remoteScreenSize.value.width
      )
    )
  ),
  desktopHeight: Math.max(
    1,
    Math.round(
      normalizeNumber(
        source.desktopHeight,
        fallback.desktopHeight || remoteScreenSize.value.height
      )
    )
  ),
  autoReconnect: Boolean(source.autoReconnect)
})

const applySessionSettingsState = (nextSettings) => {
  sessionSettings.value = { ...nextSettings }
  settingsForm.value = { ...nextSettings }
}

const sendSettingsPayload = (nextSettings) => {
  if (connectionStatus.value !== 'connected' || !ws || ws.readyState !== WebSocket.OPEN) {
    return false
  }

  sendSocketMessage({
    type: 'settings',
    quality: nextSettings.quality,
    fps: nextSettings.fps,
    desktop_width: nextSettings.desktopWidth,
    desktop_height: nextSettings.desktopHeight,
    scale_percent: nextSettings.scalePercent,
    adaptive: nextSettings.adaptive,
    wheel_speed: nextSettings.wheelSpeed,
    mouse_sensitivity: nextSettings.mouseSensitivity,
    preset: nextSettings.preset || 'custom'
  })
  return true
}

const commitSessionSettings = (source, options = {}) => {
  const {
    requireConnection = false,
    closeDialog = false,
    successMessage = '',
    silentWhenDisconnected = false
  } = options

  const nextSettings = normalizeSettingsState(source)
  const sent = sendSettingsPayload(nextSettings)

  if (!sent && requireConnection) {
    ElMessage.warning('远程桌面未连接，无法应用设置')
    return false
  }

  applySessionSettingsState(nextSettings)

  if (!sent && !silentWhenDisconnected) {
    ElMessage.info('设置已保存在本地，会在下次连接后继续使用')
  } else if (successMessage) {
    ElMessage.success(successMessage)
  }

  if (closeDialog) {
    settingsVisible.value = false
  }

  return true
}

const handleSettingsResolutionChange = (scalePercent) => {
  settingsForm.value.scalePercent = Math.max(40, Math.min(100, Math.round(normalizeNumber(scalePercent, settingsForm.value.scalePercent))))
  markPresetCustom()
}

const handleSettingsDesktopResolutionChange = (value) => {
  const parsed = parseDesktopResolutionValue(
    value,
    settingsForm.value.desktopWidth || remoteScreenSize.value.width,
    settingsForm.value.desktopHeight || remoteScreenSize.value.height
  )
  settingsForm.value.desktopWidth = parsed.width
  settingsForm.value.desktopHeight = parsed.height
}

const handleFullscreenPresetChange = (preset) => {
  const nextSettings = normalizeSettingsState({
    ...sessionSettings.value,
    ...getPresetSettings(preset),
    autoReconnect: sessionSettings.value.autoReconnect,
    wheelSpeed: sessionSettings.value.wheelSpeed,
    mouseSensitivity: sessionSettings.value.mouseSensitivity
  })
  commitSessionSettings(nextSettings, {
    silentWhenDisconnected: true
  })
}

const handleFullscreenResolutionChange = (scalePercent) => {
  commitSessionSettings({
    ...sessionSettings.value,
    scalePercent,
    preset: 'custom'
  }, {
    silentWhenDisconnected: true
  })
}

const handleFullscreenDesktopResolutionChange = (value) => {
  const parsed = parseDesktopResolutionValue(
    value,
    sessionSettings.value.desktopWidth || remoteScreenSize.value.width,
    sessionSettings.value.desktopHeight || remoteScreenSize.value.height
  )
  commitSessionSettings({
    ...sessionSettings.value,
    desktopWidth: parsed.width,
    desktopHeight: parsed.height
  }, {
    silentWhenDisconnected: true
  })
}

const handleFullscreenWheelSpeedChange = (wheelSpeed) => {
  commitSessionSettings({
    ...sessionSettings.value,
    wheelSpeed,
    preset: 'custom'
  }, {
    silentWhenDisconnected: true
  })
}

const handleFullscreenMouseSensitivityChange = (mouseSensitivity) => {
  commitSessionSettings({
    ...sessionSettings.value,
    mouseSensitivity,
    preset: 'custom'
  }, {
    silentWhenDisconnected: true
  })
}

const handleFullscreenAutoReconnectChange = (autoReconnect) => {
  commitSessionSettings({
    ...sessionSettings.value,
    autoReconnect
  }, {
    silentWhenDisconnected: true
  })
}

const toggleFullscreen = () => {
  const container = desktopContainer.value
  if (!container) return

  if (!document.fullscreenElement) {
    // 尝试全屏
    if (container.requestFullscreen) {
      container.requestFullscreen().catch(err => {
        console.error('全屏失败:', err)
        ElMessage.warning('浏览器不支持全屏或被阻止')
      })
    } else if (container.webkitRequestFullscreen) {
      // Safari
      container.webkitRequestFullscreen()
    } else if (container.mozRequestFullScreen) {
      // Firefox
      container.mozRequestFullScreen()
    } else if (container.msRequestFullscreen) {
      // IE/Edge
      container.msRequestFullscreen()
    } else {
      ElMessage.warning('浏览器不支持全屏功能')
    }
  } else {
    // 退出全屏
    if (document.exitFullscreen) {
      document.exitFullscreen()
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen()
    } else if (document.mozCancelFullScreen) {
      document.mozCancelFullScreen()
    } else if (document.msExitFullscreen) {
      document.msExitFullscreen()
    }
  }
}

const exitFullscreenIfNeeded = async () => {
  const container = desktopContainer.value
  const isCurrentDesktopFullscreen = document.fullscreenElement
    && (!container || document.fullscreenElement === container)

  if (!isCurrentDesktopFullscreen) {
    return
  }

  try {
    if (document.exitFullscreen) {
      await document.exitFullscreen()
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen()
    } else if (document.mozCancelFullScreen) {
      document.mozCancelFullScreen()
    } else if (document.msExitFullscreen) {
      document.msExitFullscreen()
    }
  } catch (error) {
    console.error('退出全屏失败:', error)
  }
}

const showSettings = async () => {
  handleFullscreenActivity()
  settingsForm.value = { ...sessionSettings.value }
  settingsVisible.value = true
}

const resetSettingsForm = () => {
  settingsForm.value = { ...sessionSettings.value }
}

const applySettings = () => {
  commitSessionSettings(settingsForm.value, {
    requireConnection: true,
    closeDialog: true,
    successMessage: '远程桌面设置已应用'
  })
}

const getPresetLabel = (preset) => {
  const item = presetOptions.find(option => option.value === preset)
  return item?.label || '自定义'
}

const getStatusText = () => {
  const statusMap = {
    disconnected: '未连接',
    connecting: awaitingConsent.value ? '等待确认' : '连接中',
    connected: '已连接',
    error: '连接错误'
  }
  return statusMap[connectionStatus.value] || '未知'
}

const performCloseCleanup = async () => {
  if (isClosing.value) {
    return
  }

  isClosing.value = true
  fullscreenToolbarVisible.value = false
  settingsVisible.value = false
  clipboardVisible.value = false
  fileTransferVisible.value = false

  try {
    await exitFullscreenIfNeeded()
    disconnect({ suppressReconnect: true })
  } finally {
    isClosing.value = false
  }
}

const cleanupRemoteDialogArtifacts = () => {
  if (typeof document === 'undefined' || dialogVisible.value) {
    return
  }

  document.querySelectorAll('.web-remote-desktop-overlay').forEach(element => {
    element.remove()
  })

  if (!document.querySelector('.el-overlay')) {
    document.body.classList.remove('el-popup-parent--hidden')
    document.body.classList.remove('el-overflow-hidden')
    document.body.style.removeProperty('overflow')
    document.body.style.removeProperty('width')
    document.body.style.removeProperty('padding-right')
  }
}

const scheduleRemoteDialogArtifactCleanup = () => {
  if (typeof window === 'undefined') {
    cleanupRemoteDialogArtifacts()
    return
  }

  window.setTimeout(() => {
    cleanupRemoteDialogArtifacts()
  }, 250)
}

const syncParentVisibilityClosed = () => {
  if (closeNotified.value) {
    return
  }

  closeNotified.value = true
  emit('update:visible', false)
}

const notifyParentClosed = () => {
  syncParentVisibilityClosed()
  emit('request-close')
  emit('close')
}

const requestDialogClose = async (done) => {
  logRemoteDesktop('request close start', {
    dialogVisible: dialogVisible.value,
    isClosing: isClosing.value
  })
  if (isClosing.value) {
    if (typeof done === 'function') {
      done()
    }
    return
  }

  try {
    await performCloseCleanup()
  } catch (error) {
    console.error('关闭远程桌面时清理失败:', error)
  } finally {
    logRemoteDesktop('request close finalize')
    dialogVisible.value = false
    if (typeof done === 'function') {
      done()
    }
    await nextTick()
    syncParentVisibilityClosed()
    scheduleRemoteDialogArtifactCleanup()
  }
}

const handleDialogBeforeClose = (done) => {
  void requestDialogClose(done)
}

const handleDialogClosed = () => {
  logRemoteDesktop('closed event')
  cleanupRemoteDialogArtifacts()
  notifyParentClosed()
}

const handleClose = () => {
  logRemoteDesktop('handle close click', {
    dialogVisible: dialogVisible.value
  })
  if (!dialogVisible.value) {
    notifyParentClosed()
    return
  }

  void requestDialogClose()
}

const normalizeNumber = (value, fallback) => {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : fallback
}
</script>

<style scoped>
.remote-container {
  display: flex;
  flex-direction: column;
  height: 70vh;
}

.toolbar {
  padding: 10px;
  background: #f5f7fa;
  border-bottom: 1px solid #dcdfe6;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toolbar .info {
  color: #606266;
  font-size: 14px;
  margin: 0 10px;
}

.desktop-container {
  flex: 1;
  position: relative;
  background: #2c3e50;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  user-select: none;
}

.desktop-container.is-fullscreen {
  box-sizing: border-box;
  overflow: visible;
}

.fullscreen-toolbar-handle {
  position: absolute;
  top: 14px;
  right: 16px;
  z-index: 21;
  box-shadow: 0 10px 24px rgba(7, 17, 27, 0.28);
}

.fullscreen-toolbar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 20;
  padding: 10px 14px;
  background: linear-gradient(180deg, rgba(7, 17, 27, 0.94), rgba(7, 17, 27, 0.72));
  backdrop-filter: blur(10px);
  color: #f5f7fa;
  border-bottom: 1px solid rgba(255, 255, 255, 0.12);
  overflow: visible;
  pointer-events: auto;
}

.fullscreen-info {
  color: rgba(245, 247, 250, 0.88);
  font-size: 13px;
}

.fullscreen-field {
  color: rgba(245, 247, 250, 0.72);
  font-size: 12px;
}

.desktop-container canvas {
  display: block;
  background: #000;
  border: 1px solid #dcdfe6;
  box-sizing: content-box;
  cursor: default;
  outline: none;
  touch-action: none;
  user-select: none;
  /* 禁用图像平滑以提高清晰度 */
  image-rendering: -webkit-optimize-contrast;
  image-rendering: crisp-edges;
  image-rendering: pixelated;
  /* 保持原始尺寸，不拉伸 */
  object-fit: contain;
}

.connecting-mask,
.error-mask,
.disconnected-mask {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.95);
  z-index: 10;
}

.connecting-mask p,
.error-mask p,
.disconnected-mask p {
  margin-top: 20px;
  font-size: 16px;
  color: #606266;
}

.statusbar {
  padding: 8px 10px;
  background: #f5f7fa;
  border-top: 1px solid #dcdfe6;
  font-size: 12px;
  color: #909399;
}

.warning-text {
  color: #e67e22;
  font-weight: 600;
}

.settings-hint {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
  margin-top: 8px;
}

.clipboard-panel,
.file-transfer-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.clipboard-toolbar,
.file-transfer-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.clipboard-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}

.clipboard-column {
  min-width: 0;
}

.clipboard-title,
.transfer-title {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.clipboard-hint,
.transfer-text,
.transfer-directory,
.file-list-loading {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}

.transfer-directory {
  word-break: break-all;
}

.transfer-dropzone {
  padding: 18px 20px;
  border-radius: 12px;
  border: 1px dashed #cbd5e1;
  background: linear-gradient(135deg, #f8fafc 0%, #eef6ff 100%);
  transition: border-color 0.2s ease, background 0.2s ease, transform 0.2s ease;
}

.transfer-dropzone.is-active {
  border-color: #409eff;
  background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
  transform: translateY(-1px);
}

.transfer-dropzone-title {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
  margin-bottom: 6px;
}

.transfer-dropzone-hint,
.transfer-overview-label,
.transfer-overview-text {
  font-size: 12px;
  color: #64748b;
  line-height: 1.6;
}

.transfer-overview {
  display: grid;
  grid-template-columns: 140px repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.transfer-overview-card {
  padding: 14px 16px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #ebeef5;
  min-width: 0;
}

.transfer-overview-value {
  font-size: 28px;
  line-height: 1.1;
  font-weight: 700;
  color: #111827;
}

.transfer-block {
  padding: 14px 16px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid #ebeef5;
}

.transfer-text {
  margin-bottom: 10px;
}

.file-list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.hidden-upload-input {
  display: none;
}

:deep(.remote-fullscreen-select-popper) {
  z-index: 4000 !important;
}

:deep(.desktop-container.is-fullscreen .el-overlay-dialog) {
  padding: 24px;
  box-sizing: border-box;
}

@media (max-width: 960px) {
  .clipboard-grid {
    grid-template-columns: 1fr;
  }

  .transfer-overview {
    grid-template-columns: 1fr;
  }
}
</style>
