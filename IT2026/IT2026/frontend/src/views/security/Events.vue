<template>
  <div class="zv-security-events">
    <div class="zv-sec-header">
      <h2 class="zv-sec-title">安全事件中心</h2>
      <el-button :icon="Refresh" plain @click="loadData">刷新</el-button>
    </div>

    <div class="zv-sec-filter">
      <el-select v-model="filters.event_type" placeholder="事件类型" clearable style="width:150px">
        <el-option v-for="t in eventTypes" :key="t.value" :label="t.label" :value="t.value" />
      </el-select>
      <el-select v-model="filters.severity" placeholder="风险等级" clearable style="width:130px">
        <el-option label="严重" value="critical" /><el-option label="高危" value="high" />
        <el-option label="中危" value="medium" /><el-option label="低危" value="low" /><el-option label="信息" value="info" />
      </el-select>
      <el-select v-model="filters.status" placeholder="状态" clearable style="width:120px">
        <el-option label="待处置" value="open" /><el-option label="处理中" value="processing" />
        <el-option label="已解决" value="resolved" /><el-option label="已忽略" value="ignored" />
      </el-select>
<el-input v-model="filters.keyword" placeholder="搜索标题/描述/进程" clearable style="width:220px" @keyup.enter="handleSearch" />
          <el-button type="primary" @click="handleSearch">查询</el-button>
      <el-button :disabled="!selectedIds.length" @click="batchHandle">批量处置({{ selectedIds.length }})</el-button>
    </div>

    <el-table :data="events" stripe @selection-change="onSelectionChange" v-loading="loading" style="margin-top:12px">
      <el-table-column type="selection" width="40" />
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="hostname" label="终端" min-width="120" />
      <el-table-column prop="event_type" label="类型" width="120">
        <template #default="{ row }"><el-tag :type="typeTag(row.event_type)" size="small">{{ typeLabel(row.event_type) }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="severity" label="级别" width="80">
        <template #default="{ row }"><el-tag :type="severityTag(row.severity)" size="small" effect="dark">{{ severityLabel(row.severity) }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
      <el-table-column prop="process_name" label="进程" min-width="120" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }"><el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="occurred_at" label="发生时间" width="160" />
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="showDetail(row)">详情</el-button>
          <el-button link type="warning" v-if="row.status==='open'" @click="handle(row)">处置</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-model:current-page="pagination.page"
      v-model:page-size="pagination.page_size"
      :total="pagination.total"
      :page-sizes="[20, 50, 100]"
      layout="total, sizes, prev, pager, next"
      style="margin-top:16px;justify-content:flex-end;display:flex"
      @size-change="loadData" @current-change="loadData"
    />

    <el-drawer v-model="detailVisible" title="安全事件详情" size="500px">
      <div v-if="current" class="zv-sec-detail">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="事件ID">{{ current.id }}</el-descriptions-item>
          <el-descriptions-item label="终端">{{ current.hostname }} ({{ current.ip_address }})</el-descriptions-item>
          <el-descriptions-item label="类型">{{ typeLabel(current.event_type) }}</el-descriptions-item>
          <el-descriptions-item label="级别"><el-tag :type="severityTag(current.severity)" effect="dark" size="small">{{ severityLabel(current.severity) }}</el-tag></el-descriptions-item>
          <el-descriptions-item label="标题">{{ current.title }}</el-descriptions-item>
          <el-descriptions-item label="描述">{{ current.description }}</el-descriptions-item>
          <el-descriptions-item label="进程">{{ current.process_name }} (PID: {{ current.process_pid || '-' }})</el-descriptions-item>
          <el-descriptions-item label="文件">{{ current.file_path || '-' }}</el-descriptions-item>
          <el-descriptions-item label="远程">{{ current.remote_ip || '-' }}:{{ current.remote_port || '-' }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ statusLabel(current.status) }}</el-descriptions-item>
          <el-descriptions-item label="处置人">{{ current.handler || '-' }}</el-descriptions-item>
          <el-descriptions-item label="处置备注">{{ current.handle_note || '-' }}</el-descriptions-item>
          <el-descriptions-item label="发生时间">{{ current.occurred_at }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-drawer>

    <el-dialog v-model="handleVisible" title="处置安全事件" width="420px">
      <el-form label-width="80px">
        <el-form-item label="状态">
          <el-select v-model="handleForm.status" style="width:100%">
            <el-option label="处理中" value="processing" /><el-option label="已解决" value="resolved" /><el-option label="已忽略" value="ignored" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="handleForm.handle_note" type="textarea" :rows="3" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="handleVisible=false">取消</el-button>
        <el-button type="primary" @click="submitHandle">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getSecurityEvents, getSecurityEventDetail, handleSecurityEvent, batchHandleSecurityEvents } from '@/api/security'

const eventTypes = [
  { value: 'virus', label: '病毒/木马' }, { value: 'suspicious_process', label: '可疑进程' },
  { value: 'usb', label: 'USB事件' }, { value: 'firewall', label: '防火墙事件' },
  { value: 'app_control', label: '程序管控' }, { value: 'file_anomaly', label: '文件异常' },
  { value: 'registry_anomaly', label: '注册表异常' }, { value: 'network_anomaly', label: '网络异常' },
  { value: 'policy_exec', label: '策略执行' }
]
const typeMap = Object.fromEntries(eventTypes.map(t => [t.value, t.label]))
const typeLabel = (v) => typeMap[v] || v
const typeTag = (v) => ({ virus: 'danger', suspicious_process: 'danger', usb: 'warning', firewall: 'warning', app_control: 'warning', file_anomaly: 'danger', registry_anomaly: 'warning', network_anomaly: 'warning', policy_exec: 'info' })[v] || 'info'
const severityLabel = (v) => ({ critical: '严重', high: '高危', medium: '中危', low: '低危', info: '信息' })[v] || v
const severityTag = (v) => ({ critical: 'danger', high: 'warning', medium: 'primary', low: 'success', info: 'info' })[v] || 'info'
const statusLabel = (v) => ({ open: '待处置', processing: '处理中', resolved: '已解决', ignored: '已忽略' })[v] || v
const statusTag = (v) => ({ open: 'danger', processing: 'warning', resolved: 'success', ignored: 'info' })[v] || 'info'

const loading = ref(false)
const events = ref([])
const selectedIds = ref([])
const filters = reactive({ event_type: '', severity: '', status: '', keyword: '' })
const pagination = reactive({ page: 1, page_size: 20, total: 0 })
const detailVisible = ref(false)
const current = ref(null)
const handleVisible = ref(false)
const handleForm = reactive({ status: 'resolved', handle_note: '' })
let handleTargetId = null

// 筛选变化时回到第一页，避免落在超出总页数的空页
const handleSearch = () => { pagination.page = 1; loadData() }

const loadData = async () => {
  loading.value = true
  try {
    const res = await getSecurityEvents({
      page: pagination.page, page_size: pagination.page_size,
      event_type: filters.event_type || undefined, severity: filters.severity || undefined,
      status: filters.status || undefined, keyword: filters.keyword || undefined
    })
    events.value = res.data || []
    pagination.total = res.total || 0
  } catch (e) { ElMessage.error('加载安全事件失败') }
  finally { loading.value = false }
}
const onSelectionChange = (rows) => { selectedIds.value = rows.map(r => r.id) }
const showDetail = async (row) => {
  try { current.value = await getSecurityEventDetail(row.id); detailVisible.value = true }
  catch (e) { ElMessage.error('加载详情失败') }
}
const handle = (row) => { handleTargetId = row.id; handleForm.status = 'resolved'; handleForm.handle_note = ''; handleVisible.value = true }
const submitHandle = async () => {
  try { await handleSecurityEvent(handleTargetId, { status: handleForm.status, handle_note: handleForm.handle_note }); ElMessage.success('处置成功'); handleVisible.value = false; loadData() }
  catch (e) { ElMessage.error('处置失败') }
}
const batchHandle = async () => {
  try {
    await ElMessageBox.confirm(`确定批量处置 ${selectedIds.value.length} 条事件为已解决？`, '批量处置', { type: 'warning' })
    await batchHandleSecurityEvents({ event_ids: selectedIds.value, status: 'resolved', handle_note: '批量处置' })
    ElMessage.success('批量处置成功'); loadData()
  } catch (e) { if (e !== 'cancel') ElMessage.error('批量处置失败') }
}
onMounted(loadData)
</script>

<style scoped lang="scss">
.zv-security-events { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
.zv-sec-filter { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.zv-sec-detail { padding: 8px; }
</style>