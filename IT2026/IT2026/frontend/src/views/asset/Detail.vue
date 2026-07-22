<template>
  <div class="app-container">
    <el-card>
      <div class="detail-header">
        <h2>资产详情</h2>
        <div class="header-actions">
          <el-button type="warning" @click="editMode = true" v-if="!editMode && asset.id">
            <el-icon><Edit /></el-icon>
            编辑
          </el-button>
          <el-button type="primary" @click="saveAsset" v-if="editMode" :loading="saving">
            <el-icon><Check /></el-icon>
            保存
          </el-button>
          <el-button @click="cancelEdit" v-if="editMode">
            <el-icon><Close /></el-icon>
            取消
          </el-button>
          <el-button type="primary" @click="goToTerminal" v-if="!editMode && asset.id">
            <el-icon><Monitor /></el-icon>
            查看实时监控
          </el-button>
          <el-button @click="$router.back()">返回</el-button>
        </div>
      </div>

      <!-- 关联终端状态 -->
      <el-alert
        v-if="heartbeat"
        :title="`终端状态：${asset.status === 'online' ? '● 在线' : '○ 离线'}`"
        :type="asset.status === 'online' ? 'success' : 'info'"
        style="margin-bottom: 20px"
      >
        <template v-if="asset.status === 'online'">
          <div style="display: flex; gap: 30px; margin-top: 10px;">
            <span>CPU: <strong>{{ heartbeat.cpu_usage || 0 }}%</strong></span>
            <span>内存: <strong>{{ heartbeat.memory_usage || 0 }}%</strong></span>
            <span>磁盘: <strong>{{ heartbeat.disk_usage || 0 }}%</strong></span>
            <span style="color: #999;">最后心跳: {{ heartbeat.heartbeat_time || '-' }}</span>
          </div>
        </template>
      </el-alert>

      <!-- 查看模式 -->
      <div v-if="!editMode">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="主机名">{{ asset.hostname }}</el-descriptions-item>
          <el-descriptions-item label="IP地址">{{ asset.ip_address }}</el-descriptions-item>
          <el-descriptions-item label="MAC地址">{{ asset.mac_address }}</el-descriptions-item>
          <el-descriptions-item label="资产类型">
            <el-tag>{{ getAssetTypeText(asset.asset_type) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="getStatusType(asset.status)">{{ getStatusText(asset.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="操作系统" :span="2">
            {{ asset.os_type }} {{ asset.os_version }}
          </el-descriptions-item>
          <el-descriptions-item label="CPU核心">{{ asset.cpu_cores || '-' }}</el-descriptions-item>
          <el-descriptions-item label="内存">{{ asset.memory_mb ? (asset.memory_mb / 1024).toFixed(1) + ' GB' : '-' }}</el-descriptions-item>
          <el-descriptions-item label="磁盘容量">{{ asset.disk_gb || '-' }} GB</el-descriptions-item>
          <el-descriptions-item label="序列号">{{ asset.serial_number || '-' }}</el-descriptions-item>
          <el-descriptions-item label="制造商">{{ asset.manufacturer || '-' }}</el-descriptions-item>
          <el-descriptions-item label="型号">{{ asset.model || '-' }}</el-descriptions-item>
        </el-descriptions>

        <!-- 采购信息 -->
        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><ShoppingCart /></el-icon> 采购信息
        </h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="采购日期">{{ asset.purchase_date || '-' }}</el-descriptions-item>
          <el-descriptions-item label="采购价格">
            {{ asset.purchase_price ? '¥' + Number(asset.purchase_price).toLocaleString() : '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="供应商">{{ asset.supplier || '-' }}</el-descriptions-item>
          <el-descriptions-item label="合同编号">{{ asset.contract_no || '-' }}</el-descriptions-item>
        </el-descriptions>

        <!-- 保修信息 -->
        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><Tools /></el-icon> 保修信息
        </h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="保修开始">{{ asset.warranty_start || '-' }}</el-descriptions-item>
          <el-descriptions-item label="保修结束">
            <span v-if="asset.warranty_end">
              {{ asset.warranty_end }}
              <el-tag v-if="getWarrantyStatus(asset.warranty_end)" :type="getWarrantyStatus(asset.warranty_end).type" size="small" style="margin-left: 10px;">
                {{ getWarrantyStatus(asset.warranty_end).text }}
              </el-tag>
            </span>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item label="保修服务商" :span="2">{{ asset.warranty_provider || '-' }}</el-descriptions-item>
        </el-descriptions>

        <!-- 使用信息 -->
        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><User /></el-icon> 使用信息
        </h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="部署日期">{{ asset.deployment_date || '-' }}</el-descriptions-item>
          <el-descriptions-item label="资产状态">
            <el-tag :type="getAssetStatusType(asset.asset_status)">
              {{ getAssetStatusText(asset.asset_status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="使用人">{{ asset.user_name || '-' }}</el-descriptions-item>
          <el-descriptions-item label="使用部门">{{ asset.department || '-' }}</el-descriptions-item>
          <el-descriptions-item label="位置" :span="2">{{ asset.location || '-' }}</el-descriptions-item>
          <el-descriptions-item label="负责人" :span="2">{{ asset.owner || '-' }}</el-descriptions-item>
        </el-descriptions>

        <!-- 其他信息 -->
        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><DocumentCopy /></el-icon> 其他信息
        </h3>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="报废日期" v-if="asset.asset_status === 'retired'">
            {{ asset.retire_date || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="报废原因" :span="asset.asset_status === 'retired' ? 1 : 2" v-if="asset.asset_status === 'retired'">
            {{ asset.retire_reason || '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="备注" :span="2">{{ asset.notes || '-' }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ asset.created_at || '-' }}</el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ asset.updated_at || '-' }}</el-descriptions-item>
        </el-descriptions>

        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><Monitor /></el-icon> 运行状态
        </h3>
        <el-card v-loading="historyLoading" shadow="never">
          <el-descriptions :column="2" border>
            <el-descriptions-item label="当前在线状态">
              <el-tag :type="getStatusType(statusOverview?.current_status || asset.status)">
                {{ getStatusText(statusOverview?.current_status || asset.status) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="Agent 安装状态">
              <el-tag :type="asset.agent_install_status === 'installed' ? 'success' : 'info'">
                {{ asset.agent_install_status === 'installed' ? '已安装' : '未安装' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="当前在线时长">
              {{ uptimeSummary?.current_uptime_text || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="近 7 天在线率">
              {{ uptimeSummary?.availability_percent ?? '-' }}%
            </el-descriptions-item>
            <el-descriptions-item label="最后心跳时间">
              {{ statusOverview?.heartbeat?.heartbeat_time || heartbeat?.heartbeat_time || '-' }}
            </el-descriptions-item>
            <el-descriptions-item label="最后上报状态">
              <span v-if="statusOverview?.heartbeat">
                CPU {{ statusOverview.heartbeat.cpu_usage ?? 0 }}% /
                内存 {{ statusOverview.heartbeat.memory_usage ?? 0 }}% /
                磁盘 {{ statusOverview.heartbeat.disk_usage ?? 0 }}%
              </span>
              <span v-else>-</span>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>

        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><DocumentCopy /></el-icon> 变更历史
        </h3>
        <el-card v-loading="historyLoading" shadow="never">
          <el-table :data="assetChanges" stripe size="small">
            <el-table-column prop="created_at" label="时间" width="170" />
            <el-table-column prop="change_type" label="类型" width="120" />
            <el-table-column prop="field_name" label="字段" width="140" />
            <el-table-column label="旧值" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                {{ formatHistoryValue(row.old_value) }}
              </template>
            </el-table-column>
            <el-table-column label="新值" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">
                {{ formatHistoryValue(row.new_value) }}
              </template>
            </el-table-column>
            <el-table-column prop="source_type" label="来源" width="110" />
            <el-table-column prop="operator_name" label="操作者" width="150" show-overflow-tooltip />
          </el-table>
        </el-card>

        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><Monitor /></el-icon> 状态历史
        </h3>
        <el-card v-loading="historyLoading" shadow="never">
          <el-table :data="statusHistory" stripe size="small">
            <el-table-column prop="heartbeat_time" label="心跳时间" width="170" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="getStatusType(row.status)">{{ getStatusText(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="cpu_usage" label="CPU" width="90" />
            <el-table-column prop="memory_usage" label="内存" width="90" />
            <el-table-column prop="disk_usage" label="磁盘" width="90" />
            <el-table-column prop="process_count" label="进程数" width="100" />
            <el-table-column prop="logged_users" label="登录用户" min-width="180" show-overflow-tooltip />
          </el-table>
        </el-card>
      </div>

      <!-- 编辑模式 -->
      <el-form v-else :model="asset" label-width="120px">
        <h3 style="margin-top: 20px; margin-bottom: 15px;">基本信息</h3>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="主机名">
              <el-input v-model="asset.hostname" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="序列号">
              <el-input v-model="asset.serial_number" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="制造商">
              <el-input v-model="asset.manufacturer" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="型号">
              <el-input v-model="asset.model" />
            </el-form-item>
          </el-col>
        </el-row>

        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><ShoppingCart /></el-icon> 采购信息
        </h3>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="采购日期">
              <el-date-picker v-model="asset.purchase_date" type="date" placeholder="选择日期" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="采购价格">
              <el-input v-model="asset.purchase_price" type="number" placeholder="单位：元">
                <template #prepend>¥</template>
              </el-input>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="供应商">
              <el-input v-model="asset.supplier" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="合同编号">
              <el-input v-model="asset.contract_no" />
            </el-form-item>
          </el-col>
        </el-row>

        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><Tools /></el-icon> 保修信息
        </h3>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="保修开始">
              <el-date-picker v-model="asset.warranty_start" type="date" placeholder="选择日期" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="保修结束">
              <el-date-picker v-model="asset.warranty_end" type="date" placeholder="选择日期" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="保修服务商">
              <el-input v-model="asset.warranty_provider" />
            </el-form-item>
          </el-col>
        </el-row>

        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><User /></el-icon> 使用信息
        </h3>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="部署日期">
              <el-date-picker v-model="asset.deployment_date" type="date" placeholder="选择日期" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="资产状态">
              <el-select v-model="asset.asset_status" style="width: 100%">
                <el-option label="在库" value="in_stock" />
                <el-option label="使用中" value="in_use" />
                <el-option label="维修中" value="maintenance" />
                <el-option label="已报废" value="retired" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="使用人">
              <el-input v-model="asset.user_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="使用部门">
              <el-input v-model="asset.department" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="位置">
              <el-input v-model="asset.location" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="负责人">
              <el-input v-model="asset.owner" />
            </el-form-item>
          </el-col>
        </el-row>

        <h3 style="margin-top: 30px; margin-bottom: 15px;">
          <el-icon><DocumentCopy /></el-icon> 其他信息
        </h3>
        <el-row :gutter="20">
          <el-col :span="12" v-if="asset.asset_status === 'retired'">
            <el-form-item label="报废日期">
              <el-date-picker v-model="asset.retire_date" type="date" placeholder="选择日期" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
          </el-col>
          <el-col :span="12" v-if="asset.asset_status === 'retired'">
            <el-form-item label="报废原因">
              <el-input v-model="asset.retire_reason" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="asset.notes" type="textarea" :rows="3" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getAssetChanges,
  getAssetDetail,
  getAssetStatus,
  getAssetStatusHistory,
  getAssetUptime,
  updateAsset
} from '@/api/asset'
import { ElMessage } from 'element-plus'
import { Monitor, ShoppingCart, Tools, User, DocumentCopy, Edit, Check, Close } from '@element-plus/icons-vue'
import dayjs from 'dayjs'

const route = useRoute()
const router = useRouter()
const asset = ref({})
const originalAsset = ref({})
const heartbeat = ref(null)
const statusOverview = ref(null)
const statusHistory = ref([])
const assetChanges = ref([])
const uptimeSummary = ref(null)
const historyLoading = ref(false)
const editMode = ref(false)
const saving = ref(false)

const loadAssetHistory = async () => {
  historyLoading.value = true
  try {
    const [statusData, historyData, changesData, uptimeData] = await Promise.all([
      getAssetStatus(route.params.id),
      getAssetStatusHistory(route.params.id, { limit: 20 }),
      getAssetChanges(route.params.id, { page: 1, page_size: 20 }),
      getAssetUptime(route.params.id, { days: 7 })
    ])
    statusOverview.value = statusData || null
    statusHistory.value = historyData?.data || []
    assetChanges.value = changesData?.data || []
    uptimeSummary.value = uptimeData || null
  } catch (error) {
    console.error('加载资产历史失败:', error)
  } finally {
    historyLoading.value = false
  }
}

const loadDetail = async () => {
  try {
    const data = await getAssetDetail(route.params.id)
    asset.value = data.asset || {}
    originalAsset.value = JSON.parse(JSON.stringify(data.asset || {}))
    heartbeat.value = data.heartbeat || null
    await loadAssetHistory()
  } catch (error) {
    console.error('加载失败:', error)
    ElMessage.error('加载资产详情失败')
  }
}

const saveAsset = async () => {
  try {
    saving.value = true
    await updateAsset(asset.value.id, asset.value)
    ElMessage.success('保存成功')
    editMode.value = false
    originalAsset.value = JSON.parse(JSON.stringify(asset.value))
    await loadDetail()
  } catch (error) {
    console.error('保存失败:', error)
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

const cancelEdit = () => {
  asset.value = JSON.parse(JSON.stringify(originalAsset.value))
  editMode.value = false
}

const getStatusType = (status) => {
  const map = { online: 'success', offline: 'danger', degraded: 'warning', unknown: 'info' }
  return map[status] || 'info'
}

const getStatusText = (status) => {
  const map = { online: '在线', offline: '离线', degraded: '降级', unknown: '未知' }
  return map[status] || status
}

const getAssetTypeText = (type) => {
  const map = { server: '服务器', pc: 'PC终端', switch: '交换机', router: '路由器', unknown: '未知' }
  return map[type] || type
}

const getAssetStatusType = (status) => {
  const map = { in_stock: 'info', in_use: 'success', maintenance: 'warning', retired: 'danger' }
  return map[status] || 'info'
}

const getAssetStatusText = (status) => {
  const map = { in_stock: '在库', in_use: '使用中', maintenance: '维修中', retired: '已报废' }
  return map[status] || status
}

const getWarrantyStatus = (endDate) => {
  if (!endDate) return null
  const end = dayjs(endDate)
  const now = dayjs()
  const daysLeft = end.diff(now, 'day')

  if (daysLeft < 0) {
    return { text: '已过保', type: 'danger' }
  } else if (daysLeft <= 30) {
    return { text: `剩余${daysLeft}天`, type: 'warning' }
  } else if (daysLeft <= 90) {
    return { text: `剩余${daysLeft}天`, type: '' }
  } else {
    return { text: '保修中', type: 'success' }
  }
}

const goToTerminal = () => {
  router.push({ name: 'TerminalDetail', params: { id: asset.value.id } })
}

const formatHistoryValue = (value) => {
  if (value === null || value === undefined || value === '') {
    return '-'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

onMounted(() => {
  loadDetail()
})
</script>

<style scoped>
.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.header-actions {
  display: flex;
  gap: 10px;
}
</style>
