<template>
  <div class="package-repository">
    <el-card class="header-card">
      <div class="header-actions">
        <div>
          <h2>软件包仓库</h2>
          <p style="color: #909399; font-size: 14px; margin-top: 5px;">
            管理软件安装包，支持批量分发到终端
          </p>
        </div>
        <el-button type="primary" icon="Upload" @click="showUploadDialog = true">
          上传软件包
        </el-button>
      </div>
    </el-card>

    <!-- 搜索筛选 -->
    <el-card class="filter-card">
      <el-row :gutter="20">
        <el-col :span="6">
          <el-input
            v-model="searchKeyword"
            placeholder="搜索软件包名称"
            clearable
            @keyup.enter="loadPackages"
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
            <el-tag :type="getCategoryType(row.category)" size="small">
              {{ getCategoryLabel(row.category) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="vendor" label="厂商" width="150" show-overflow-tooltip />
        <el-table-column prop="file_size_readable" label="文件大小" width="120" />
        <el-table-column prop="architecture" label="架构" width="100" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'available' ? 'success' : 'info'" size="small">
              {{ row.status === 'available' ? '可用' : '已废弃' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="download_count" label="下载" width="80" />
        <el-table-column prop="install_count" label="安装" width="80" />
        <el-table-column prop="created_at" label="上传时间" width="160" />
        <el-table-column label="操作" width="220" fixed="right">
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
    <el-dialog v-model="showUploadDialog" title="上传软件包" width="600px" @close="resetUploadForm">
      <el-form :model="uploadForm" :rules="uploadRules" ref="uploadFormRef" label-width="120px">
        <el-form-item label="软件包名称" prop="package_name">
          <el-input v-model="uploadForm.package_name" placeholder="例如：Chrome" />
        </el-form-item>
        <el-form-item label="显示名称" prop="display_name">
          <el-input v-model="uploadForm.display_name" placeholder="例如：Google Chrome浏览器" />
        </el-form-item>
        <el-form-item label="版本号" prop="version">
          <el-input v-model="uploadForm.version" placeholder="例如：1.0.0" />
        </el-form-item>
        <el-form-item label="分类" prop="category">
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
        <el-form-item label="选择文件" prop="file">
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            :on-change="handleFileChange"
            :file-list="fileList"
            accept=".exe,.msi,.bat,.zip"
          >
            <el-button type="primary">选择文件</el-button>
            <template #tip>
              <div class="el-upload__tip">
                支持 .exe、.msi、.bat、.zip 等格式，最大 2GB
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
    <el-dialog v-model="showDeployDialog" title="创建分发任务" width="600px" @close="resetDeployForm">
      <el-form :model="deployForm" :rules="deployRules" ref="deployFormRef" label-width="120px">
        <el-form-item label="任务名称" prop="task_name">
          <el-input v-model="deployForm.task_name" />
        </el-form-item>
        <el-form-item label="软件包">
          <el-input :value="selectedPackage?.display_name + ' ' + selectedPackage?.version" disabled />
        </el-form-item>
        <el-form-item label="目标类型" prop="target_type">
          <el-radio-group v-model="deployForm.target_type">
            <el-radio value="asset">指定终端</el-radio>
            <el-radio value="group">指定分组</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="选择目标" v-if="deployForm.target_type === 'asset'" prop="target_ids">
          <el-select
            v-model="deployForm.target_ids"
            multiple
            filterable
            placeholder="选择终端"
            style="width: 100%"
          >
            <el-option
              v-for="asset in assets"
              :key="asset.id"
              :label="`${asset.hostname} (${asset.ip_address})`"
              :value="asset.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="选择分组" v-if="deployForm.target_type === 'group'" prop="target_ids">
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
        <el-form-item label="调度类型">
          <el-radio-group v-model="deployForm.schedule_type">
            <el-radio value="immediate">立即执行</el-radio>
            <el-radio value="scheduled">定时执行</el-radio>
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
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { getAssetList } from '@/api/asset'
import { getGroups } from '@/api/group'
import {
  getSoftwarePackages,
  uploadSoftwarePackage,
  createSoftwareTask,
  deleteSoftwarePackage
} from '@/api/software'

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
const selectedPackage = ref(null)
const fileList = ref([])
const uploadRef = ref(null)
const uploadFormRef = ref(null)
const deployFormRef = ref(null)

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
  schedule_type: 'immediate',
  scheduled_time: null
})

// 验证规则
const uploadRules = {
  package_name: [{ required: true, message: '请输入软件包名称', trigger: 'blur' }],
  display_name: [{ required: true, message: '请输入显示名称', trigger: 'blur' }],
  version: [{ required: true, message: '请输入版本号', trigger: 'blur' }],
  category: [{ required: true, message: '请选择分类', trigger: 'change' }]
}

const deployRules = {
  task_name: [{ required: true, message: '请输入任务名称', trigger: 'blur' }],
  target_type: [{ required: true, message: '请选择目标类型', trigger: 'change' }],
  target_ids: [{ required: true, message: '请选择目标', trigger: 'change' }]
}

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

    const response = await getSoftwarePackages(params)
    packages.value = response.data || []
    total.value = response.total || 0
  } catch (error) {
    ElMessage.error('加载软件包列表失败：' + (error.response?.data?.detail || error.message))
  } finally {
    loading.value = false
  }
}

// 加载资产列表
const loadAssets = async () => {
  try {
    const response = await getAssetList({ page: 1, page_size: 100 })
    assets.value = response.data || []
  } catch (error) {
    const errMsg = error.response?.data?.detail || error.response?.data?.message || error.message || '未知错误'
    ElMessage.error('加载终端列表失败：' + errMsg)
  }
}

// 加载分组列表
const loadGroups = async () => {
  try {
    const response = await getGroups()
    groups.value = response.data || []
  } catch (error) {
    const errMsg = error.response?.data?.detail || error.response?.data?.message || error.message || '未知错误'
    ElMessage.error('加载分组列表失败：' + errMsg)
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

// 重置上传表单
const resetUploadForm = () => {
  uploadFormRef.value?.resetFields()
  fileList.value = []
}

// 重置分发表单
const resetDeployForm = () => {
  deployFormRef.value?.resetFields()
}

// 上传软件包
const uploadPackage = async () => {
  try {
    await uploadFormRef.value.validate()
  } catch {
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

    await uploadSoftwarePackage(formData)

    ElMessage.success('软件包上传成功')
    showUploadDialog.value = false
    loadPackages()
  } catch (error) {
    ElMessage.error('上传失败：' + (error.response?.data?.detail || error.message))
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
  try {
    await deployFormRef.value.validate()
  } catch {
    return
  }

  deploying.value = true
  try {
    await createSoftwareTask({
      task_name: deployForm.task_name,
      task_type: deployForm.task_type,
      package_id: selectedPackage.value.id,
      target_type: deployForm.target_type,
      target_ids: deployForm.target_ids,
      schedule_type: deployForm.schedule_type,
      scheduled_time: deployForm.scheduled_time
    })

    ElMessage.success('分发任务创建成功，请在任务管理页面查看进度')
    showDeployDialog.value = false
  } catch (error) {
    ElMessage.error('创建任务失败：' + (error.response?.data?.detail || error.message))
  } finally {
    deploying.value = false
  }
}

// 删除软件包
const deletePackage = async (pkg) => {
  try {
    await ElMessageBox.confirm(
      `确定删除软件包 ${pkg.display_name} ${pkg.version}？`,
      '警告',
      { type: 'warning' }
    )

    await deleteSoftwarePackage(pkg.id)
    ElMessage.success('删除成功')
    loadPackages()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败：' + (error.response?.data?.detail || error.message))
    }
  }
}

// 查看详情
const viewDetails = (pkg) => {
  ElMessageBox.alert(
    `<div style="line-height: 1.8">
      <p><strong>软件名称：</strong>${pkg.display_name}</p>
      <p><strong>版本：</strong>${pkg.version}</p>
      <p><strong>厂商：</strong>${pkg.vendor || '-'}</p>
      <p><strong>文件名：</strong>${pkg.file_name}</p>
      <p><strong>文件大小：</strong>${pkg.file_size_readable}</p>
      <p><strong>架构：</strong>${pkg.architecture}</p>
      <p><strong>需要重启：</strong>${pkg.requires_reboot ? '是' : '否'}</p>
      <p><strong>下载次数：</strong>${pkg.download_count}</p>
      <p><strong>安装次数：</strong>${pkg.install_count}</p>
      <p><strong>描述：</strong>${pkg.description || '-'}</p>
    </div>`,
    '软件包详情',
    {
      dangerouslyUseHTMLString: true,
      confirmButtonText: '关闭'
    }
  )
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
.package-repository {
  padding: 0;
}

.header-card {
  margin-bottom: 20px;
}

.header-actions {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.header-actions h2 {
  margin: 0;
  font-size: 18px;
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
</style>
