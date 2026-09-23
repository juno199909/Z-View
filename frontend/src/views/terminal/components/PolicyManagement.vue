<template>
  <div class="policy-management">
    <el-card>
      <template #header>
        <div class="header">
          <div>
            <h2>策略管理</h2>
            <p style="color: #909399; font-size: 14px; margin-top: 5px;">
              管理软件黑白名单和强制安装策略
            </p>
          </div>
          <div>
            <el-button type="primary" @click="openCreateDialog">
              创建策略
            </el-button>
          </div>
        </div>
      </template>

      <!-- 筛选 -->
      <div class="filter-bar">
        <el-select v-model="filters.policy_type" placeholder="策略类型" clearable style="width: 200px; margin-right: 10px;">
          <el-option label="黑名单" value="blacklist" />
          <el-option label="白名单" value="whitelist" />
          <el-option label="强制安装" value="force_install" />
        </el-select>
        <el-select v-model="filters.enabled" placeholder="状态" clearable style="width: 150px; margin-right: 10px;">
          <el-option label="已启用" :value="true" />
          <el-option label="已禁用" :value="false" />
        </el-select>
        <el-button type="primary" @click="loadPolicies">查询</el-button>
      </div>

      <!-- 策略列表 -->
      <el-table :data="policies" style="margin-top: 20px;" v-loading="loading">
        <el-table-column prop="policy_name" label="策略名称" min-width="180" />
        <el-table-column prop="policy_type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag :type="getPolicyTypeColor(row.policy_type)">
              {{ getPolicyTypeLabel(row.policy_type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="200" show-overflow-tooltip />
        <el-table-column prop="target_type" label="应用范围" width="100">
          <template #default="{ row }">
            {{ getTargetTypeLabel(row.target_type) }}
          </template>
        </el-table-column>
        <el-table-column label="目标对象" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">
            {{ getTargetSummary(row) }}
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="90" sortable />
        <el-table-column prop="enabled" label="状态" width="90">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="toggleEnabled(row)" />
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="160" />
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button size="small" @click="openEditDialog(row)">
              编辑
            </el-button>
            <el-button size="small" type="warning" @click="executePolicyHandler(row)">
              立即执行
            </el-button>
            <el-button size="small" type="primary" @click="viewDetails(row)">
              详情
            </el-button>
            <el-button size="small" type="danger" @click="deletePolicyHandler(row)">
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :total="pagination.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadPolicies"
          @current-change="loadPolicies"
        />
      </div>
    </el-card>

    <!-- 创建/编辑策略对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      :title="isEditMode ? '编辑策略' : '创建策略'"
      width="700px"
      destroy-on-close
    >
      <el-form :model="policyForm" label-width="100px">
        <el-form-item label="策略名称" required>
          <el-input v-model="policyForm.policy_name" placeholder="请输入策略名称" />
        </el-form-item>
        <el-form-item label="策略类型" required>
          <el-select v-model="policyForm.policy_type" style="width: 100%;" @change="onPolicyTypeChange">
            <el-option label="黑名单 - 禁止安装指定软件" value="blacklist" />
            <el-option label="白名单 - 只允许安装指定软件" value="whitelist" />
            <el-option label="强制安装 - 自动安装指定软件" value="force_install" />
          </el-select>
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="policyForm.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="应用范围">
          <el-radio-group v-model="policyForm.target_type">
            <el-radio value="all">所有终端</el-radio>
            <el-radio value="group">指定分组</el-radio>
            <el-radio value="asset">指定终端</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="目标分组" v-if="policyForm.target_type === 'group'">
          <el-select
            v-model="policyForm.target_ids"
            multiple
            filterable
            placeholder="请选择目标分组"
            style="width: 100%;"
          >
            <el-option
              v-for="item in groupOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标终端" v-if="policyForm.target_type === 'asset'">
          <el-select
            v-model="policyForm.target_ids"
            multiple
            filterable
            placeholder="请选择目标终端"
            style="width: 100%;"
          >
            <el-option
              v-for="item in targetOptions"
              :key="item.id"
              :label="item.name"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number v-model="policyForm.priority" :min="0" :max="100" />
          <span style="margin-left: 10px; color: #909399; font-size: 12px;">数字越大优先级越高</span>
        </el-form-item>
        <el-form-item label="是否启用">
          <el-switch v-model="policyForm.enabled" />
        </el-form-item>

        <!-- 规则列表 -->
        <el-divider>策略规则</el-divider>
        <div v-for="(rule, index) in policyForm.rules" :key="index" class="rule-item">
          <el-row :gutter="10">
            <el-col :span="6">
              <el-select
                v-model="rule.rule_type"
                placeholder="规则类型"
                style="width: 100%;"
                :disabled="policyForm.policy_type === 'force_install'"
              >
                <el-option v-if="policyForm.policy_type === 'force_install'" label="软件包仓库" value="package_id" />
                <el-option label="软件名称" value="software_name" />
                <el-option label="厂商" value="vendor" />
              </el-select>
            </el-col>
            <el-col :span="10">
              <el-select
                v-if="policyForm.policy_type === 'force_install'"
                v-model="rule.rule_value"
                filterable
                clearable
                placeholder="请选择软件包仓库中的软件"
                style="width: 100%;"
                :loading="packageLoading"
              >
                <el-option
                  v-for="pkg in packageOptions"
                  :key="pkg.id"
                  :label="getPackageOptionLabel(pkg)"
                  :value="String(pkg.id)"
                />
              </el-select>
              <el-input v-else v-model="rule.rule_value" placeholder="例如：Chrome" />
            </el-col>
            <el-col :span="6">
              <el-select
                v-model="rule.match_type"
                placeholder="匹配方式"
                style="width: 100%;"
                :disabled="policyForm.policy_type === 'force_install'"
              >
                <el-option label="精确匹配" value="exact" />
                <el-option label="包含" value="contains" />
              </el-select>
            </el-col>
            <el-col :span="2">
              <el-button type="danger" @click="removeRule(index)" text>删除</el-button>
            </el-col>
          </el-row>
        </div>
        <el-button type="primary" @click="addRule" style="margin-top: 10px;">
          添加规则
        </el-button>
      </el-form>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="submitPolicyHandler" :loading="creating">
          {{ isEditMode ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 策略详情对话框 -->
    <el-dialog v-model="showDetailsDialog" title="策略详情" width="800px" destroy-on-close>
      <el-descriptions :column="2" border v-if="selectedPolicy">
        <el-descriptions-item label="策略名称">{{ selectedPolicy.policy_name }}</el-descriptions-item>
        <el-descriptions-item label="策略类型">
          <el-tag :type="getPolicyTypeColor(selectedPolicy.policy_type)">
            {{ getPolicyTypeLabel(selectedPolicy.policy_type) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="描述" :span="2">{{ selectedPolicy.description || '-' }}</el-descriptions-item>
        <el-descriptions-item label="应用范围">{{ getTargetTypeLabel(selectedPolicy.target_type) }}</el-descriptions-item>
        <el-descriptions-item label="目标对象">{{ getTargetSummary(selectedPolicy) }}</el-descriptions-item>
        <el-descriptions-item label="优先级">{{ selectedPolicy.priority }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="selectedPolicy.enabled ? 'success' : 'info'">
            {{ selectedPolicy.enabled ? '已启用' : '已禁用' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ selectedPolicy.created_at }}</el-descriptions-item>
      </el-descriptions>

      <el-divider>策略规则</el-divider>
      <el-table :data="selectedPolicy?.rules || []" style="margin-top: 10px;">
        <el-table-column prop="rule_type" label="规则类型" width="120">
          <template #default="{ row }">
            {{ getRuleTypeLabel(row.rule_type) }}
          </template>
        </el-table-column>
        <el-table-column label="规则值" min-width="260">
          <template #default="{ row }">
            {{ getRuleDisplayValue(row) }}
          </template>
        </el-table-column>
        <el-table-column prop="match_type" label="匹配方式" width="120">
          <template #default="{ row }">
            {{ getMatchTypeLabel(row.match_type) }}
          </template>
        </el-table-column>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getPolicies, getPolicyDetail, createPolicy, updatePolicy, executePolicy, deletePolicy } from '@/api/policy'
import { getAssetList } from '@/api/asset'
import { getGroups } from '@/api/group'
import { getSoftwarePackages } from '@/api/software'

const loading = ref(false)
const creating = ref(false)
const policies = ref([])
const showCreateDialog = ref(false)
const showDetailsDialog = ref(false)
const selectedPolicy = ref(null)
const targetOptions = ref([])
const groupOptions = ref([])
const packageOptions = ref([])
const packageLoading = ref(false)
const isEditMode = ref(false)
const editingPolicyId = ref(null)

const ASSET_FETCH_PAGE_SIZE = 100
const PACKAGE_FETCH_PAGE_SIZE = 100

const filters = reactive({
  policy_type: '',
  enabled: null
})

const pagination = reactive({
  page: 1,
  page_size: 20,
  total: 0
})

const createDefaultRule = (policyType = 'blacklist') => {
  if (policyType === 'force_install') {
    return {
      rule_type: 'package_id',
      rule_value: '',
      match_type: 'exact',
      action: 'force'
    }
  }

  return {
    rule_type: 'software_name',
    rule_value: '',
    match_type: 'contains',
    action: policyType === 'whitelist' ? 'allow' : 'deny'
  }
}

const policyForm = reactive({
  policy_name: '',
  policy_type: 'blacklist',
  description: '',
  enabled: true,
  priority: 0,
  target_type: 'all',
  target_ids: [],
  rules: [createDefaultRule()]
})

const normalizeTargetIds = (targetIds) => (
  Array.isArray(targetIds)
    ? targetIds
        .map(id => Number(id))
        .filter(id => Number.isFinite(id))
    : []
)

const loadPolicies = async () => {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.page_size
    }

    // 只添加有值的参数
    if (filters.policy_type) {
      params.policy_type = filters.policy_type
    }
    if (filters.enabled !== null) {
      params.enabled = filters.enabled
    }

    const res = await getPolicies(params)
    // 转换enabled字段为布尔值
    policies.value = res.data.map(policy => ({
      ...policy,
      enabled: Boolean(policy.enabled),
      target_ids: normalizeTargetIds(policy.target_ids)
    }))
    pagination.total = res.total
  } catch (error) {
    ElMessage.error('加载策略列表失败')
  } finally {
    loading.value = false
  }
}

const loadTargetOptions = async () => {
  try {
    const assets = []
    let page = 1
    let total = 0

    do {
      const res = await getAssetList({ page, page_size: ASSET_FETCH_PAGE_SIZE })
      assets.push(...(res.data || []))
      total = Number(res.total || 0)
      page += 1
    } while (assets.length < total)

    targetOptions.value = assets.map(asset => ({
      id: asset.id,
      name: `${asset.hostname} (${asset.ip_address})`
    }))
  } catch (error) {
    console.error('加载目标选项失败:', error)
  }
}

const loadGroupOptions = async () => {
  try {
    const res = await getGroups()
    groupOptions.value = (res.data || []).map(group => ({
      id: Number(group.id),
      name: group.name
    }))
  } catch (error) {
    console.error('加载分组选项失败:', error)
    ElMessage.error('加载分组选项失败')
  }
}

const loadPackageOptions = async () => {
  packageLoading.value = true
  try {
    const packages = []
    let page = 1
    let total = 0

    do {
      const res = await getSoftwarePackages({
        page,
        page_size: PACKAGE_FETCH_PAGE_SIZE,
        status: 'available'
      })
      packages.push(...(res.data || []))
      total = Number(res.total || 0)
      page += 1
    } while (packages.length < total)

    packageOptions.value = packages
  } catch (error) {
    console.error('加载软件包仓库失败:', error)
    ElMessage.error('加载软件包仓库失败')
  } finally {
    packageLoading.value = false
  }
}

const resetPolicyForm = () => {
  isEditMode.value = false
  editingPolicyId.value = null
  policyForm.policy_name = ''
  policyForm.policy_type = 'blacklist'
  policyForm.description = ''
  policyForm.enabled = true
  policyForm.priority = 0
  policyForm.target_type = 'all'
  policyForm.target_ids = []
  policyForm.rules = [createDefaultRule('blacklist')]
}

const openCreateDialog = async () => {
  resetPolicyForm()
  showCreateDialog.value = true
  if (!packageOptions.value.length) {
    await loadPackageOptions()
  }
}

const openEditDialog = async (policy) => {
  try {
    if (!packageOptions.value.length) {
      await loadPackageOptions()
    }

    const detail = await getPolicyDetail(policy.id)
    isEditMode.value = true
    editingPolicyId.value = policy.id
    policyForm.policy_name = detail.policy_name || ''
    policyForm.policy_type = detail.policy_type || 'blacklist'
    policyForm.description = detail.description || ''
    policyForm.enabled = Boolean(detail.enabled)
    policyForm.priority = Number(detail.priority || 0)
    policyForm.target_type = detail.target_type || 'all'
    policyForm.target_ids = normalizeTargetIds(detail.target_ids)
    policyForm.rules = Array.isArray(detail.rules) && detail.rules.length
      ? detail.rules.map(rule => ({
          rule_type: rule.rule_type || (detail.policy_type === 'force_install' ? 'package_id' : 'software_name'),
          rule_value: String(rule.rule_value ?? ''),
          match_type: rule.match_type || (detail.policy_type === 'force_install' ? 'exact' : 'contains'),
          action: rule.action || (detail.policy_type === 'whitelist' ? 'allow' : detail.policy_type === 'force_install' ? 'force' : 'deny')
        }))
      : [createDefaultRule(detail.policy_type)]
    showCreateDialog.value = true
  } catch (error) {
    ElMessage.error('加载策略详情失败')
  }
}

const addRule = () => {
  policyForm.rules.push(createDefaultRule(policyForm.policy_type))
}

const removeRule = (index) => {
  policyForm.rules.splice(index, 1)
}

const onPolicyTypeChange = () => {
  policyForm.rules = [createDefaultRule(policyForm.policy_type)]
}

const buildPolicyPayload = () => ({
  ...policyForm,
  target_ids: [...policyForm.target_ids],
  rules: policyForm.rules.map(rule => {
    if (policyForm.policy_type === 'force_install') {
      return {
        rule_type: 'package_id',
        rule_value: String(rule.rule_value || ''),
        match_type: 'exact',
        action: 'force'
      }
    }

    return {
      ...rule,
      action: policyForm.policy_type === 'whitelist' ? 'allow' : 'deny'
    }
  })
})

const submitPolicyHandler = async () => {
  if (!policyForm.policy_name) {
    ElMessage.warning('请输入策略名称')
    return
  }

  if (policyForm.rules.length === 0) {
    ElMessage.warning('请至少添加一条规则')
    return
  }

  creating.value = true
  try {
    const payload = buildPolicyPayload()

    if (payload.policy_type === 'force_install' && payload.rules.some(rule => !rule.rule_value)) {
      ElMessage.warning('请选择要强制安装的软件包')
      creating.value = false
      return
    }

    const result = isEditMode.value
      ? await updatePolicy(editingPolicyId.value, payload)
      : await createPolicy(payload)
    const queuedTasks = Number(result.queued_tasks || 0)
    ElMessage.success(
      queuedTasks > 0
        ? `${isEditMode.value ? '策略更新成功' : '策略创建成功'}，已下发 ${queuedTasks} 个安装任务`
        : (result.message || (isEditMode.value ? '策略更新成功' : '策略创建成功'))
    )
    showCreateDialog.value = false
    resetPolicyForm()
    loadPolicies()
  } catch (error) {
    ElMessage.error(isEditMode.value ? '更新策略失败' : '创建策略失败')
  } finally {
    creating.value = false
  }
}

const viewDetails = async (policy) => {
  try {
    const detail = await getPolicyDetail(policy.id)
    selectedPolicy.value = {
      ...detail,
      enabled: Boolean(detail.enabled),
      target_ids: normalizeTargetIds(detail.target_ids)
    }
    showDetailsDialog.value = true
  } catch (error) {
    ElMessage.error('加载策略详情失败')
  }
}

const toggleEnabled = async (policy) => {
  try {
    await updatePolicy(policy.id, { enabled: policy.enabled })
    ElMessage.success(policy.enabled ? '策略已启用' : '策略已禁用')
  } catch (error) {
    policy.enabled = !policy.enabled
    ElMessage.error('更新失败')
  }
}

const executePolicyHandler = async (policy) => {
  try {
    await ElMessageBox.confirm(`确定立即执行策略"${policy.policy_name}"吗？`, '立即执行', {
      type: 'warning'
    })

    const result = await executePolicy(policy.id)
    ElMessage.success(result.message || '策略执行已触发')
    loadPolicies()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(error?.response?.data?.detail || '立即执行失败')
    }
  }
}

const deletePolicyHandler = async (policy) => {
  try {
    await ElMessageBox.confirm(`确定要删除策略"${policy.policy_name}"吗？`, '确认删除', {
      type: 'warning'
    })

    await deletePolicy(policy.id)
    ElMessage.success('策略删除成功')
    loadPolicies()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败')
    }
  }
}

const getPolicyTypeLabel = (type) => {
  const labels = { blacklist: '黑名单', whitelist: '白名单', force_install: '强制安装' }
  return labels[type] || type
}

const getPolicyTypeColor = (type) => {
  const colors = { blacklist: 'danger', whitelist: 'success', force_install: 'warning' }
  return colors[type] || 'info'
}

const getTargetTypeLabel = (type) => {
  const labels = { all: '所有终端', group: '指定分组', asset: '指定终端' }
  return labels[type] || type
}

const getTargetNames = (policy) => {
  if (!policy || policy.target_type === 'all') {
    return ['全部在线受管终端']
  }

  const targetIds = normalizeTargetIds(policy.target_ids)
  if (!targetIds.length) {
    return ['未配置目标对象']
  }

  const source = policy.target_type === 'group' ? groupOptions.value : targetOptions.value
  const labelMap = new Map(source.map(item => [Number(item.id), item.name]))

  return targetIds.map(id => labelMap.get(id) || `${policy.target_type === 'group' ? '分组' : '终端'} #${id}`)
}

const getTargetSummary = (policy) => {
  const names = getTargetNames(policy)
  if (names.length <= 2) {
    return names.join('、')
  }
  return `${names.slice(0, 2).join('、')} 等 ${names.length} 项`
}

const getRuleTypeLabel = (type) => {
  const labels = { software_name: '软件名称', package_id: '软件包ID', vendor: '厂商', category: '分类' }
  return labels[type] || type
}

const getPackageOptionLabel = (pkg) => {
  const version = pkg.version ? ` ${pkg.version}` : ''
  const vendor = pkg.vendor ? ` | ${pkg.vendor}` : ''
  return `${pkg.display_name || pkg.package_name}${version}${vendor}`
}

const getRuleDisplayValue = (rule) => {
  if (rule.rule_type === 'package_id' && rule.package_display_name) {
    const version = rule.package_version ? ` ${rule.package_version}` : ''
    return `${rule.package_display_name}${version} (ID: ${rule.rule_value})`
  }

  return rule.rule_value || '-'
}

const getMatchTypeLabel = (type) => {
  const labels = { exact: '精确匹配', contains: '包含', regex: '正则表达式' }
  return labels[type] || type
}

onMounted(() => {
  loadPolicies()
  loadTargetOptions()
  loadGroupOptions()
  loadPackageOptions()
})
</script>

<style scoped>
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.filter-bar {
  display: flex;
  align-items: center;
}

.pagination {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

.rule-item {
  margin-bottom: 10px;
  padding: 10px;
  background: #f5f7fa;
  border-radius: 4px;
}
</style>
