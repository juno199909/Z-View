<template>
  <div class="zv-sec-page">
    <div class="zv-sec-header">
      <h2 class="zv-sec-title">程序管控</h2>
      <div><el-button type="primary" @click="openPolicy">下发程序管控策略</el-button><el-button :icon="Refresh" plain @click="loadData" style="margin-left:8px">刷新</el-button></div>
    </div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
      程序执行控制：应用黑名单/白名单、未知程序执行告警、进程启动日志。下发后 Agent 立即扫描命中黑名单的进程并告警。
    </el-alert>

    <el-table :data="logs" stripe v-loading="loading" style="margin-top:8px">
      <el-table-column prop="hostname" label="终端" min-width="110" />
      <el-table-column prop="process_name" label="进程名" min-width="140" />
      <el-table-column prop="pid" label="PID" width="80" />
      <el-table-column prop="path" label="可执行文件路径" min-width="240" show-overflow-tooltip />
      <el-table-column prop="user" label="用户" width="100" />
      <el-table-column prop="matched_policy" label="命中策略" min-width="130"><template #default="{row}"><span v-if="row.matched_policy"><el-tag type="danger" size="small">{{ row.matched_policy }}</el-tag></span><span v-else>-</span></template></el-table-column>
      <el-table-column prop="action" label="处置" width="90"><template #default="{row}"><el-tag :type="row.action==='blocked'?'danger':row.action==='alerted'?'warning':'success'" size="small">{{ actLabel(row.action) }}</el-tag></template></el-table-column>
      <el-table-column prop="launched_at" label="启动时间" width="160" />
    </el-table>

    <el-dialog v-model="policyVisible" title="下发程序管控策略" width="560px">
      <el-form :model="form" label-width="120px">
        <el-form-item label="管控模式">
          <el-radio-group v-model="form.mode">
            <el-radio value="blacklist">黑名单（禁止指定程序）</el-radio>
            <el-radio value="whitelist">白名单（仅允许指定程序）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="form.mode==='blacklist'?'黑名单程序':'白名单程序'">
          <el-select v-model="form.list" multiple filterable allow-create default-first-option placeholder="输入程序名，如 cmd.exe / powershell.exe，回车添加" style="width:100%">
            <el-option v-for="p in suggestList" :key="p" :label="p" :value="p" />
          </el-select>
          <div class="zv-hint">{{ form.mode==='blacklist'?'命中的进程将告警并可远程结束':'未在白名单内的程序运行时告警' }}</div>
        </el-form-item>
        <el-form-item label="未知程序告警"><el-switch v-model="form.alert_unknown" /></el-form-item>
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
      <template #footer><el-button @click="policyVisible=false">取消</el-button><el-button type="primary" :loading="applying" @click="doApply">立即下发</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getProcessLaunchLogs, applyAppControlPolicy } from '@/api/security'
import { useAssetGroupOptions } from '@/composables/useAssetGroupOptions'
const loading = ref(false); const logs = ref([])
const suggestList = ['cmd.exe','powershell.exe','regedit.exe','vssadmin.exe','wmic.exe','certutil.exe','bitsadmin.exe','mshta.exe']
const actLabel = (v) => ({ allowed:'允许', blocked:'已阻止', alerted:'告警' })[v] || v
const policyVisible = ref(false); const applying = ref(false)
const { groups, assets, loadOptions } = useAssetGroupOptions()
const form = reactive({ mode:'blacklist', list:[], alert_unknown:true, scope_type:'global', group_id:'', asset_ids: [] })
const loadData = async () => { loading.value=true; try { const r=await getProcessLaunchLogs({page:1,page_size:50}); logs.value=r.data||[] } catch(e){ElMessage.error('加载进程日志失败')} finally{loading.value=false} }
const openPolicy = () => { form.mode='blacklist'; form.list=[]; form.alert_unknown=true; form.scope_type='global'; form.group_id=''; form.asset_ids=[]; policyVisible.value=true }
const doApply = async () => {
  if (!form.list.length) { ElMessage.warning('请至少添加一个程序'); return }
  applying.value=true
  try {
    const target = { scope_type: form.scope_type, alert_unknown: form.alert_unknown }
    if (form.scope_type==='group') target.group_id = Number(form.group_id)
    if (form.scope_type==='asset') target.asset_ids = [...form.asset_ids].map(Number).filter(Boolean)
    if (form.mode==='blacklist') { target.blacklist = form.list; target.whitelist = [] } else { target.whitelist = form.list; target.blacklist = [] }
    const r = await applyAppControlPolicy(target)
    const ok = (r.dispatch_results||[]).filter(x=>x.success).length
    ElMessage.success(`程序管控策略已下发（${ok}/${r.targets||0} 台执行成功）`)
    policyVisible.value=false; loadData()
  } catch(e){ ElMessage.error('下发失败') } finally { applying.value=false }
}
onMounted(() => { loadData(); loadOptions() })
</script>
<style scoped lang="scss">
.zv-sec-page { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
.zv-hint { font-size: 12px; color: #909399; margin-top: 4px; }
</style>