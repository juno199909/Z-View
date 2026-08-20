<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">资产详情</h2>
        <div class="zv-page-subtitle">ID: {{ asset.id || '-' }}</div>
      </div>
      <div class="zv-page-actions">
        <el-button v-if="!editMode && asset.id" :icon="Edit" @click="editMode = true">编辑</el-button>
        <el-button v-if="editMode" type="primary" :icon="Check" :loading="saving" @click="saveAsset">保存</el-button>
        <el-button v-if="editMode" :icon="Close" @click="cancelEdit">取消</el-button>
        <el-button v-if="!editMode && asset.id" type="primary" plain :icon="Monitor" @click="goToTerminal">实时监控</el-button>
        <el-button :icon="ArrowLeft" @click="$router.back()">返回</el-button>
      </div>
    </div>

    <!-- 资产概览卡 -->
    <div class="zv-card zv-overview">
      <div class="zv-overview-avatar" :style="{ background: getTypeGradient(asset.asset_type) }">
        <el-icon :size="32"><component :is="getTypeIcon(asset.asset_type)" /></el-icon>
      </div>
      <div class="zv-overview-info">
        <div class="zv-overview-name">
          {{ asset.hostname || '加载中...' }}
          <span v-if="asset.status" class="zv-status-chip" :class="`is-${asset.status}`">
            <span class="zv-status-dot" :class="`is-${asset.status}`" />
            {{ getStatusText(asset.status) }}
          </span>
        </div>
        <div class="zv-overview-meta">
          <span><el-icon :size="13" /><span>{{ asset.ip_address || '-' }}</span></span>
          <span>{{ asset.os_type }} {{ asset.os_version }}</span>
          <span>{{ getAssetTypeText(asset.asset_type) }}</span>
        </div>
      </div>
      <div v-if="heartbeat" class="zv-overview-health">
        <div class="zv-health-item">
          <div class="zv-health-num">{{ heartbeat.cpu_usage || 0 }}%</div>
          <div class="zv-health-label">CPU</div>
        </div>
        <div class="zv-health-item">
          <div class="zv-health-num">{{ heartbeat.memory_usage || 0 }}%</div>
          <div class="zv-health-label">内存</div>
        </div>
        <div class="zv-health-item">
          <div class="zv-health-num">{{ heartbeat.disk_usage || 0 }}%</div>
          <div class="zv-health-label">磁盘</div>
        </div>
      </div>
    </div>

    <div v-if="!editMode" class="zv-detail-grid">
      <!-- 基本信息 -->
      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><InfoFilled /></el-icon>
          基本信息
        </h3>
        <div class="zv-info-grid">
          <div class="zv-info-item">
            <div class="zv-info-label">主机名</div>
            <div class="zv-info-value">{{ asset.hostname || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">IP 地址</div>
            <div class="zv-info-value zv-mono">{{ asset.ip_address || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">MAC 地址</div>
            <div class="zv-info-value zv-mono">{{ asset.mac_address || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">资产类型</div>
            <div class="zv-info-value">
              <el-tag size="small" effect="light" :style="{ color: getTypeColor(asset.asset_type), borderColor: getTypeColor(asset.asset_type) }">
                {{ getAssetTypeText(asset.asset_type) }}
              </el-tag>
            </div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">CPU 核心</div>
            <div class="zv-info-value">{{ asset.cpu_cores || '-' }} 核</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">内存</div>
            <div class="zv-info-value">{{ asset.memory_mb ? (asset.memory_mb / 1024).toFixed(1) + ' GB' : '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">磁盘容量</div>
            <div class="zv-info-value">{{ asset.disk_gb || '-' }} GB</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">序列号</div>
            <div class="zv-info-value zv-mono">{{ asset.serial_number || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">制造商</div>
            <div class="zv-info-value">{{ asset.manufacturer || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">型号</div>
            <div class="zv-info-value">{{ asset.model || '-' }}</div>
          </div>
        </div>
      </div>

      <!-- 采购信息 -->
      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><ShoppingCart /></el-icon>
          采购信息
        </h3>
        <div class="zv-info-grid">
          <div class="zv-info-item">
            <div class="zv-info-label">采购日期</div>
            <div class="zv-info-value">{{ asset.purchase_date || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">采购价格</div>
            <div class="zv-info-value">{{ asset.purchase_price ? '¥ ' + Number(asset.purchase_price).toLocaleString() : '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">供应商</div>
            <div class="zv-info-value">{{ asset.supplier || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">合同编号</div>
            <div class="zv-info-value zv-mono">{{ asset.contract_no || '-' }}</div>
          </div>
        </div>
      </div>

      <!-- 保修信息 -->
      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><Tools /></el-icon>
          保修信息
        </h3>
        <div class="zv-info-grid">
          <div class="zv-info-item">
            <div class="zv-info-label">保修开始</div>
            <div class="zv-info-value">{{ asset.warranty_start || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">保修结束</div>
            <div class="zv-info-value">
              <span>{{ asset.warranty_end || '-' }}</span>
              <el-tag v-if="getWarrantyStatus(asset.warranty_end)" :type="getWarrantyStatus(asset.warranty_end).type" size="small" effect="light" style="margin-left: 8px;">
                {{ getWarrantyStatus(asset.warranty_end).text }}
              </el-tag>
            </div>
          </div>
          <div class="zv-info-item zv-info-full">
            <div class="zv-info-label">保修服务商</div>
            <div class="zv-info-value">{{ asset.warranty_provider || '-' }}</div>
          </div>
        </div>
      </div>

      <!-- 使用信息 -->
      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><User /></el-icon>
          使用信息
        </h3>
        <div class="zv-info-grid">
          <div class="zv-info-item">
            <div class="zv-info-label">部署日期</div>
            <div class="zv-info-value">{{ asset.deployment_date || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">资产状态</div>
            <div class="zv-info-value">
              <el-tag size="small" :type="getAssetStatusType(asset.asset_status)" effect="light">
                {{ getAssetStatusText(asset.asset_status) }}
              </el-tag>
            </div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">使用人</div>
            <div class="zv-info-value">{{ asset.user_name || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">使用部门</div>
            <div class="zv-info-value">{{ asset.department || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">位置</div>
            <div class="zv-info-value">{{ asset.location || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">负责人</div>
            <div class="zv-info-value">{{ asset.owner || '-' }}</div>
          </div>
        </div>
      </div>

      <!-- 其他信息 -->
      <div v-if="asset.notes || asset.asset_status === 'retired'" class="zv-card zv-section zv-section-full">
        <h3 class="zv-section-title">
          <el-icon><DocumentCopy /></el-icon>
          其他信息
        </h3>
        <div class="zv-info-grid">
          <div v-if="asset.asset_status === 'retired'" class="zv-info-item">
            <div class="zv-info-label">报废日期</div>
            <div class="zv-info-value">{{ asset.retire_date || '-' }}</div>
          </div>
          <div v-if="asset.asset_status === 'retired'" class="zv-info-item">
            <div class="zv-info-label">报废原因</div>
            <div class="zv-info-value">{{ asset.retire_reason || '-' }}</div>
          </div>
          <div v-if="asset.notes" class="zv-info-item zv-info-full">
            <div class="zv-info-label">备注</div>
            <div class="zv-info-value">{{ asset.notes }}</div>
          </div>
        </div>
      </div>

      <!-- 运行状态 -->
      <div class="zv-card zv-section zv-section-full" v-loading="historyLoading">
        <h3 class="zv-section-title">
          <el-icon><Monitor /></el-icon>
          运行状态
        </h3>
        <div class="zv-info-grid">
          <div class="zv-info-item">
            <div class="zv-info-label">当前在线</div>
            <div class="zv-info-value">
              <el-tag :type="getStatusType(statusOverview?.current_status || asset.status)" size="small" effect="light">
                {{ getStatusText(statusOverview?.current_status || asset.status) }}
              </el-tag>
            </div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">Agent</div>
            <div class="zv-info-value">
              <el-tag :type="asset.agent_install_status === 'installed' ? 'success' : 'info'" size="small" effect="light">
                {{ asset.agent_install_status === 'installed' ? '已安装' : '未安装' }}
              </el-tag>
              <el-tag v-if="asset.agent_version" size="small" effect="plain" style="margin-left:6px">
                v{{ asset.agent_version }}
              </el-tag>
            </div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">当前在线时长</div>
            <div class="zv-info-value">{{ uptimeSummary?.current_uptime_text || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">近 7 天在线率</div>
            <div class="zv-info-value zv-num">{{ uptimeSummary?.availability_percent ?? '-' }}%</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">最后心跳</div>
            <div class="zv-info-value zv-mono">{{ statusOverview?.heartbeat?.heartbeat_time || heartbeat?.heartbeat_time || '-' }}</div>
          </div>
          <div class="zv-info-item">
            <div class="zv-info-label">最后上报</div>
            <div class="zv-info-value">
              <span v-if="statusOverview?.heartbeat">CPU {{ statusOverview.heartbeat.cpu_usage ?? 0 }}% · 内存 {{ statusOverview.heartbeat.memory_usage ?? 0 }}% · 磁盘 {{ statusOverview.heartbeat.disk_usage ?? 0 }}%</span>
              <span v-else>-</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 变更历史 -->
      <div class="zv-card zv-section zv-section-full" v-loading="historyLoading">
        <h3 class="zv-section-title">
          <el-icon><DocumentCopy /></el-icon>
          变更历史
        </h3>
        <el-table :data="assetChanges" stripe size="small" empty-text="暂无变更记录">
          <el-table-column prop="created_at" label="时间" width="170" />
          <el-table-column prop="change_type" label="类型" width="120" />
          <el-table-column prop="field_name" label="字段" width="140" />
          <el-table-column label="旧值" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ formatHistoryValue(row.old_value) }}</template>
          </el-table-column>
          <el-table-column label="新值" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ formatHistoryValue(row.new_value) }}</template>
          </el-table-column>
          <el-table-column prop="source_type" label="来源" width="110" />
          <el-table-column prop="operator_name" label="操作者" width="150" show-overflow-tooltip />
        </el-table>
      </div>

      <!-- 状态历史 -->
      <div class="zv-card zv-section zv-section-full" v-loading="historyLoading">
        <h3 class="zv-section-title">
          <el-icon><Monitor /></el-icon>
          状态历史
        </h3>
        <el-table :data="statusHistory" stripe size="small" empty-text="暂无状态历史">
          <el-table-column prop="heartbeat_time" label="心跳时间" width="170" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="getStatusType(row.status)" size="small" effect="light">{{ getStatusText(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="cpu_usage" label="CPU" width="80" />
          <el-table-column prop="memory_usage" label="内存" width="80" />
          <el-table-column prop="disk_usage" label="磁盘" width="80" />
          <el-table-column prop="process_count" label="进程" width="100" />
          <el-table-column prop="logged_users" label="登录用户" min-width="180" show-overflow-tooltip />
        </el-table>
      </div>
    </div>

    <!-- 编辑模式 -->
    <el-form v-else :model="asset" label-width="100px" class="zv-edit-form">
      <div class="zv-card zv-section">
        <h3 class="zv-section-title">基本信息</h3>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="主机名"><el-input v-model="asset.hostname" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="序列号"><el-input v-model="asset.serial_number" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="制造商"><el-input v-model="asset.manufacturer" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="型号"><el-input v-model="asset.model" /></el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><ShoppingCart /></el-icon>
          采购信息
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
            <el-form-item label="供应商"><el-input v-model="asset.supplier" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="合同编号"><el-input v-model="asset.contract_no" /></el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><Tools /></el-icon>
          保修信息
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
            <el-form-item label="保修服务商"><el-input v-model="asset.warranty_provider" /></el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><User /></el-icon>
          使用信息
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
          <el-col :span="12"><el-form-item label="使用人"><el-input v-model="asset.user_name" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="使用部门"><el-input v-model="asset.department" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="位置"><el-input v-model="asset.location" /></el-form-item></el-col>
          <el-col :span="12"><el-form-item label="负责人"><el-input v-model="asset.owner" /></el-form-item></el-col>
        </el-row>
      </div>

      <div class="zv-card zv-section">
        <h3 class="zv-section-title">
          <el-icon><DocumentCopy /></el-icon>
          其他信息
        </h3>
        <el-row :gutter="20">
          <el-col v-if="asset.asset_status === 'retired'" :span="12">
            <el-form-item label="报废日期">
              <el-date-picker v-model="asset.retire_date" type="date" placeholder="选择日期" style="width: 100%" value-format="YYYY-MM-DD" />
            </el-form-item>
          </el-col>
          <el-col v-if="asset.asset_status === 'retired'" :span="12">
            <el-form-item label="报废原因"><el-input v-model="asset.retire_reason" /></el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注"><el-input v-model="asset.notes" type="textarea" :rows="3" /></el-form-item>
          </el-col>
        </el-row>
      </div>
    </el-form>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  getAssetChanges, getAssetDetail, getAssetStatus, getAssetStatusHistory, getAssetUptime, updateAsset
} from '@/api/asset'
import { ElMessage } from 'element-plus'
import {
  Monitor, ShoppingCart, Tools, User, DocumentCopy, Edit, Check, Close,
  InfoFilled, ArrowLeft, Box, Connection, Share, Cpu
} from '@element-plus/icons-vue'
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
    // 支持从列表页“编辑”入口进入（/asset/detail/:id?edit=true）
    if (route.query.edit === 'true' || route.query.edit === '1') {
      editMode.value = true
    }
  } catch (error) {
    console.error('加载失败:', error)
    ElMessage.error('加载资产详情失败')
  }
}

const saveAsset = async () => {
  saving.value = true
  try {
    // 只提交可编辑字段，避免把 id/status/agent_version 等只读字段原样回传
    const editableFields = ['hostname', 'ip_address', 'mac_address', 'asset_type', 'status',
      'location', 'owner', 'group_id', 'manufacturer', 'model', 'serial_number', 'notes']
    const payload = {}
    for (const key of editableFields) {
      if (asset.value[key] !== undefined) payload[key] = asset.value[key]
    }
    await updateAsset(asset.value.id, payload)
    ElMessage.success('保存成功')
    editMode.value = false
    originalAsset.value = JSON.parse(JSON.stringify(asset.value))
    await loadDetail()
  } catch (error) {
    ElMessage.error('保存失败')
  } finally {
    saving.value = false
  }
}

const cancelEdit = () => {
  asset.value = JSON.parse(JSON.stringify(originalAsset.value))
  editMode.value = false
}

const TYPE_META = {
  server: { label: '服务器', icon: Cpu, color: '#3b82f6', gradient: 'linear-gradient(135deg, #3b82f6, #2563eb)' },
  pc:     { label: 'PC 终端', icon: Monitor, color: '#10b981', gradient: 'linear-gradient(135deg, #10b981, #059669)' },
  switch: { label: '交换机', icon: Connection, color: '#f59e0b', gradient: 'linear-gradient(135deg, #f59e0b, #d97706)' },
  router: { label: '路由器', icon: Share, color: '#8b5cf6', gradient: 'linear-gradient(135deg, #8b5cf6, #7c3aed)' }
}
const getAssetTypeText = (t) => TYPE_META[t]?.label || '未知'
const getTypeIcon = (t) => TYPE_META[t]?.icon || Box
const getTypeColor = (t) => TYPE_META[t]?.color || '#94a3b8'
const getTypeGradient = (t) => TYPE_META[t]?.gradient || 'linear-gradient(135deg, #94a3b8, #64748b)'

const getStatusType = (s) => ({ online: 'success', offline: 'danger', degraded: 'warning', unknown: 'info' }[s] || 'info')
const getStatusText = (s) => ({ online: '在线', offline: '离线', degraded: '降级', unknown: '未知' }[s] || s)

const getAssetStatusType = (s) => ({ in_stock: 'info', in_use: 'success', maintenance: 'warning', retired: 'danger' }[s] || 'info')
const getAssetStatusText = (s) => ({ in_stock: '在库', in_use: '使用中', maintenance: '维修中', retired: '已报废' }[s] || s)

const getWarrantyStatus = (endDate) => {
  if (!endDate) return null
  const daysLeft = dayjs(endDate).diff(dayjs(), 'day')
  if (daysLeft < 0) return { text: '已过保', type: 'danger' }
  if (daysLeft <= 30) return { text: `剩余 ${daysLeft} 天`, type: 'warning' }
  if (daysLeft <= 90) return { text: `剩余 ${daysLeft} 天`, type: 'info' }
  return { text: '保修中', type: 'success' }
}

const goToTerminal = () => router.push({ name: 'TerminalDetail', params: { id: asset.value.id } })
const formatHistoryValue = (v) => (v === null || v === undefined || v === '') ? '-' : (typeof v === 'object' ? JSON.stringify(v) : String(v))

onMounted(() => loadDetail())
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page {
  padding: $content-padding;
  max-width: 1600px;
  margin: 0 auto;
}

.zv-page-actions {
  display: flex;
  gap: 10px;
}

// ---- 概览卡 ----
.zv-overview {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 24px;
  margin-bottom: 16px;
  background: linear-gradient(135deg, $bg-card 0%, $slate-50 100%);
}

.zv-overview-avatar {
  width: 64px;
  height: 64px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.10);
  flex-shrink: 0;
}

.zv-overview-info {
  flex: 1;
  min-width: 0;
}

.zv-overview-name {
  font-size: 22px;
  font-weight: 700;
  color: $text-primary;
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.zv-overview-meta {
  display: flex;
  gap: 18px;
  font-size: 13px;
  color: $text-secondary;

  span {
    display: flex;
    align-items: center;
    gap: 4px;
  }
}

.zv-overview-health {
  display: flex;
  gap: 14px;
}

.zv-health-item {
  background: $bg-card;
  border: 1px solid $border-color-light;
  border-radius: $border-radius;
  padding: 12px 20px;
  text-align: center;
  min-width: 84px;
}

.zv-health-num {
  font-size: 22px;
  font-weight: 700;
  color: $brand-primary;
  font-family: $font-mono;
  line-height: 1;
}

.zv-health-label {
  font-size: 11px;
  color: $text-tertiary;
  margin-top: 4px;
}

// ---- 详情网格 ----
.zv-detail-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
}

.zv-section {
  padding: 20px 24px;
}

.zv-section-full {
  grid-column: 1 / -1;
}

.zv-section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 600;
  color: $text-primary;
  margin: 0 0 18px 0;
  padding-bottom: 12px;
  border-bottom: 1px solid $border-color-light;

  .el-icon { color: $brand-primary; }
}

.zv-info-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px 20px;
}

.zv-info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.zv-info-full {
  grid-column: 1 / -1;
}

.zv-info-label {
  font-size: 12px;
  color: $text-tertiary;
}

.zv-info-value {
  font-size: 14px;
  color: $text-primary;
  font-weight: 500;
}

.zv-mono { font-family: $font-mono; font-size: 13px; }
.zv-num { font-family: $font-mono; font-weight: 600; }

.zv-status-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
  border-radius: $radius-pill;
  font-size: 12px;
  font-weight: 500;
  background: $slate-50;
  color: $text-secondary;

  &.is-online  { background: rgba(16, 185, 129, 0.10); color: $success-color; }
  &.is-offline { background: rgba(239, 68, 68, 0.10); color: $danger-color; }
  &.is-warning { background: rgba(245, 158, 11, 0.10); color: $warning-color; }
  &.is-unknown { background: $slate-100; color: $text-tertiary; }
}

.zv-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

// ---- 编辑模式 ----
.zv-edit-form {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  > .zv-section { grid-column: 1 / -1; }
}

@media (max-width: 900px) {
  .zv-detail-grid,
  .zv-info-grid,
  .zv-edit-form {
    grid-template-columns: 1fr;
  }
  .zv-overview { flex-wrap: wrap; }
  .zv-overview-health { width: 100%; justify-content: stretch; }
}
</style>
