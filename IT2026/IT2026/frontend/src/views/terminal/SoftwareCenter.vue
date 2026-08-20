<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">软件管理</h2>
        <div class="zv-page-subtitle">仓库 {{ repoTotal }} 个 · 任务 {{ taskStats.total || 0 }} 个 · 策略 {{ whiteList.length + blackList.length }} 项</div>
      </div>
      <div class="zv-page-actions">
        <el-button type="primary" :icon="Plus" @click="showUploadDialog">上传软件</el-button>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="zv-tabs">
      <!-- 软件仓库 -->
      <el-tab-pane label="软件仓库" name="repo">
        <div class="zv-card">
          <div class="zv-filter-bar">
            <el-input v-model="repoSearch" placeholder="搜索软件" clearable :prefix-icon="'Search'" style="width: 280px" />
            <el-select v-model="repoCategory" placeholder="全部分类" clearable style="width: 160px; margin-left: 12px;">
              <el-option v-for="c in categories" :key="c" :label="c" :value="c" />
            </el-select>
          </div>
          <el-table v-loading="repoLoading" :data="filteredRepo">
            <el-table-column label="软件" min-width="240">
              <template #default="{ row }">
                <div class="zv-pkg-cell">
                  <div class="zv-pkg-icon" :style="{ background: getPkgColor(row.category) }">
                    <el-icon :size="20"><Box /></el-icon>
                  </div>
                  <div>
                    <div class="zv-pkg-name">{{ row.name }}</div>
                    <div class="zv-pkg-version">{{ row.version }}</div>
                  </div>
                </div>
              </template>
            </el-table-column>
            <el-table-column prop="vendor" label="厂商" min-width="140" show-overflow-tooltip />
            <el-table-column prop="category" label="分类" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="light">{{ row.category || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="100">
              <template #default="{ row }">{{ formatSize(row.size) }}</template>
            </el-table-column>
            <el-table-column label="安装数" width="100" align="center">
              <template #default="{ row }">
                <span class="zv-mono">{{ row.install_count || 0 }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="160" align="right">
              <template #default="{ row }">
                <el-button text type="primary" size="small" @click="showTaskDialog(row)">分发</el-button>
                <el-button text type="danger" size="small" @click="handleDeletePackage(row)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- 软件任务 -->
      <el-tab-pane label="软件任务" name="task">
        <div class="zv-card">
          <el-table v-loading="taskLoading" :data="taskList">
            <el-table-column label="软件" min-width="180">
              <template #default="{ row }">
                <span class="zv-mono">{{ row.package_name || row.package_id }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="light" :type="getActionType(row.action)">
                  {{ getActionText(row.action) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="目标" width="120">
              <template #default="{ row }">
                <span class="zv-mono">{{ row.target_count }} 台</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="getTaskStatusType(row.status)" effect="light">
                  {{ getTaskStatusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="170">
              <template #default="{ row }"><span class="zv-mono">{{ row.created_at }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="120" align="right">
              <template #default="{ row }">
                <el-button v-if="row.status === 'pending'" text type="warning" size="small" @click="handleCancelTask(row)">取消</el-button>
                <el-button text type="primary" size="small" @click="viewTaskResult(row)">详情</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-tab-pane>

      <!-- 软件策略 -->
      <el-tab-pane label="黑白名单" name="policy">
        <div class="zv-policy-grid">
          <div class="zv-card zv-card-pad">
            <h3 class="zv-section-title">
              <el-icon><Select /></el-icon>
              白名单（必装基线）
            </h3>
            <el-table v-loading="policyLoading" :data="whiteList" size="small">
              <el-table-column prop="software_name" label="软件" />
              <el-table-column prop="version" label="版本" width="120" />
              <el-table-column label="操作" width="100" align="right">
                <template #default="{ row }">
                  <el-button text type="danger" size="small" @click="removePolicy('white', row)">移除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div class="zv-policy-add">
              <el-input v-model="newWhiteName" placeholder="软件名" size="small" style="width: 180px" />
              <el-input v-model="newWhiteVersion" placeholder="版本" size="small" style="width: 100px" />
              <el-button size="small" type="primary" @click="addPolicy('white')">添加</el-button>
            </div>
          </div>

          <div class="zv-card zv-card-pad">
            <h3 class="zv-section-title">
              <el-icon><CloseBold /></el-icon>
              黑名单（禁止安装）
            </h3>
            <el-table v-loading="policyLoading" :data="blackList" size="small">
              <el-table-column prop="software_name" label="软件" />
              <el-table-column prop="version" label="版本" width="120" />
              <el-table-column label="操作" width="100" align="right">
                <template #default="{ row }">
                  <el-button text type="danger" size="small" @click="removePolicy('black', row)">移除</el-button>
                </template>
              </el-table-column>
            </el-table>
            <div class="zv-policy-add">
              <el-input v-model="newBlackName" placeholder="软件名" size="small" style="width: 180px" />
              <el-input v-model="newBlackVersion" placeholder="版本" size="small" style="width: 100px" />
              <el-button size="small" type="primary" @click="addPolicy('black')">添加</el-button>
            </div>
          </div>
        </div>
      </el-tab-pane>
      <!-- 软件策略（8082 完整策略 CRUD：黑名单/白名单/强制安装） -->
      <el-tab-pane label="软件策略" name="policy-mgmt" lazy>
        <PolicyManagement />
      </el-tab-pane>

      <!-- 合规检查 -->
      <el-tab-pane label="合规检查" name="compliance" lazy>
        <ComplianceManagement />
      </el-tab-pane>

      <!-- 全网软件清单 -->
      <el-tab-pane label="软件清单" name="inventory" lazy>
        <InstalledSoftware />
      </el-tab-pane>
    </el-tabs>

    <!-- 上传对话框 -->
    <el-dialog v-model="uploadDialogVisible" title="上传软件" width="500px" destroy-on-close>
      <el-form :model="uploadForm" label-width="100px">
        <el-form-item label="软件包">
          <el-upload :auto-upload="false" :limit="1" :on-change="(f) => uploadForm.file = f">
            <el-button :icon="Upload">选择文件</el-button>
            <span v-if="uploadForm.file" class="zv-upload-name">{{ uploadForm.file.name }}</span>
          </el-upload>
        </el-form-item>
        <el-form-item label="软件名"><el-input v-model="uploadForm.name" /></el-form-item>
        <el-form-item label="版本"><el-input v-model="uploadForm.version" /></el-form-item>
        <el-form-item label="厂商"><el-input v-model="uploadForm.vendor" /></el-form-item>
        <el-form-item label="分类">
          <el-input v-model="uploadForm.category" placeholder="例如：办公、开发、安全" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- 分发对话框 -->
    <el-dialog v-model="taskDialogVisible" title="分发任务" width="500px" destroy-on-close>
      <el-form :model="taskForm" label-width="100px">
        <el-form-item label="软件">
          <span class="zv-mono">{{ currentPackage?.name }} {{ currentPackage?.version }}</span>
        </el-form-item>
        <el-form-item label="操作">
          <el-radio-group v-model="taskForm.action">
            <el-radio value="install">安装</el-radio>
            <el-radio value="upgrade">升级</el-radio>
            <el-radio value="uninstall">卸载</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="目标终端">
          <el-select v-model="taskForm.target_type" style="width: 100%">
            <el-option label="全部终端" value="all" />
            <el-option label="按分组" value="group" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="taskForm.target_type === 'group'" label="选择分组">
          <el-select v-model="taskForm.group_ids" multiple filterable style="width: 100%">
            <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="taskDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitTask">下发</el-button>
      </template>
    </el-dialog>

    <!-- 任务详情对话框 -->
    <el-dialog v-model="taskDetailVisible" title="任务详情" width="720px" destroy-on-close>
      <div v-loading="taskDetailLoading">
        <template v-if="taskDetail">
          <div class="zv-detail-row"><span class="zv-label">软件</span><span class="zv-mono">{{ taskDetail.software_name || taskDetail.package_display_name || '-' }}</span></div>
          <div class="zv-detail-row"><span class="zv-label">操作</span>{{ getActionText(taskDetail.task_type || taskDetail.action) }}</div>
          <div class="zv-detail-row"><span class="zv-label">状态</span><el-tag size="small" :type="getTaskStatusType(taskDetail.status)">{{ getTaskStatusText(taskDetail.status) }}</el-tag></div>
          <div class="zv-detail-row"><span class="zv-label">目标</span>{{ taskDetail.target_count || (taskDetail.results || []).length }} 台终端</div>
          <div class="zv-detail-row zv-detail-full"><span class="zv-label">执行明细</span>
            <el-table :data="taskDetail.results || []" size="small" max-height="320">
              <el-table-column prop="hostname" label="主机" min-width="120" />
              <el-table-column prop="ip_address" label="IP" min-width="120" />
              <el-table-column label="结果" width="90">
                <template #default="{ row }">
                  <el-tag size="small" :type="getTaskStatusType(row.status)">{{ getTaskStatusText(row.status) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="message" label="消息" min-width="180" show-overflow-tooltip />
              <template #empty><el-empty description="暂无执行记录" :image-size="60" /></template>
            </el-table>
          </div>
        </template>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Search, Box, Select, CloseBold, Upload
} from '@element-plus/icons-vue'
import {
  getSoftwarePackages, getSoftwarePackageStats,
  getSoftwareTasks, getSoftwareTaskStats, getSoftwareTaskDetail,
  createSoftwareTask, cancelSoftwareTask as cancelTaskApi,
  uploadSoftwarePackage, deleteSoftwarePackage as deletePackageApi
} from '@/api/software'
import { getGroups } from '@/api/group'
import { getPolicies, createPolicy, deletePolicy } from '@/api/policy'
import PolicyManagement from '@/views/terminal/components/PolicyManagement.vue'
import ComplianceManagement from '@/views/terminal/components/ComplianceManagement.vue'
import InstalledSoftware from '@/views/terminal/components/InstalledSoftware.vue'

const activeTab = ref('repo')
const repoList = ref([])
const repoTotal = ref(0)
const taskList = ref([])
const taskStats = ref({})
const whiteList = ref([])
const blackList = ref([])
const groups = ref([])
const stats = ref({})
const categories = ref([])

const repoSearch = ref('')
const repoCategory = ref('')
const repoLoading = ref(false)
const taskLoading = ref(false)
const policyLoading = ref(false)

const newWhiteName = ref('')
const newWhiteVersion = ref('')
const newBlackName = ref('')
const newBlackVersion = ref('')

const uploadDialogVisible = ref(false)
const uploading = ref(false)
const uploadForm = reactive({ file: null, name: '', version: '', vendor: '', category: '' })

const taskDialogVisible = ref(false)
const submitting = ref(false)
const currentPackage = ref(null)
const taskForm = reactive({ action: 'install', target_type: 'all', group_ids: [] })

const taskDetailVisible = ref(false)
const taskDetailLoading = ref(false)
const taskDetail = ref(null)

const filteredRepo = computed(() => {
  let result = repoList.value
  if (repoCategory.value) result = result.filter(p => p.category === repoCategory.value)
  if (repoSearch.value) {
    const q = repoSearch.value.toLowerCase()
    result = result.filter(p => p.name.toLowerCase().includes(q) || (p.vendor || '').toLowerCase().includes(q))
  }
  return result
})

const getPkgColor = (cat) => {
  const map = { 办公: '#3b82f6', 开发: '#10b981', 安全: '#ef4444', 通讯: '#8b5cf6', 工具: '#f59e0b' }
  return map[cat] || '#64748b'
}

const formatSize = (bytes) => {
  if (!bytes) return '-'
  if (bytes > 1024*1024*1024) return (bytes/1024/1024/1024).toFixed(2) + ' GB'
  if (bytes > 1024*1024) return (bytes/1024/1024).toFixed(2) + ' MB'
  if (bytes > 1024) return (bytes/1024).toFixed(1) + ' KB'
  return bytes + ' B'
}

const getActionType = (a) => ({ install: 'success', upgrade: 'warning', uninstall: 'info' }[a] || 'info')
const getActionText = (a) => ({ install: '安装', upgrade: '升级', uninstall: '卸载' }[a] || a)
const getTaskStatusType = (s) => ({ success: 'success', completed: 'success', failed: 'danger', running: 'warning', pending: 'info' }[s] || 'info')
const getTaskStatusText = (s) => ({ success: '成功', completed: '成功', failed: '失败', running: '运行中', pending: '等待中' }[s] || s)

const loadRepo = async () => {
  repoLoading.value = true
  try {
    const res = await getSoftwarePackages({ page: 1, page_size: 200 })
    repoList.value = res.data || []
    repoTotal.value = res.total || repoList.value.length
    categories.value = [...new Set(repoList.value.map(p => p.category).filter(Boolean))]
  } catch (error) {
    console.error('加载软件仓库失败', error)
  } finally {
    repoLoading.value = false
  }
}

const loadTasks = async () => {
  taskLoading.value = true
  try {
    const res = await getSoftwareTasks({ page: 1, page_size: 50 })
    taskList.value = res.data || []
  } catch (error) {
    console.error('加载任务失败', error)
  } finally {
    taskLoading.value = false
  }
}

const loadPolicies = async () => {
  policyLoading.value = true
  try {
    // 后端 /policies 返回分页策略列表（含 rules），按 policy_type 拉取后摊平为软件条目
    const [white, black] = await Promise.all([
      getPolicies({ policy_type: 'whitelist', page: 1, page_size: 100 }),
      getPolicies({ policy_type: 'blacklist', page: 1, page_size: 100 })
    ])
    const flatten = (policies) => {
      const rows = []
      for (const p of (policies.data || [])) {
        for (const rule of (p.rules || [])) {
          if (rule.rule_type === 'software_name') {
            rows.push({ policy_id: p.id, name: rule.rule_value, version: p.description || '' })
          }
        }
      }
      return rows
    }
    whiteList.value = flatten(white)
    blackList.value = flatten(black)
  } catch (error) {
    console.error('加载策略失败', error)
  } finally {
    policyLoading.value = false
  }
}

const loadStats = async () => {
  try {
    const data = await getSoftwarePackageStats()
    stats.value = data || {}
    taskStats.value = await getSoftwareTaskStats()
  } catch {}
}

const loadGroups = async () => {
  try {
    const res = await getGroups()
    groups.value = res.data || []
  } catch {}
}

const showUploadDialog = () => {
  uploadForm.file = null
  uploadForm.name = ''
  uploadForm.version = ''
  uploadForm.vendor = ''
  uploadForm.category = ''
  uploadDialogVisible.value = true
}

const submitUpload = async () => {
  if (!uploadForm.file) return ElMessage.warning('请选择文件')
  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', uploadForm.file.raw)
    formData.append('name', uploadForm.name)
    formData.append('version', uploadForm.version)
    formData.append('vendor', uploadForm.vendor)
    formData.append('category', uploadForm.category)
    await uploadSoftwarePackage(formData)
    ElMessage.success('上传成功')
    uploadDialogVisible.value = false
    loadRepo()
  } catch (error) {
    ElMessage.error('上传失败')
  } finally {
    uploading.value = false
  }
}

const showTaskDialog = (pkg) => {
  currentPackage.value = pkg
  taskForm.action = 'install'
  taskForm.target_type = 'all'
  taskForm.group_ids = []
  taskDialogVisible.value = true
}

const submitTask = async () => {
  submitting.value = true
  try {
    await createSoftwareTask({
      package_id: currentPackage.value.id,
      action: taskForm.action,
      target_type: taskForm.target_type,
      group_ids: taskForm.group_ids
    })
    ElMessage.success('任务已下发')
    taskDialogVisible.value = false
    loadTasks()
  } catch (error) {
    ElMessage.error('下发失败')
  } finally {
    submitting.value = false
  }
}

const handleCancelTask = async (row) => {
  try {
    await ElMessageBox.confirm('确定取消该任务吗？', '提示', { type: 'warning' })
    await cancelTaskApi(row.id)
    ElMessage.success('已取消')
    loadTasks()
  } catch (e) { if (e !== 'cancel') ElMessage.error('取消失败') }
}

const viewTaskResult = async (row) => {
  taskDetailLoading.value = true
  taskDetailVisible.value = true
  try {
    taskDetail.value = await getSoftwareTaskDetail(row.id)
  } catch (error) {
    taskDetailVisible.value = false
  } finally {
    taskDetailLoading.value = false
  }
}

const handleDeletePackage = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除软件「${row.name}」吗？`, '警告', { type: 'warning' })
    await deletePackageApi(row.id)
    ElMessage.success('已删除')
    loadRepo()
  } catch (e) { if (e !== 'cancel') ElMessage.error('删除失败') }
}

const addPolicy = async (type) => {
  const name = (type === 'white' ? newWhiteName.value : newBlackName.value).trim()
  const version = (type === 'white' ? newWhiteVersion.value : newBlackVersion.value).trim()
  if (!name) return ElMessage.warning('请输入软件名')
  try {
    // 每条名单对应一个策略：规则按软件名精确匹配，版本号存入描述便于回显
    await createPolicy({
      policy_name: `${type === 'white' ? '白名单' : '黑名单'}-${name}`,
      policy_type: type === 'white' ? 'whitelist' : 'blacklist',
      description: version || null,
      enabled: true,
      priority: 0,
      target_type: 'all',
      target_ids: [],
      rules: [{
        rule_type: 'software_name',
        rule_value: name,
        match_type: 'exact',
        action: type === 'white' ? 'allow' : 'deny'
      }]
    })
    ElMessage.success('添加成功')
    if (type === 'white') { newWhiteName.value = ''; newWhiteVersion.value = '' }
    else { newBlackName.value = ''; newBlackVersion.value = '' }
    loadPolicies()
  } catch (e) {
    // 错误提示由全局拦截器弹出（如同名策略已存在）
  }
}

const removePolicy = async (type, row) => {
  try {
    await ElMessageBox.confirm(`确定移除「${row.name}」吗？`, '提示', { type: 'warning' })
    await deletePolicy(row.policy_id)
    ElMessage.success('已移除')
    loadPolicies()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('移除失败')
  }
}

onMounted(() => { loadRepo(); loadTasks(); loadPolicies(); loadStats(); loadGroups() })
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1400px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-tabs {
  :deep(.el-tabs__header) { margin-bottom: 16px; }
  :deep(.el-tabs__item) {
    font-weight: 500;
    &.is-active { color: $brand-primary; }
  }
  :deep(.el-tabs__active-bar) { background: $brand-primary; }
}

.zv-card { padding: 0; margin-bottom: 16px; }
.zv-card-pad { padding: 24px 26px; }

.zv-section-title {
  display: flex; align-items: center; gap: 8px;
  font-size: 15px; font-weight: 600; color: $text-primary;
  margin: 0 0 18px 0;
  padding-bottom: 12px;
  border-bottom: 1px solid $border-color-light;
  .el-icon { color: $brand-primary; }
}

.zv-filter-bar {
  padding: 16px 22px;
  border-bottom: 1px solid $border-color-light;
  background: $slate-50;
  display: flex;
  align-items: center;
}

.zv-pkg-cell { display: flex; align-items: center; gap: 10px; }
.zv-pkg-icon {
  width: 36px; height: 36px; border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  color: #fff; box-shadow: 0 2px 6px rgba(0,0,0,0.08);
  flex-shrink: 0;
}
.zv-pkg-name { font-size: 13px; font-weight: 600; color: $text-primary; line-height: 1.2; }
.zv-pkg-version { font-size: 12px; color: $text-tertiary; margin-top: 2px; }

.zv-mono { font-family: $font-mono; font-size: 12px; color: $text-secondary; }

.zv-detail-row {
  display: flex;
  padding: 10px 0;
  border-bottom: 1px solid $border-color-light;
  gap: 16px;
  font-size: 13px;
  color: $text-primary;

  &.zv-detail-full { flex-direction: column; }
}

.zv-label {
  font-size: 12px;
  color: $text-tertiary;
  width: 60px;
  flex-shrink: 0;
}

.zv-policy-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  @media (max-width: 1000px) { grid-template-columns: 1fr; }
}

.zv-policy-add {
  display: flex;
  gap: 8px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed $border-color;
  align-items: center;
}

.zv-upload-name { margin-left: 12px; font-size: 13px; color: $text-secondary; }

:deep(.el-input__wrapper),
:deep(.el-select__wrapper) {
  background: $slate-50;
  box-shadow: none;
  border-radius: $border-radius;
  transition: all $transition-base;
  &:hover { background: $bg-card; box-shadow: 0 0 0 1px $brand-primary-100; }
  &.is-focus { background: $bg-card; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.10); }
}

:deep(.el-table) {
  --el-table-header-bg-color: #fafbfc;
  th.el-table__cell { background: #fafbfc; color: $text-secondary; font-weight: 600; font-size: 12px; }
  tr:hover > td.el-table__cell { background: rgba(37, 99, 235, 0.03) !important; }
  td.el-table__cell { border-bottom: 1px solid $slate-100 !important; }
  .el-table__inner-wrapper::before { height: 0; }
}
</style>
