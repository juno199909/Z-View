<template>
  <div class="software-package-container">
    <el-card class="header-card">
      <div class="header-actions">
        <h2>软件包管理</h2>
        <el-button type="primary" icon="Upload" @click="showUploadDialog = true">
          上传软件包
        </el-button>
      </div>
    </el-card>

    <!-- 搜索和筛选 -->
    <el-card class="filter-card">
      <el-row :gutter="20">
        <el-col :span="6">
          <el-input
            v-model="searchKeyword"
            placeholder="搜索软件包名称"
            clearable
            @clear="loadPackages"
          >
            <template #prefix>
              <el-icon><Search /></el-icon>
            </template>
          </el-input>
        </el-col>
        <el-col :span="4">
          <el-select v-model="filterCategory" placeholder="分类" clearable @change="loadPackages">
            <el-option label="全部" value="" />
            <el-option label="办公软件" value="office" />
            <el-option label="开发工具" value="dev" />
            <el-option label="安全软件" value="security" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-select v-model="filterStatus" placeholder="状态" clearable @change="loadPackages">
            <el-option label="全部" value="" />
            <el-option label="可用" value="available" />
            <el-option label="已废弃" value="deprecated" />
          </el-select>
        </el-col>
        <el-col :span="4">
          <el-button type="primary" @click="loadPackages">搜索</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-col>
      </el-row>
    </el-card>

    <!-- 软件包列表 -->
    <el-card class="table-card">
      <el-table :data="packages" v-loading="loading" style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="display_name" label="软件名称" width="200">
          <template #default="{ row }">
            <div>
              <div style="font-weight: bold">{{ row.display_name }}</div>
              <div style="font-size: 12px; color: #909399">{{ row.package_name }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="version" label="版本" width="120" />
        <el-table-column prop="category" label="分类" width="100">
          <template #default="{ row }">
            <el-tag :type="getCategoryType(row.category)">
              {{ getCategoryLabel(row.category) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="vendor" label="厂商" width="150" />
        <el-table-column prop="file_size_readable" label="文件大小" width="120" />
        <el-table-column prop="architecture" label="架构" width="100" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'available' ? 'success' : 'info'">
              {{ row.status === 'available' ? '可用' : '已废弃' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="download_count" label="下载次数" width="100" />
        <el-table-column prop="install_count" label="安装次数" width="100" />
        <el-table-column prop="created_at" label="上传时间" width="180" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="deployPackage(row)">
              分发
            </el-button>
            <el-button size="small" type="info" @click="viewDetails(row)">
              详情
            </el-button>
            <el-button size="small" type="danger" @click="deletePackage(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-container">
        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          :page-sizes="[10, 20, 50, 100]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadPackages"
          @current-change="loadPackages"
        />
      </div>
    </el-card>

    <!-- 上传对话框 -->
    <el-dialog v-model="showUploadDialog" title="上传软件包" width="600px">
      <el-form :model="uploadForm" label-width="120px">
        <el-form-item label="软件包名称" required>
          <el-input v-model="uploadForm.package_name" placeholder="例如：Chrome" />
        </el-form-item>
        <el-form-item label="显示名称" required>
          <el-input v-model="uploadForm.display_name" placeholder="例如：Google Chrome浏览器" />
        </el-form-item>
        <el-form-item label="版本号" required>
          <el-input v-model="uploadForm.version" placeholder="例如：1.0.0" />
        </el-form-item>
        <el-form-item label="分类" required>
          <el-select v-model="uploadForm.category" style="width: 100%">
            <el-option label="办公软件" value="office" />
            <el-option label="开发工具" value="dev" />
            <el-option label="安全软件" value="security" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="厂商">
          <el-input v-model="uploadForm.vendor" placeholder="例如：Google Inc." />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="uploadForm.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="安装命令">
          <el-input v-model="uploadForm.install_command" placeholder="例如：{file_path} /S" />
          <span style="font-size: 12px; color: #909399">
            使用 {file_path} 作为文件路径占位符
          </span>
        </el-form-item>
        <el-form-item label="架构">
          <el-select v-model="uploadForm.architecture" style="width: 100%">
            <el-option label="全部" value="all" />
            <el-option label="x86" value="x86" />
            <el-option label="x64" value="x64" />
            <el-option label="ARM" value="arm" />
          </el-select>
        </el-form-item>
        <el-form-item label="需要重启">
          <el-switch v-model="uploadForm.requires_reboot" />
        </el-form-item>
        <el-form-item label="选择文件" required>
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            :on-change="handleFileChange"
            :file-list="fileList"
          >
            <el-button type="primary">选择文件</el-button>
            <template #tip>
              <div class="el-upload__tip">
                支持 .exe、.msi、.bat 等格式
              </div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="uploadPackage">
          上传
        </el-button>
      </template>
    </el-dialog>

    <!-- 分发对话框 -->
    <el-dialog v-model="showDeployDialog" title="创建分发任务" width="600px">
      <el-form :model="deployForm" label-width="120px">
        <el-form-item label="任务名称" required>
          <el-input v-model="deployForm.task_name" />
        </el-form-item>
        <el-form-item label="软件包">
          <el-input :value="selectedPackage?.display_name" disabled />
        </el-form-item>
        <el-form-item label="目标类型" required>
          <el-radio-group v-model="deployForm.target_type">
            <el-radio label="asset">指定资产</el-radio>
            <el-radio label="group">指定分组</el-radio>
            <el-radio label="all">全部资产</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="选择目标" v-if="deployForm.target_type === 'asset'" required>
          <el-select
            v-model="deployForm.target_ids"
            multiple
            placeholder="选择资产"
            style="width: 100%"
            filterable
          >
            <el-option
              v-for="asset in assets"
              :key="asset.id"
              :label="`${asset.hostname} (${asset.ip_address})`"
              :value="asset.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="选择分组" v-if="deployForm.target_type === 'group'" required>
          <el-select
            v-model="deployForm.target_ids"
            multiple
            placeholder="选择分组"
            style="width: 100%"
          >
            <el-option
              v-for="group in groups"
              :key="group.id"
              :label="group.name"
              :value="group.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-select v-model="deployForm.priority" style="width: 100%">
            <el-option label="低" value="low" />
            <el-option label="普通" value="normal" />
            <el-option label="高" value="high" />
            <el-option label="紧急" value="urgent" />
          </el-select>
        </el-form-item>
        <el-form-item label="调度类型">
          <el-radio-group v-model="deployForm.schedule_type">
            <el-radio label="immediate">立即执行</el-radio>
            <el-radio label="scheduled">定时执行</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="执行时间" v-if="deployForm.schedule_type === 'scheduled'">
          <el-date-picker
            v-model="deployForm.scheduled_time"
            type="datetime"
            placeholder="选择日期时间"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showDeployDialog = false">取消</el-button>
        <el-button type="primary" :loading="deploying" @click="createDeployTask">
          创建任务
        </el-button>
      </template>
    </el-dialog>

    <!-- 软件包详情对话框 -->
    <el-dialog v-model="showDetailDialog" title="软件包详情" width="720px">
      <el-skeleton :loading="detailLoading" animated :rows="8">
        <div v-if="detailPackage" class="detail-grid">
          <div class="detail-item">
            <span class="detail-label">软件名称</span>
            <span class="detail-value">{{ detailPackage.display_name || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">软件包名称</span>
            <span class="detail-value">{{ detailPackage.package_name || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">版本</span>
            <span class="detail-value">{{ detailPackage.version || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">分类</span>
            <span class="detail-value">{{ getCategoryLabel(detailPackage.category) }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">厂商</span>
            <span class="detail-value">{{ detailPackage.vendor || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">架构</span>
            <span class="detail-value">{{ detailPackage.architecture || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">文件名</span>
            <span class="detail-value">{{ detailPackage.file_name || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">文件大小</span>
            <span class="detail-value">{{ detailPackage.file_size_readable || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">安装命令</span>
            <span class="detail-value">{{ detailPackage.install_command || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">卸载命令</span>
            <span class="detail-value">{{ detailPackage.uninstall_command || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">需要重启</span>
            <span class="detail-value">{{ detailPackage.requires_reboot ? '是' : '否' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">下载次数</span>
            <span class="detail-value">{{ detailPackage.download_count ?? 0 }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">安装次数</span>
            <span class="detail-value">{{ detailPackage.install_count ?? 0 }}</span>
          </div>
          <div class="detail-item detail-item--full">
            <span class="detail-label">描述</span>
            <span class="detail-value">{{ detailPackage.description || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">上传时间</span>
            <span class="detail-value">{{ detailPackage.created_at || '-' }}</span>
          </div>
          <div class="detail-item">
            <span class="detail-label">更新时间</span>
            <span class="detail-value">{{ detailPackage.updated_at || '-' }}</span>
          </div>
        </div>
      </el-skeleton>
      <template #footer>
        <el-button @click="showDetailDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import axios from 'axios'
import { getSoftwarePackageDetail } from './src/api/software'

const softwareApi = axios.create({
  baseURL: import.meta.env.VITE_SOFTWARE_API_BASE || '/software-api/api/v1',
  timeout: 30000
})

const assetsApi = axios.create({
  baseURL: import.meta.env.VITE_ASSETS_API_BASE || '/api/v1',
  timeout: 30000
})

// 数据
const packages = ref([])
const assets = ref([])
const groups = ref([])
const loading = ref(false)
const uploading = ref(false)
const deploying = ref(false)
const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)

// 筛选
const searchKeyword = ref('')
const filterCategory = ref('')
const filterStatus = ref('')

// 对话框
const showUploadDialog = ref(false)
const showDeployDialog = ref(false)
const showDetailDialog = ref(false)
const selectedPackage = ref(null)
const detailPackage = ref(null)
const detailLoading = ref(false)
const fileList = ref([])
const uploadRef = ref(null)

// 表单
const uploadForm = reactive({
  package_name: '',
  display_name: '',
  version: '',
  category: 'other',
  vendor: '',
  description: '',
  install_command: '',
  architecture: 'all',
  requires_reboot: false,
  upload_by: 'admin'
})

const deployForm = reactive({
  task_name: '',
  task_type: 'install',
  target_type: 'asset',
  target_ids: [],
  priority: 'normal',
  schedule_type: 'immediate',
  scheduled_time: null
})

// 加载软件包列表
const loadPackages = async () => {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value
    }
    if (searchKeyword.value) params.keyword = searchKeyword.value
    if (filterCategory.value) params.category = filterCategory.value
    if (filterStatus.value) params.status = filterStatus.value

    const response = await softwareApi.get('/software/packages', { params })
    packages.value = response.data.data
    total.value = response.data.total
  } catch (error) {
    ElMessage.error('加载软件包列表失败：' + error.message)
  } finally {
    loading.value = false
  }
}

// 加载资产列表
const loadAssets = async () => {
  try {
    const response = await assetsApi.get('/assets', {
      params: { page: 1, page_size: 1000 }
    })
    assets.value = response.data.data
  } catch (error) {
    console.error('加载资产列表失败', error)
  }
}

// 加载分组列表
const loadGroups = async () => {
  try {
    const response = await assetsApi.get('/groups')
    groups.value = response.data
  } catch (error) {
    console.error('加载分组列表失败', error)
  }
}

// 重置筛选
const resetFilters = () => {
  searchKeyword.value = ''
  filterCategory.value = ''
  filterStatus.value = ''
  currentPage.value = 1
  loadPackages()
}

// 文件选择
const handleFileChange = (file) => {
  fileList.value = [file]
}

// 上传软件包
const uploadPackage = async () => {
  if (!uploadForm.package_name || !uploadForm.display_name || !uploadForm.version) {
    ElMessage.warning('请填写必填项')
    return
  }

  if (fileList.value.length === 0) {
    ElMessage.warning('请选择文件')
    return
  }

  uploading.value = true
  try {
    const formData = new FormData()
    formData.append('file', fileList.value[0].raw)
    formData.append('package_info', JSON.stringify(uploadForm))

    await softwareApi.post('/software/packages/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' }
    })

    ElMessage.success('软件包上传成功')
    showUploadDialog.value = false
    loadPackages()
  } catch (error) {
    ElMessage.error('上传失败：' + error.message)
  } finally {
    uploading.value = false
  }
}

// 分发软件包
const deployPackage = (pkg) => {
  selectedPackage.value = pkg
  deployForm.task_name = `分发 ${pkg.display_name} ${pkg.version}`
  showDeployDialog.value = true
  loadAssets()
  loadGroups()
}

// 创建分发任务
const createDeployTask = async () => {
  if (!deployForm.task_name) {
    ElMessage.warning('请填写任务名称')
    return
  }

  if (deployForm.target_type !== 'all' && deployForm.target_ids.length === 0) {
    ElMessage.warning('请选择目标')
    return
  }

  deploying.value = true
  try {
    await softwareApi.post('/software/tasks', {
      task_name: deployForm.task_name,
      task_type: deployForm.task_type,
      package_id: selectedPackage.value.id,
      target_type: deployForm.target_type,
      target_ids: deployForm.target_ids,
      priority: deployForm.priority,
      schedule_type: deployForm.schedule_type,
      scheduled_time: deployForm.scheduled_time
    })

    ElMessage.success('分发任务创建成功')
    showDeployDialog.value = false
  } catch (error) {
    ElMessage.error('创建任务失败：' + error.message)
  } finally {
    deploying.value = false
  }
}

// 删除软件包
const deletePackage = async (pkg) => {
  try {
    await ElMessageBox.confirm(`确定删除软件包 ${pkg.display_name}？`, '警告', {
      type: 'warning'
    })

    await softwareApi.delete(`/software/packages/${pkg.id}`)
    ElMessage.success('删除成功')
    loadPackages()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败：' + error.message)
    }
  }
}

// 查看详情
const viewDetails = async (pkg) => {
  // 中文注释：优先拉取服务端详情，避免列表字段不全导致页面展示不完整。
  detailLoading.value = true
  showDetailDialog.value = true
  try {
    const response = await getSoftwarePackageDetail(pkg.id)
    detailPackage.value = response.data.data
  } catch (error) {
    detailPackage.value = {
      ...pkg,
      description: pkg.description || '详情加载失败，请稍后重试'
    }
    ElMessage.warning('获取软件包详情失败，已显示列表中的基础信息')
  } finally {
    detailLoading.value = false
  }
}

// 辅助函数
const getCategoryType = (category) => {
  const types = { office: 'primary', dev: 'success', security: 'danger', other: 'info' }
  return types[category] || 'info'
}

const getCategoryLabel = (category) => {
  const labels = { office: '办公', dev: '开发', security: '安全', other: '其他' }
  return labels[category] || category
}

onMounted(() => {
  loadPackages()
})
</script>

<style scoped>
.software-package-container {
  padding: 20px;
}

.header-card {
  margin-bottom: 20px;
}

.header-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.header-actions h2 {
  margin: 0;
}

.filter-card {
  margin-bottom: 20px;
}

.table-card {
  margin-bottom: 20px;
}

.pagination-container {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
}

.detail-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  background: #f8fafc;
  border-radius: 8px;
}

.detail-item--full {
  grid-column: 1 / -1;
}

.detail-label {
  font-size: 12px;
  color: #909399;
}

.detail-value {
  font-size: 14px;
  color: #303133;
  word-break: break-all;
}
</style>
