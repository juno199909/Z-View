<template>
  <div class="zv-sec-page">
    <div class="zv-sec-header">
      <h2 class="zv-sec-title">策略中心</h2>
      <el-button :icon="Refresh" plain @click="loadData">刷新</el-button>
    </div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
      统一安全策略管理。在「防火墙 / USB管控」各模块页面通过结构化表单创建并下发策略；本页面用于管理已有策略：启用/禁用、绑定范围、版本回滚、查看执行结果、删除。
    </el-alert>

    <div class="zv-pol-filter">
      <el-select v-model="filters.policy_type" placeholder="策略类型" clearable style="width:140px" @change="loadData">
        <el-option v-for="t in types" :key="t.v" :label="t.l" :value="t.v" />
      </el-select>
      <el-select v-model="filters.enabled" placeholder="状态" clearable style="width:120px" @change="loadData">
        <el-option label="启用" :value="true" /><el-option label="禁用" :value="false" />
      </el-select>
    </div>

    <el-table :data="policies" stripe v-loading="loading" style="margin-top:12px">
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="policy_name" label="策略名" min-width="150" />
      <el-table-column prop="policy_type" label="类型" width="110">
        <template #default="{row}"><el-tag size="small">{{ typeLabel(row.policy_type) }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="enabled" label="状态" width="90">
        <template #default="{row}">
          <el-switch :model-value="row.enabled" @change="val=>toggleEnabled(row,val)" :loading="row._toggling" />
        </template>
      </el-table-column>
      <el-table-column prop="priority" label="优先级" width="80" />
      <el-table-column prop="version" label="版本" width="70"><template #default="{row}">v{{row.version}}</template></el-table-column>
      <el-table-column prop="binding_count" label="绑定数" width="80" />
      <el-table-column prop="updated_at" label="更新时间" width="160" />
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{row}">
          <el-button link type="primary" @click="showBind(row)">绑定</el-button>
          <el-button link type="success" @click="showExecResults(row)">执行结果</el-button>
          <el-button link type="warning" @click="showVersions(row)">版本/回滚</el-button>
          <el-button link type="danger" @click="delPolicy(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="bindVisible" title="策略绑定" width="480px">
      <p>策略: {{ bindTarget?.policy_name }}（{{ typeLabel(bindTarget?.policy_type) }}）</p>
      <el-form :model="bindForm" label-width="100px" style="margin-top:12px">
        <el-form-item label="绑定范围">
          <el-radio-group v-model="bindForm.scope_type">
            <el-radio value="global">全局（所有终端）</el-radio>
            <el-radio value="group">终端组</el-radio>
            <el-radio value="asset">指定终端</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="终端组" v-if="bindForm.scope_type==='group'">
          <el-select v-model="bindForm.group_id" filterable placeholder="选择终端组" style="width:100%">
            <el-option v-for="g in groups" :key="g.value" :label="g.label" :value="g.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="终端" v-if="bindForm.scope_type==='asset'">
          <el-select v-model="bindForm.asset_ids" multiple filterable placeholder="选择终端（可多选）" style="width:100%">
            <el-option v-for="a in assets" :key="a.value" :label="a.label" :value="a.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="bindVisible=false">取消</el-button><el-button type="primary" @click="doBind">绑定</el-button></template>
    </el-dialog>

    <el-dialog v-model="execVisible" title="策略执行结果" width="760px">
      <p>策略: {{ execTarget?.policy_name }}</p>
      <el-table :data="execResults" stripe size="small" style="margin-top:8px" v-loading="execLoading">
        <el-table-column prop="hostname" label="终端" min-width="120" />
        <el-table-column prop="status" label="状态" width="90"><template #default="{row}"><el-tag :type="row.status==='success'?'success':row.status==='partial'?'warning':'danger'" size="small">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
        <el-table-column prop="applied_rules" label="成功" width="60" />
        <el-table-column prop="failed_rules" label="失败" width="60" />
        <el-table-column prop="error_detail" label="错误详情" min-width="220" show-overflow-tooltip />
        <el-table-column prop="executed_at" label="执行时间" width="160" />
      </el-table>
      <template #footer><el-button @click="execVisible=false">关闭</el-button></template>
    </el-dialog>

    <el-dialog v-model="versionVisible" title="策略版本历史" width="640px">
      <p>策略: {{ versionTarget?.policy_name }}（当前 v{{ versionTarget?.version }}）</p>
      <el-table :data="versions" stripe size="small" style="margin-top:8px" v-loading="versionLoading">
        <el-table-column prop="version" label="版本" width="70"><template #default="{row}">v{{row.version}}</template></el-table-column>
        <el-table-column prop="change_note" label="变更说明" min-width="160" />
        <el-table-column prop="changed_by" label="变更人" width="110" />
        <el-table-column prop="created_at" label="时间" width="160" />
        <el-table-column label="操作" width="90"><template #default="{row}"><el-button link type="warning" :disabled="row.version===versionTarget?.version" @click="doRollback(row)">回滚到此版本</el-button></template></el-table-column>
      </el-table>
      <template #footer><el-button @click="versionVisible=false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { getSecurityPolicies, updateSecurityPolicy, bindSecurityPolicy, deleteSecurityPolicy, getSecurityPolicyVersions, getSecurityPolicyExecResults, rollbackSecurityPolicy } from '@/api/security'
import { useAssetGroupOptions } from '@/composables/useAssetGroupOptions'
const types = [{v:'firewall',l:'防火墙'},{v:'usb',l:'USB管控'}]
const typeLabel = (v)=>({firewall:'防火墙',usb:'USB管控'})[v]||v
const statusLabel = (v)=>({success:'成功',failed:'失败',partial:'部分成功',pending:'待执行'})[v]||v
const loading = ref(false); const policies = ref([])
const filters = reactive({policy_type:'',enabled:''})
const bindVisible = ref(false); const bindTarget = ref(null); const bindForm = reactive({scope_type:'global',group_id:null,asset_ids:[]})
const execVisible = ref(false); const execTarget = ref(null); const execResults = ref([]); const execLoading = ref(false)
const versionVisible = ref(false); const versionTarget = ref(null); const versions = ref([]); const versionLoading = ref(false)
const { groups, assets, loadOptions } = useAssetGroupOptions()
const loadData = async () => { loading.value=true; try{const r=await getSecurityPolicies({page:1,page_size:200,policy_type:filters.policy_type||undefined,enabled:filters.enabled===''?undefined:filters.enabled}); policies.value=(r.data||[]).map(p=>({...p,_toggling:false}))}catch(e){ElMessage.error('加载策略失败')}finally{loading.value=false} }
const toggleEnabled = async (row, val) => { row._toggling=true; try{ await updateSecurityPolicy(row.id, {enabled:val}); row.enabled=val; ElMessage.success(val?'已启用':'已禁用') }catch(e){ElMessage.error('操作失败')}finally{row._toggling=false} }
const showBind = (row) => { bindTarget.value=row; bindForm.scope_type='global'; bindForm.group_id=null; bindForm.asset_ids=[]; loadOptions(); bindVisible.value=true }
const doBind = async () => {
  try {
    if (bindForm.scope_type==='asset') {
      for (const id of (bindForm.asset_ids||[])) await bindSecurityPolicy(bindTarget.value.id, {scope_type:'asset',scope_id:id})
    } else {
      await bindSecurityPolicy(bindTarget.value.id, {scope_type:bindForm.scope_type, scope_id: bindForm.scope_type==='group'?bindForm.group_id:null})
    }
    ElMessage.success('绑定成功'); bindVisible.value=false; loadData()
  } catch(e){ ElMessage.error('绑定失败') }
}
const showExecResults = async (row) => { execTarget.value=row; execVisible.value=true; execLoading.value=true; try{const r=await getSecurityPolicyExecResults(row.id,{page:1,page_size:50}); execResults.value=r.data||[]}catch(e){ElMessage.error('加载执行结果失败')}finally{execLoading.value=false} }
const showVersions = async (row) => { versionTarget.value=row; versionVisible.value=true; versionLoading.value=true; try{const r=await getSecurityPolicyVersions(row.id); versions.value=r.data||[]}catch(e){ElMessage.error('加载版本失败')}finally{versionLoading.value=false} }
const doRollback = async (row) => { try{ await ElMessageBox.confirm(`确定回滚到 v${row.version}？当前配置将被替换。`,'版本回滚',{type:'warning'}); await rollbackSecurityPolicy(versionTarget.value.id, {version:row.version}); ElMessage.success('已回滚'); versionVisible.value=false; loadData() }catch(e){ if(e!=='cancel')ElMessage.error('回滚失败') } }
const delPolicy = async (row) => { try{await ElMessageBox.confirm(`确定删除策略"${row.policy_name}"？`,'删除',{type:'warning'}); await deleteSecurityPolicy(row.id); ElMessage.success('已删除'); loadData()}catch(e){} }
onMounted(loadData)
</script>
<style scoped lang="scss">
.zv-sec-page { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
.zv-pol-filter { display: flex; gap: 10px; }
</style>