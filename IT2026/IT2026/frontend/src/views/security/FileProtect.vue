<template>
  <div class="zv-sec-page">
    <div class="zv-sec-header">
      <h2 class="zv-sec-title">文件保护</h2>
      <div><el-button type="primary" @click="openPolicy">下发文件保护策略</el-button><el-button :icon="Refresh" plain @click="loadData" style="margin-left:8px">刷新</el-button></div>
    </div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
      重要目录保护：建立文件哈希基线，定时比对检测异常修改/创建/删除，批量变更告警（防勒索病毒大量加密）。下发后 Agent 立即对保护目录建立基线。
    </el-alert>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="保护目录基线" name="baselines">
        <el-table :data="baselines" stripe v-loading="loading" style="margin-top:8px">
          <el-table-column prop="hostname" label="终端" min-width="110" />
          <el-table-column prop="dir_path" label="保护目录" min-width="180" />
          <el-table-column prop="file_path" label="文件" min-width="240" show-overflow-tooltip />
          <el-table-column prop="file_size" label="大小" width="100" />
          <el-table-column prop="md5" label="MD5" width="260" />
          <el-table-column prop="baseline_at" label="基线时间" width="160" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="文件异常事件" name="anomalies">
        <el-table :data="anomalies" stripe v-loading="loading" style="margin-top:8px">
          <el-table-column prop="hostname" label="终端" min-width="110" />
          <el-table-column prop="dir_path" label="保护目录" min-width="160" />
          <el-table-column prop="file_path" label="文件" min-width="240" show-overflow-tooltip />
          <el-table-column prop="anomaly_type" label="异常类型" width="120"><template #default="{row}"><el-tag :type="row.anomaly_type==='mass_change'?'danger':'warning'" size="small">{{ anomLabel(row.anomaly_type) }}</el-tag></template></el-table-column>
          <el-table-column prop="process_name" label="触发进程" min-width="130" />
          <el-table-column prop="occurred_at" label="时间" width="160" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="policyVisible" title="下发文件保护策略" width="560px">
      <el-form :model="form" label-width="130px">
        <el-form-item label="保护目录">
          <el-select v-model="form.protected_dirs" multiple filterable allow-create default-first-option placeholder="输入目录路径，如 D:\财务资料，回车添加" style="width:100%">
            <el-option v-for="d in suggestDirs" :key="d" :label="d" :value="d" />
          </el-select>
          <div class="zv-hint">将对这些目录建立文件哈希基线并持续监控异常变更</div>
        </el-form-item>
        <el-form-item label="批量变更阈值">
          <el-input-number v-model="form.mass_change_threshold" :min="5" :max="5000" />
          <div class="zv-hint">短时间内超过此数量的文件变更视为勒索病毒行为，触发高危告警</div>
        </el-form-item>
        <el-form-item label="目标范围">
          <el-radio-group v-model="form.scope_type">
            <el-radio value="global">全局</el-radio><el-radio value="group">终端组</el-radio><el-radio value="asset">指定终端</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="终端组" v-if="form.scope_type==='group'">
          <el-select v-model="form.group_id" filterable placeholder="选择终端组" style="width:100%">
            <el-option v-for="g in groups" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="终端" v-if="form.scope_type==='asset'">
          <el-select v-model="form.asset_ids" multiple filterable placeholder="选择终端（可多选）" style="width:100%">
            <el-option v-for="a in assets" :key="a.value" :label="a.label" :value="a.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="policyVisible=false">取消</el-button><el-button type="primary" :loading="applying" @click="doApply">立即下发并建立基线</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getFileProtectBaselines, getFileAnomalies, applyFileProtectPolicy } from '@/api/security'
import { useAssetGroupOptions } from '@/composables/useAssetGroupOptions'
const activeTab = ref('baselines'); const loading = ref(false)
const baselines = ref([]); const anomalies = ref([])
const suggestDirs = ['D:\\共享文件','D:\\财务资料','D:\\业务数据','C:\\重要配置']
const anomLabel = (v) => ({ modified:'修改', created:'新建', deleted:'删除', renamed:'重命名', mass_change:'批量变更(疑似勒索)' })[v] || v
const policyVisible = ref(false); const applying = ref(false)
const { groups, assets, loadOptions } = useAssetGroupOptions()
const form = reactive({ protected_dirs:[], mass_change_threshold:50, scope_type:'global', group_id:'', asset_ids: [] })
const loadBaselines = async () => { loading.value=true; try{const r=await getFileProtectBaselines({page:1,page_size:50}); baselines.value=r.data||[]}catch(e){ElMessage.error('加载基线失败')}finally{loading.value=false} }
const loadAnomalies = async () => { loading.value=true; try{const r=await getFileAnomalies({page:1,page_size:50}); anomalies.value=r.data||[]}catch(e){ElMessage.error('加载异常失败')}finally{loading.value=false} }
const loadData = () => activeTab.value==='baselines'?loadBaselines():loadAnomalies()
const openPolicy = () => { form.protected_dirs=[]; form.mass_change_threshold=50; form.scope_type='global'; form.group_id=''; form.asset_ids=[]; policyVisible.value=true }
const doApply = async () => {
  if (!form.protected_dirs.length) { ElMessage.warning('请至少添加一个保护目录'); return }
  applying.value=true
  try {
    const target = { scope_type: form.scope_type, protected_dirs: form.protected_dirs, mass_change_threshold: form.mass_change_threshold }
    if (form.scope_type==='group') target.group_id = Number(form.group_id)
    if (form.scope_type==='asset') target.asset_ids = [...form.asset_ids].map(Number).filter(Boolean)
    const r = await applyFileProtectPolicy(target)
    const ok = (r.dispatch_results||[]).filter(x=>x.dirs && x.dirs.some(d=>d.success)).length
    ElMessage.success(`文件保护策略已下发（${ok}/${r.targets||0} 台建立基线成功）`)
    policyVisible.value=false; loadData()
  } catch(e){ ElMessage.error('下发失败') } finally { applying.value=false }
}
watch(activeTab, loadData); onMounted(() => { loadBaselines(); loadOptions() })
</script>
<style scoped lang="scss">
.zv-sec-page { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
.zv-hint { font-size: 12px; color: #909399; margin-top: 4px; }
</style>