<template>
  <div>
    <div class="zv-embed-toolbar">
      <span class="zv-embed-desc">
        超过留存期的数据每 6 小时自动清理一次，也可点击"立即清理"即时生效；心跳明细建议不低于 7 天。
      </span>
      <div class="zv-embed-actions">
        <el-button :icon="Refresh" @click="loadData" :loading="loading">刷新</el-button>
        <el-button type="warning" :icon="Delete" @click="runNow" :loading="runLoading">立即清理</el-button>
        <el-button type="primary" :icon="Check" @click="save" :loading="saving">保存配置</el-button>
      </div>
    </div>

    <div class="zv-card zv-card-pad">
      <el-table :data="tables" v-loading="loading" stripe>
        <el-table-column prop="label" label="日志类型" min-width="160" />
        <el-table-column prop="table" label="数据表" min-width="200" show-overflow-tooltip />
        <el-table-column label="留存天数" width="160" align="center">
          <template #default="{ row }">
            <el-input-number v-model="daysForm[row.table]" :min="1" :max="3650" size="small" controls-position="right" />
          </template>
        </el-table-column>
        <el-table-column label="当前行数" width="120" align="right">
          <template #default="{ row }">{{ formatRows(row.rows) }}</template>
        </el-table-column>
        <el-table-column label="最早数据" width="180">
          <template #default="{ row }">{{ row.oldest || '暂无数据' }}</template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="resultVisible" title="清理结果" width="420px">
      <el-table :data="runResult" size="small">
        <el-table-column prop="label" label="日志类型" />
        <el-table-column prop="deleted" label="本次删除行数" align="right" />
      </el-table>
      <template #footer>
        <el-button type="primary" @click="resultVisible = false">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, Delete, Refresh } from '@element-plus/icons-vue'
import { getLogRetention, runLogRetentionNow, updateLogRetention } from '@/api/monitoring'

const loading = ref(false)
const saving = ref(false)
const runLoading = ref(false)
const tables = ref([])
const daysForm = ref({})
const resultVisible = ref(false)
const runResult = ref([])

const formatRows = (n) => (n === null || n === undefined ? '-' : Number(n).toLocaleString())

const loadData = async () => {
  loading.value = true
  try {
    const res = await getLogRetention()
    tables.value = res.tables || []
    const form = {}
    for (const t of tables.value) form[t.table] = t.days
    daysForm.value = form
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const res = await updateLogRetention(daysForm.value)
    ElMessage.success(res.message || '配置已保存')
    loadData()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    saving.value = false
  }
}

const runNow = async () => {
  runLoading.value = true
  try {
    const res = await runLogRetentionNow()
    const deleted = res.deleted || {}
    runResult.value = (tables.value || []).map((t) => ({
      label: t.label,
      deleted: typeof deleted[t.table] === 'number' ? Number(deleted[t.table]).toLocaleString() : String(deleted[t.table] ?? '-'),
    }))
    resultVisible.value = true
    loadData()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    runLoading.value = false
  }
}

onMounted(loadData)
</script>

<style scoped>
.zv-embed-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.zv-embed-desc {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.8;
}
</style>
