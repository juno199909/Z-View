<template>
  <div class="compliance-management">
    <el-card class="header-card">
      <div class="header">
        <div>
          <h2>软件合规</h2>
          <p class="sub-title">管理规则、执行扫描、查看统计并导出终端软件合规结果</p>
        </div>
        <div class="header-actions">
          <el-button @click="refreshAll">刷新</el-button>
          <el-button :loading="exporting" @click="handleExport">导出结果</el-button>
          <el-button type="success" @click="openScanDialog()">立即扫描</el-button>
          <el-button type="primary" @click="openCreateDialog">新建规则</el-button>
        </div>
      </div>
    </el-card>

    <el-row :gutter="16" class="summary-row">
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover">
          <div class="summary-item">
            <span class="summary-label">合规规则</span>
            <span class="summary-value">{{ statsOverview.total_checks }}</span>
            <span class="summary-meta">启用中 {{ statsOverview.enabled_checks }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover">
          <div class="summary-item">
            <span class="summary-label">扫描结果</span>
            <span class="summary-value">{{ statsOverview.total_results }}</span>
            <span class="summary-meta">手工核查 {{ statsOverview.manual_review_count }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover">
          <div class="summary-item">
            <span class="summary-label">合规率</span>
            <span class="summary-value success">{{ statsOverview.compliance_rate }}%</span>
            <span class="summary-meta">自动判定 {{ statsOverview.evaluated_results }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="12" :lg="6">
        <el-card shadow="hover">
          <div class="summary-item">
            <span class="summary-label">不合规项</span>
            <span class="summary-value danger">{{ statsOverview.non_compliant_count }}</span>
            <span class="summary-meta">需优先整改</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" class="charts-row">
      <el-col :xs="24" :lg="8">
        <el-card class="chart-card" v-loading="statsLoading">
          <template #header>
            <div class="card-header">
              <span>合规分布</span>
            </div>
          </template>
          <VChart class="chart" :option="complianceChartOption" autoresize />
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="8">
        <el-card class="chart-card" v-loading="statsLoading">
          <template #header>
            <div class="card-header">
              <span>风险级别分布</span>
            </div>
          </template>
          <VChart class="chart" :option="severityChartOption" autoresize />
        </el-card>
      </el-col>
      <el-col :xs="24" :lg="8">
        <el-card class="chart-card" v-loading="statsLoading">
          <template #header>
            <div class="card-header">
              <span>不合规规则 Top 10</span>
            </div>
          </template>
          <VChart class="chart" :option="topChecksChartOption" autoresize />
        </el-card>
      </el-col>
    </el-row>

    <el-card class="table-card">
      <template #header>
        <div class="card-header">
          <span>合规规则</span>
          <el-button text type="primary" @click="loadChecks">刷新规则</el-button>
        </div>
      </template>

      <el-table :data="checks" v-loading="checksLoading" style="width: 100%">
        <el-table-column prop="check_name" label="规则名称" min-width="220" />
        <el-table-column prop="check_type" label="检查类型" width="120">
          <template #default="{ row }">
            <el-tag :type="getCheckTypeTag(row.check_type)">{{ getCheckTypeLabel(row.check_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="software_name" label="软件名称" min-width="180" />
        <el-table-column prop="required_version" label="目标版本" width="140">
          <template #default="{ row }">
            {{ row.required_version || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="severity" label="风险级别" width="120">
          <template #default="{ row }">
            <el-tag :type="getSeverityTag(row.severity)">{{ getSeverityLabel(row.severity) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="应用范围" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">
            {{ formatGroupScope(row.apply_to_groups) }}
          </template>
        </el-table-column>
        <el-table-column prop="enabled" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用中' : '已停用' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="340" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button size="small" type="primary" @click="applyCheckFilter(row.id)">查看结果</el-button>
              <el-button size="small" @click="openEditDialog(row)">编辑</el-button>
              <el-button size="small" type="success" @click="openScanDialog(row)">扫描</el-button>
              <el-button size="small" type="danger" @click="handleDeleteRule(row)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="table-card">
      <template #header>
        <div class="card-header">
          <span>扫描结果</span>
          <div class="filter-bar">
            <div class="quick-filter-group">
              <span class="quick-filter-label">风险快捷</span>
              <el-button
                v-for="option in severityQuickFilters"
                :key="option.value || 'all'"
                size="small"
                type="primary"
                :plain="resultFilters.severity !== option.value"
                @click="applySeverityQuickFilter(option.value)"
              >
                {{ option.label }}
              </el-button>
            </div>
            <el-select
              v-model="resultFilters.check_id"
              placeholder="按规则筛选"
              clearable
              style="width: 220px"
              @change="handleFilterChange"
            >
              <el-option
                v-for="check in checks"
                :key="check.id"
                :label="check.check_name"
                :value="check.id"
              />
            </el-select>
            <el-select
              v-model="resultFilters.is_compliant"
              placeholder="合规状态"
              clearable
              style="width: 160px"
              @change="handleFilterChange"
            >
              <el-option label="合规" :value="true" />
              <el-option label="不合规" :value="false" />
            </el-select>
            <el-select
              v-model="resultFilters.severity"
              placeholder="风险级别"
              clearable
              style="width: 160px"
              @change="handleFilterChange"
            >
              <el-option label="低" value="low" />
              <el-option label="中" value="medium" />
              <el-option label="高" value="high" />
              <el-option label="严重" value="critical" />
            </el-select>
            <el-button @click="resetResultFilters">重置筛选</el-button>
          </div>
        </div>
      </template>

      <el-table :data="results" v-loading="resultsLoading" style="width: 100%">
        <el-table-column prop="check_name" label="规则名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="severity" label="风险级别" width="120">
          <template #default="{ row }">
            <el-tag :type="getSeverityTag(row.severity)">{{ getSeverityLabel(row.severity) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="终端" min-width="180">
          <template #default="{ row }">
            <div>{{ row.hostname || '-' }}</div>
            <div class="muted-line">{{ row.ip_address || '-' }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="expected_software" label="目标软件" min-width="180" />
        <el-table-column prop="current_version" label="当前版本" width="150">
          <template #default="{ row }">
            {{ row.current_version || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="expected_version" label="要求版本" width="150">
          <template #default="{ row }">
            {{ row.expected_version || '-' }}
          </template>
        </el-table-column>
        <el-table-column prop="is_compliant" label="结果" width="110">
          <template #default="{ row }">
            <el-tag :type="getComplianceStatusTag(row)">
              {{ getComplianceStatusLabel(row) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="details" label="说明" min-width="280" show-overflow-tooltip />
        <el-table-column prop="checked_at" label="扫描时间" width="170" />
        <el-table-column label="整改动作" width="170" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="getRemediationActionLabel(row)"
              size="small"
              type="primary"
              :loading="remediationLoadingMap[row.id]"
              @click="handleRemediation(row)"
            >
              {{ getRemediationActionLabel(row) }}
            </el-button>
            <span v-else class="muted-text">无需联动</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="resultsPagination.page"
          v-model:page-size="resultsPagination.page_size"
          :total="resultsPagination.total"
          :page-sizes="[10, 20, 50, 100]"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="loadResults"
          @current-change="loadResults"
        />
      </div>
    </el-card>

    <el-card class="table-card">
      <template #header>
        <div class="card-header">
          <span>扫描历史任务</span>
          <div class="filter-bar">
            <el-tag type="info">仅展示软件合规扫描任务</el-tag>
            <el-button text type="primary" @click="loadScanTasks">刷新历史</el-button>
          </div>
        </div>
      </template>

      <el-table :data="scanTasks" v-loading="scanTasksLoading" style="width: 100%">
        <el-table-column prop="task_name" label="任务名称" min-width="220" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="getTaskStatusTag(row.status)">{{ getTaskStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="progress" label="进度" width="180">
          <template #default="{ row }">
            <el-progress :percentage="Number(row.progress || 0)" :status="getTaskProgressStatus(row.status)" />
          </template>
        </el-table-column>
        <el-table-column label="执行情况" width="190">
          <template #default="{ row }">
            <div class="task-summary">
              <div>目标 {{ row.target_count || 0 }} 台</div>
              <div>成功 {{ row.success_count || 0 }} / 失败 {{ row.failed_count || 0 }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column prop="start_time" label="开始时间" width="170" />
        <el-table-column prop="end_time" label="结束时间" width="170" />
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" @click="openTaskDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pagination">
        <el-pagination
          v-model:current-page="scanTaskPagination.page"
          v-model:page-size="scanTaskPagination.page_size"
          :total="scanTaskPagination.total"
          :page-sizes="[5, 10, 20]"
          layout="total, sizes, prev, pager, next"
          @size-change="loadScanTasks"
          @current-change="loadScanTasks"
        />
      </div>
    </el-card>

    <el-dialog
      v-model="showRuleDialog"
      :title="ruleDialogTitle"
      width="720px"
      destroy-on-close
      @closed="resetRuleForm"
    >
      <el-form ref="ruleFormRef" :model="ruleForm" :rules="ruleRules" label-width="110px">
        <el-form-item label="规则名称" prop="check_name">
          <el-input v-model="ruleForm.check_name" placeholder="例如：浏览器必须安装 Chrome" />
        </el-form-item>
        <el-form-item label="检查类型" prop="check_type">
          <el-select v-model="ruleForm.check_type" style="width: 100%">
            <el-option label="必须安装" value="required" />
            <el-option label="禁止安装" value="forbidden" />
            <el-option label="版本不低于" value="version" />
            <el-option label="许可证合规" value="license" />
          </el-select>
        </el-form-item>90
        <el-form-item label="软件名称" prop="software_name">
          <el-input v-model="ruleForm.software_name" placeholder="例如：Google Chrome" />
        </el-form-item>
        <el-form-item v-if="showRequiredVersion" label="目标版本" prop="required_version">
          <el-input v-model="ruleForm.required_version" placeholder="例如：120.0.0" />
        </el-form-item>
        <el-form-item label="风险级别" prop="severity">
          <el-select v-model="ruleForm.severity" style="width: 100%">
            <el-option label="低" value="low" />
            <el-option label="中" value="medium" />
            <el-option label="高" value="high" />
            <el-option label="严重" value="critical" />
          </el-select>
        </el-form-item>
        <el-form-item label="规则状态">
          <el-switch
            v-model="ruleForm.enabled"
            active-text="启用"
            inactive-text="停用"
          />
        </el-form-item>
        <el-form-item label="应用分组">
          <el-select
            v-model="ruleForm.apply_to_groups"
            multiple
            filterable
            clearable
            collapse-tags
            placeholder="不选则作用于全部终端"
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
        <el-alert
          v-if="ruleForm.check_type === 'license'"
          type="warning"
          :closable="false"
          title="当前平台还没有上报许可证明细，许可证规则可先建档；扫描时会标记为手工核查，不计入自动不合规。"
        />
      </el-form>
      <template #footer>
        <el-button @click="showRuleDialog = false">取消</el-button>
        <el-button type="primary" :loading="ruleSubmitting" @click="submitRule">
          {{ ruleSubmitText }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="showScanDialog" title="执行合规扫描" width="760px" destroy-on-close @closed="resetScanForm">
      <el-form ref="scanFormRef" :model="scanForm" label-width="110px">
        <el-form-item label="任务名称">
          <el-input v-model="scanForm.task_name" placeholder="留空则自动生成任务名称" />
        </el-form-item>
        <el-form-item label="扫描规则">
          <el-select
            v-model="scanForm.check_ids"
            multiple
            collapse-tags
            filterable
            clearable
            placeholder="不选则扫描全部启用规则"
            style="width: 100%"
          >
            <el-option
              v-for="check in checks"
              :key="check.id"
              :label="check.check_name"
              :value="check.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="目标范围">
          <el-radio-group v-model="scanForm.asset_scope">
            <el-radio value="all">全部终端</el-radio>
            <el-radio value="selected">指定终端</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="scanForm.asset_scope === 'selected'" label="目标终端">
          <el-select
            v-model="scanForm.asset_ids"
            multiple
            filterable
            collapse-tags
            placeholder="请选择终端"
            style="width: 100%"
          >
            <el-option
              v-for="asset in assets"
              :key="asset.id"
              :label="`${asset.hostname || '未命名终端'} (${asset.ip_address || '-'})`"
              :value="asset.id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showScanDialog = false">取消</el-button>
        <el-button type="primary" :loading="scanLoading" @click="submitScan">开始扫描</el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="showTaskDetailDialog"
      title="扫描任务详情"
      width="980px"
      destroy-on-close
    >
      <template v-if="selectedTask">
        <el-descriptions :column="2" border class="task-detail-descriptions">
          <el-descriptions-item label="任务名称">{{ selectedTask.task_name }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="getTaskStatusTag(selectedTask.status)">{{ getTaskStatusLabel(selectedTask.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="任务类型">{{ selectedTask.task_type || '-' }}</el-descriptions-item>
          <el-descriptions-item label="目标数量">{{ selectedTask.target_count || 0 }}</el-descriptions-item>
          <el-descriptions-item label="开始时间">{{ selectedTask.start_time || '-' }}</el-descriptions-item>
          <el-descriptions-item label="结束时间">{{ selectedTask.end_time || '-' }}</el-descriptions-item>
        </el-descriptions>

        <el-table :data="selectedTaskResults" v-loading="taskDetailLoading" style="width: 100%; margin-top: 16px;" max-height="420">
          <el-table-column prop="hostname" label="终端" min-width="180">
            <template #default="{ row }">
              <div>{{ row.hostname || '-' }}</div>
              <div class="muted-line">{{ row.ip_address || '-' }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="getTaskStatusTag(row.status)">{{ getTaskStatusLabel(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="进度" width="130">
            <template #default="{ row }">
              <el-progress :percentage="Number(row.progress || 0)" :status="getTaskProgressStatus(row.status)" />
            </template>
          </el-table-column>
          <el-table-column prop="duration" label="耗时" width="100">
            <template #default="{ row }">
              {{ row.duration ? `${row.duration}s` : '-' }}
            </template>
          </el-table-column>
          <el-table-column prop="stdout_log" label="执行摘要" min-width="300" show-overflow-tooltip>
            <template #default="{ row }">
              {{ row.stdout_log || row.error_message || '-' }}
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { PieChart, BarChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import { getAssetList } from '@/api/asset'
import { getGroups } from '@/api/group'
import {
  createSoftwareTask,
  createComplianceCheck,
  deleteComplianceCheck,
  exportComplianceResults,
  getComplianceChecks,
  getSoftwarePackages,
  getSoftwareTaskDetail,
  getSoftwareTasks,
  getComplianceResults,
  getComplianceStats,
  triggerComplianceScan,
  updateComplianceCheck
} from '@/api/software'

use([CanvasRenderer, PieChart, BarChart, GridComponent, LegendComponent, TitleComponent, TooltipComponent])

const checks = ref([])
const results = ref([])
const groups = ref([])
const assets = ref([])
const scanTasks = ref([])
const selectedTask = ref(null)
const selectedTaskResults = ref([])

const checksLoading = ref(false)
const resultsLoading = ref(false)
const statsLoading = ref(false)
const scanTasksLoading = ref(false)
const ruleSubmitting = ref(false)
const scanLoading = ref(false)
const exporting = ref(false)
const taskDetailLoading = ref(false)

const showRuleDialog = ref(false)
const showScanDialog = ref(false)
const showTaskDetailDialog = ref(false)
const ruleDialogMode = ref('create')
const editingCheckId = ref(null)

const ruleFormRef = ref(null)
const remediationLoadingMap = reactive({})

const resultsPagination = reactive({
  page: 1,
  page_size: 20,
  total: 0
})

const scanTaskPagination = reactive({
  page: 1,
  page_size: 5,
  total: 0
})

const resultFilters = reactive({
  check_id: null,
  is_compliant: '',
  severity: ''
})

const severityQuickFilters = [
  { label: '全部', value: '' },
  { label: '严重', value: 'critical' },
  { label: '高', value: 'high' },
  { label: '中', value: 'medium' },
  { label: '低', value: 'low' }
]

const ASSET_FETCH_PAGE_SIZE = 100

const stats = reactive({
  overview: {
    total_checks: 0,
    enabled_checks: 0,
    total_results: 0,
    evaluated_results: 0,
    compliant_count: 0,
    non_compliant_count: 0,
    manual_review_count: 0,
    compliance_rate: 0
  },
  severity_distribution: [],
  top_non_compliant_checks: [],
  check_type_distribution: []
})

const ruleForm = reactive({
  check_name: '',
  check_type: 'required',
  software_name: '',
  required_version: '',
  severity: 'medium',
  enabled: true,
  apply_to_groups: []
})

const scanForm = reactive({
  task_name: '',
  asset_scope: 'all',
  asset_ids: [],
  check_ids: []
})

const ruleRules = {
  check_name: [{ required: true, message: '请输入规则名称', trigger: 'blur' }],
  check_type: [{ required: true, message: '请选择检查类型', trigger: 'change' }],
  software_name: [{ required: true, message: '请输入软件名称', trigger: 'blur' }],
  required_version: [
    {
      validator: (_rule, value, callback) => {
        if (showRequiredVersion.value && !value) {
          callback(new Error('请输入目标版本'))
          return
        }
        callback()
      },
      trigger: 'blur'
    }
  ]
}

const statsOverview = computed(() => stats.overview)
const showRequiredVersion = computed(() => ruleForm.check_type === 'version')
const ruleDialogTitle = computed(() => (ruleDialogMode.value === 'create' ? '新建合规规则' : '编辑合规规则'))
const ruleSubmitText = computed(() => (ruleDialogMode.value === 'create' ? '创建规则' : '保存修改'))

const hasMeaningfulValue = (value) => value !== '' && value !== null && value !== undefined

const buildResultParams = (includePaging = true) => {
  const params = {}
  if (includePaging) {
    params.page = resultsPagination.page
    params.page_size = resultsPagination.page_size
  }
  if (hasMeaningfulValue(resultFilters.check_id)) {
    params.check_id = resultFilters.check_id
  }
  if (resultFilters.is_compliant === true || resultFilters.is_compliant === false) {
    params.is_compliant = resultFilters.is_compliant
  }
  if (hasMeaningfulValue(resultFilters.severity)) {
    params.severity = resultFilters.severity
  }
  return params
}

const complianceChartOption = computed(() => ({
  tooltip: { trigger: 'item' },
  legend: { bottom: 0 },
  series: [
    {
      name: '合规分布',
      type: 'pie',
      radius: ['48%', '72%'],
      data: [
        { value: statsOverview.value.compliant_count, name: '合规', itemStyle: { color: '#67c23a' } },
        { value: statsOverview.value.non_compliant_count, name: '不合规', itemStyle: { color: '#f56c6c' } },
        { value: statsOverview.value.manual_review_count, name: '手工核查', itemStyle: { color: '#909399' } }
      ],
      label: { formatter: '{b}\n{d}%' }
    }
  ]
}))

const severityChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 40, right: 20, top: 30, bottom: 40 },
  xAxis: {
    type: 'category',
    data: stats.severity_distribution.map(item => getSeverityLabel(item.severity))
  },
  yAxis: { type: 'value' },
  series: [
    {
      type: 'bar',
      barWidth: 30,
      data: stats.severity_distribution.map(item => ({
        value: item.total,
        itemStyle: { color: getSeverityColor(item.severity) }
      }))
    }
  ]
}))

const topChecksChartOption = computed(() => ({
  tooltip: { trigger: 'axis' },
  grid: { left: 110, right: 20, top: 20, bottom: 20, containLabel: true },
  xAxis: { type: 'value' },
  yAxis: {
    type: 'category',
    data: stats.top_non_compliant_checks.map(item => item.check_name),
    inverse: true
  },
  series: [
    {
      type: 'bar',
      data: stats.top_non_compliant_checks.map(item => ({
        value: item.total,
        itemStyle: { color: '#e6a23c' }
      })),
      label: { show: true, position: 'right' }
    }
  ]
}))

const loadChecks = async () => {
  checksLoading.value = true
  try {
    const res = await getComplianceChecks({ page: 1, page_size: 100 })
    checks.value = (res.data || []).map(item => ({
      ...item,
      enabled: Boolean(item.enabled),
      apply_to_groups: Array.isArray(item.apply_to_groups) ? item.apply_to_groups : []
    }))
  } finally {
    checksLoading.value = false
  }
}

const loadResults = async () => {
  resultsLoading.value = true
  try {
    const res = await getComplianceResults(buildResultParams(true))
    results.value = res.data || []
    resultsPagination.total = res.total || 0
  } finally {
    resultsLoading.value = false
  }
}

const loadStats = async () => {
  statsLoading.value = true
  try {
    const res = await getComplianceStats(buildResultParams(false))
    stats.overview = {
      total_checks: res.overview?.total_checks || 0,
      enabled_checks: res.overview?.enabled_checks || 0,
      total_results: res.overview?.total_results || 0,
      evaluated_results: res.overview?.evaluated_results || 0,
      compliant_count: res.overview?.compliant_count || 0,
      non_compliant_count: res.overview?.non_compliant_count || 0,
      manual_review_count: res.overview?.manual_review_count || 0,
      compliance_rate: res.overview?.compliance_rate || 0
    }
    stats.severity_distribution = res.severity_distribution || []
    stats.top_non_compliant_checks = res.top_non_compliant_checks || []
    stats.check_type_distribution = res.check_type_distribution || []
  } finally {
    statsLoading.value = false
  }
}

const loadScanTasks = async () => {
  scanTasksLoading.value = true
  try {
    const res = await getSoftwareTasks({
      page: scanTaskPagination.page,
      page_size: scanTaskPagination.page_size,
      task_type: 'check',
      software_name: 'compliance-scan'
    })
    scanTasks.value = res.data || []
    scanTaskPagination.total = res.total || 0
  } finally {
    scanTasksLoading.value = false
  }
}

const loadGroups = async () => {
  const res = await getGroups()
  groups.value = res.data || []
}

const loadAssets = async () => {
  try {
    let page = 1
    let total = 0
    const allAssets = []

    do {
      const res = await getAssetList({ page, page_size: ASSET_FETCH_PAGE_SIZE })
      const pageData = Array.isArray(res.data) ? res.data : []
      total = Number(res.total || 0)
      allAssets.push(...pageData)
      page += 1

      if (!pageData.length) {
        break
      }
    } while (allAssets.length < total)

    assets.value = allAssets
    return allAssets
  } catch (error) {
    console.error('加载终端列表失败', error)
    const detail = error.response?.data?.detail || error.message || '未知错误'
    ElMessage.error(`加载终端列表失败：${detail}`)
    throw error
  }
}

const refreshAll = async () => {
  await Promise.all([loadChecks(), loadResults(), loadStats(), loadScanTasks()])
}

const refreshResultSection = async () => {
  await Promise.all([loadResults(), loadStats()])
}

const resetRuleForm = () => {
  ruleFormRef.value?.resetFields()
  ruleDialogMode.value = 'create'
  editingCheckId.value = null
  ruleForm.check_name = ''
  ruleForm.check_type = 'required'
  ruleForm.software_name = ''
  ruleForm.required_version = ''
  ruleForm.severity = 'medium'
  ruleForm.enabled = true
  ruleForm.apply_to_groups = []
}

const resetScanForm = () => {
  scanForm.task_name = ''
  scanForm.asset_scope = 'all'
  scanForm.asset_ids = []
  scanForm.check_ids = []
}

const openCreateDialog = () => {
  resetRuleForm()
  showRuleDialog.value = true
}

const openEditDialog = (check) => {
  resetRuleForm()
  ruleDialogMode.value = 'edit'
  editingCheckId.value = check.id
  ruleForm.check_name = check.check_name || ''
  ruleForm.check_type = check.check_type || 'required'
  ruleForm.software_name = check.software_name || ''
  ruleForm.required_version = check.required_version || ''
  ruleForm.severity = check.severity || 'medium'
  ruleForm.enabled = Boolean(check.enabled)
  ruleForm.apply_to_groups = Array.isArray(check.apply_to_groups) ? [...check.apply_to_groups] : []
  showRuleDialog.value = true
}

const handleFilterChange = async () => {
  resultsPagination.page = 1
  await refreshResultSection()
}

const resetResultFilters = async () => {
  resultFilters.check_id = null
  resultFilters.is_compliant = ''
  resultFilters.severity = ''
  resultsPagination.page = 1
  await refreshResultSection()
}

const applyCheckFilter = async (checkId) => {
  resultFilters.check_id = checkId
  resultsPagination.page = 1
  await refreshResultSection()
}

const applySeverityQuickFilter = async (severity) => {
  resultFilters.severity = severity
  resultsPagination.page = 1
  await refreshResultSection()
}

const submitRule = async () => {
  try {
    await ruleFormRef.value?.validate()
  } catch {
    return
  }

  ruleSubmitting.value = true
  try {
    const payload = {
      check_name: ruleForm.check_name,
      check_type: ruleForm.check_type,
      software_name: ruleForm.software_name,
      required_version: showRequiredVersion.value ? ruleForm.required_version : null,
      severity: ruleForm.severity,
      enabled: ruleForm.enabled,
      apply_to_groups: ruleForm.apply_to_groups
    }

    if (ruleDialogMode.value === 'create') {
      const { enabled, ...createPayload } = payload
      await createComplianceCheck(createPayload)
      ElMessage.success('合规规则创建成功')
    } else {
      await updateComplianceCheck(editingCheckId.value, payload)
      ElMessage.success('合规规则更新成功')
    }

    showRuleDialog.value = false
    await Promise.all([loadChecks(), loadStats()])
  } finally {
    ruleSubmitting.value = false
  }
}

const handleDeleteRule = async (check) => {
  try {
    await ElMessageBox.confirm(
      `确定删除规则“${check.check_name}”吗？相关合规结果也会一并清理。`,
      '删除确认',
      {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消'
      }
    )

    await deleteComplianceCheck(check.id)
    ElMessage.success('合规规则删除成功')

    if (resultFilters.check_id === check.id) {
      resultFilters.check_id = null
    }
    await refreshAll()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') {
      console.error('删除合规规则失败', error)
    }
  }
}

const openScanDialog = async (check = null) => {
  try {
    if (!assets.value.length) {
      await loadAssets()
    }
    scanForm.check_ids = check ? [check.id] : []
    showScanDialog.value = true
  } catch (error) {
    console.error('打开扫描弹窗失败', error)
  }
}

const submitScan = async () => {
  if (scanForm.asset_scope === 'selected' && !scanForm.asset_ids.length) {
    ElMessage.warning('请选择至少一个终端')
    return
  }

  scanLoading.value = true
  try {
    const payload = {
      task_name: scanForm.task_name || undefined,
      check_ids: scanForm.check_ids.length ? scanForm.check_ids : undefined,
      asset_ids: scanForm.asset_scope === 'selected' ? scanForm.asset_ids : undefined
    }

    const res = await triggerComplianceScan(payload)
    const summary = res.summary || {}
    ElMessage.success(
      `扫描完成：终端 ${summary.asset_count || 0} 台，不合规 ${summary.non_compliant_count || 0} 项，手工核查 ${summary.manual_review_count || 0} 项`
    )
    showScanDialog.value = false
    resultsPagination.page = 1
    scanTaskPagination.page = 1
    await refreshAll()
  } finally {
    scanLoading.value = false
  }
}

const handleExport = async () => {
  exporting.value = true
  try {
    const blob = await exportComplianceResults(buildResultParams(false))
    const downloadUrl = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = `software-compliance-${dayjs().format('YYYYMMDD-HHmmss')}.csv`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(downloadUrl)
    ElMessage.success('合规结果导出成功')
  } finally {
    exporting.value = false
  }
}

const formatGroupScope = (groupIds = []) => {
  if (!groupIds.length) {
    return '全部终端'
  }
  const names = groupIds
    .map(groupId => groups.value.find(group => group.id === groupId)?.name || `分组 ${groupId}`)
    .filter(Boolean)
  return names.join('、')
}

const normalizeSoftwareName = (value) => String(value || '')
  .toLowerCase()
  .replace(/[\s._-]+/g, '')

const pickBestPackage = (row, packages = []) => {
  const expectedName = normalizeSoftwareName(row.expected_software)
  const expectedVersion = row.expected_version || ''

  if (!expectedName || !packages.length) {
    return null
  }

  const scored = packages
    .map(pkg => {
      const displayName = normalizeSoftwareName(pkg.display_name)
      const packageName = normalizeSoftwareName(pkg.package_name)
      let score = 0

      if (displayName === expectedName || packageName === expectedName) {
        score += 100
      } else if (displayName.includes(expectedName) || packageName.includes(expectedName) || expectedName.includes(displayName) || expectedName.includes(packageName)) {
        score += 60
      }

      if (expectedVersion && pkg.version === expectedVersion) {
        score += 30
      }

      return { pkg, score }
    })
    .filter(item => item.score > 0)
    .sort((left, right) => right.score - left.score)

  return scored[0]?.pkg || null
}

const getRemediationActionLabel = (row) => {
  if (row.compliance_status !== 'non_compliant') {
    return ''
  }

  const labelMap = {
    required: '发起安装',
    version: '发起升级',
    forbidden: '发起卸载'
  }
  return labelMap[row.check_type] || ''
}

const getRemediationTaskType = (row) => {
  const taskTypeMap = {
    required: 'install',
    version: 'upgrade',
    forbidden: 'uninstall'
  }
  return taskTypeMap[row.check_type] || ''
}

const getTaskPriorityBySeverity = (severity) => {
  if (severity === 'critical' || severity === 'high') {
    return 'high'
  }
  return 'normal'
}

const handleRemediation = async (row) => {
  const actionLabel = getRemediationActionLabel(row)
  const taskType = getRemediationTaskType(row)

  if (!actionLabel || !taskType) {
    ElMessage.info('当前结果不需要联动整改')
    return
  }
  if (!row.asset_id) {
    ElMessage.warning('当前结果缺少终端标识，无法发起整改任务')
    return
  }

  remediationLoadingMap[row.id] = true
  try {
    let packageId
    if (taskType === 'install' || taskType === 'upgrade') {
      const packageRes = await getSoftwarePackages({
        page: 1,
        page_size: 100,
        keyword: row.expected_software,
        status: 'available'
      })
      const matchedPackage = pickBestPackage(row, packageRes.data || [])
      if (!matchedPackage) {
        ElMessage.warning(`软件仓库未找到与“${row.expected_software}”匹配的可用软件包，暂无法联动`)
        return
      }
      packageId = matchedPackage.id
    }

    await createSoftwareTask({
      task_name: `${actionLabel}-${row.expected_software}-${row.hostname || row.asset_id}`,
      task_type: taskType,
      package_id: packageId,
      software_name: row.expected_software,
      target_type: 'asset',
      target_ids: [row.asset_id],
      schedule_type: 'immediate',
      priority: getTaskPriorityBySeverity(row.severity)
    })
    ElMessage.success(`${actionLabel}任务已创建，请到任务管理跟踪执行进度`)
  } catch (error) {
    console.error('创建整改任务失败', error)
  } finally {
    remediationLoadingMap[row.id] = false
  }
}

const getCheckTypeLabel = (type) => {
  const labels = {
    required: '必须安装',
    forbidden: '禁止安装',
    version: '版本合规',
    license: '许可证（手工核查）'
  }
  return labels[type] || type
}

const getCheckTypeTag = (type) => {
  const tags = {
    required: 'success',
    forbidden: 'danger',
    version: 'warning',
    license: 'info'
  }
  return tags[type] || 'info'
}

const getSeverityLabel = (severity) => {
  const labels = { low: '低', medium: '中', high: '高', critical: '严重' }
  return labels[severity] || severity
}

const getSeverityTag = (severity) => {
  const tags = { low: 'info', medium: 'warning', high: 'danger', critical: 'danger' }
  return tags[severity] || 'info'
}

const getSeverityColor = (severity) => {
  const colors = {
    low: '#909399',
    medium: '#e6a23c',
    high: '#f56c6c',
    critical: '#c45656'
  }
  return colors[severity] || '#409eff'
}

const getComplianceStatusLabel = (row) => {
  if (row.compliance_status === 'manual_review') {
    return '手工核查'
  }
  return row.is_compliant ? '合规' : '不合规'
}

const getComplianceStatusTag = (row) => {
  if (row.compliance_status === 'manual_review') {
    return 'info'
  }
  return row.is_compliant ? 'success' : 'danger'
}

const getTaskStatusLabel = (status) => {
  const labels = {
    pending: '等待',
    running: '运行中',
    completed: '完成',
    failed: '失败',
    success: '成功',
    installing: '安装中',
    downloading: '下载中'
  }
  return labels[status] || status
}

const getTaskStatusTag = (status) => {
  const tags = {
    pending: 'info',
    running: 'primary',
    completed: 'success',
    failed: 'danger',
    success: 'success',
    installing: 'warning',
    downloading: 'primary'
  }
  return tags[status] || 'info'
}

const getTaskProgressStatus = (status) => {
  if (status === 'completed' || status === 'success') {
    return 'success'
  }
  if (status === 'failed') {
    return 'exception'
  }
  return undefined
}

const openTaskDetail = async (task) => {
  showTaskDetailDialog.value = true
  selectedTask.value = task
  selectedTaskResults.value = []
  taskDetailLoading.value = true
  try {
    const res = await getSoftwareTaskDetail(task.id)
    selectedTask.value = res
    selectedTaskResults.value = res.results || []
  } catch (error) {
    console.error('加载扫描任务详情失败', error)
    showTaskDetailDialog.value = false
    selectedTaskResults.value = []
  } finally {
    taskDetailLoading.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadGroups(), loadChecks(), loadResults(), loadStats(), loadScanTasks()])
})
</script>

<style scoped>
.compliance-management {
  padding: 0;
}

.header-card,
.table-card,
.chart-card {
  margin-bottom: 20px;
}

.header,
.card-header,
.header-actions,
.filter-bar,
.row-actions,
.quick-filter-group {
  display: flex;
  align-items: center;
}

.header,
.card-header {
  justify-content: space-between;
  gap: 16px;
}

.header-actions,
.filter-bar,
.row-actions,
.quick-filter-group {
  gap: 12px;
  flex-wrap: wrap;
}

.sub-title {
  margin: 6px 0 0;
  color: #909399;
  font-size: 14px;
}

.summary-row,
.charts-row {
  margin-bottom: 20px;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.summary-label {
  color: #909399;
  font-size: 13px;
}

.summary-value {
  font-size: 28px;
  font-weight: 600;
  color: #303133;
}

.summary-value.success {
  color: #67c23a;
}

.summary-value.danger {
  color: #f56c6c;
}

.summary-meta {
  color: #909399;
  font-size: 12px;
}

.chart {
  height: 300px;
}

.muted-line {
  margin-top: 4px;
  color: #909399;
  font-size: 12px;
}

.muted-text,
.quick-filter-label {
  color: #909399;
  font-size: 12px;
}

.task-summary {
  font-size: 12px;
  line-height: 1.7;
  color: #606266;
}

.task-detail-descriptions {
  margin-bottom: 8px;
}

.pagination {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 768px) {
  .header,
  .card-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .filter-bar,
  .header-actions {
    width: 100%;
  }

  .chart {
    height: 260px;
  }
}
</style>
