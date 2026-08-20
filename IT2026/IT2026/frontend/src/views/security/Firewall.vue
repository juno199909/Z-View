<template>
  <div class="zv-sec-page">
    <div class="zv-sec-header"><h2 class="zv-sec-title">防火墙管理</h2>
      <el-button :icon="Refresh" plain @click="loadData">刷新</el-button></div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
      通过 Agent 执行 Windows 防火墙规则下发（netsh advfirewall），支持按终端/组批量下发入站出站规则，下发后立即生效并回传执行结果。
    </el-alert>

    <div class="zv-fw-top">
      <div class="zv-fw-stat"><div class="zv-fw-num">{{ ruleList.length }}</div><div class="zv-fw-lbl">已下发规则策略</div></div>
      <el-button type="primary" @click="showApply = true">下发防火墙策略</el-button>
    </div>

    <el-table :data="ruleList" stripe v-loading="loading" style="margin-top:12px">
      <el-table-column prop="id" label="策略ID" width="80" />
      <el-table-column prop="policy_name" label="策略名" min-width="140" />
      <el-table-column prop="enabled" label="状态" width="80"><template #default="{row}"><el-tag :type="row.enabled?'success':'info'" size="small">{{ row.enabled?'启用':'禁用' }}</el-tag></template></el-table-column>
      <el-table-column label="规则数" width="80"><template #default="{row}">{{ (row.config&&row.config.rules)?row.config.rules.length:0 }}</template></el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="160" />
    </el-table>

    <el-dialog v-model="showApply" title="下发防火墙策略" width="640px">
      <el-form :model="form" label-width="100px">
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
        <el-divider>防火墙规则</el-divider>
        <div v-for="(rule, i) in form.rules" :key="i" class="fw-rule-row">
          <el-input v-model="rule.name" placeholder="规则名称" style="width:140px" />
          <el-select v-model="rule.direction" style="width:100px"><el-option label="入站" value="in" /><el-option label="出站" value="out" /></el-select>
          <el-select v-model="rule.action" style="width:100px"><el-option label="允许" value="allow" /><el-option label="拒绝" value="block" /></el-select>
          <el-select v-model="rule.protocol" style="width:90px"><el-option label="TCP" value="TCP" /><el-option label="UDP" value="UDP" /><el-option label="任何" value="any" /></el-select>
          <el-input v-model="rule.local_port" placeholder="本地端口" style="width:110px" />
          <el-input v-model="rule.remote_ip" placeholder="远程IP/段" style="width:140px" />
          <el-button link type="danger" @click="form.rules.splice(i,1)">删除</el-button>
        </div>
        <el-button @click="form.rules.push({name:'',direction:'in',action:'allow',protocol:'TCP',local_port:'',remote_ip:''})" style="margin-top:8px">+ 添加规则</el-button>
      </el-form>
      <template #footer><el-button @click="showApply=false">取消</el-button><el-button type="primary" :loading="applying" @click="doApply">下发</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { applyFirewallPolicy, getFirewallRules } from '@/api/security'
import { useAssetGroupOptions } from '@/composables/useAssetGroupOptions'
const showApply = ref(false); const applying = ref(false); const loading = ref(false)
const ruleList = ref([])
const { groups, assets, loadOptions } = useAssetGroupOptions()
const form = reactive({ scope_type: 'global', group_id: '', asset_ids: [], rules: [] })
const loadData = async () => { loading.value=true; try { const r=await getFirewallRules(); ruleList.value=r.data||[] } catch(e){ElMessage.error('加载防火墙策略失败')} finally{loading.value=false} }
const doApply = async () => {
  if (!form.rules.length) { ElMessage.warning('请至少添加一条规则'); return }
  // 校验规则
  for (const r of form.rules) { if (!r.name) { ElMessage.warning('规则名称不能为空'); return } }
  applying.value = true
  try {
    const target = { scope_type: form.scope_type }
    if (form.scope_type === 'group') target.group_id = Number(form.group_id)
    if (form.scope_type === 'asset') target.asset_ids = [...form.asset_ids].map(Number).filter(Boolean)
    const r = await applyFirewallPolicy({ ...target, rules: form.rules })
    const ok = (r.dispatch_results||[]).filter(x=>x.success).length
    const fail = (r.dispatch_results||[]).reduce((s,x)=>s+(x.failed||0),0)
    ElMessage.success(`防火墙策略已下发（${ok}/${r.targets||0} 台成功，共 ${r.rule_count} 条规则${fail?`，失败 ${fail} 条`:''}）`)
    showApply.value = false; loadData()
  } catch (e) { ElMessage.error('下发失败') } finally { applying.value = false }
}
onMounted(() => { loadData(); loadOptions() })
</script>
<style scoped lang="scss">
.zv-sec-page { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
.fw-rule-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; flex-wrap: wrap; }
.zv-fw-top { display: flex; justify-content: space-between; align-items: center; }
.zv-fw-stat { background:#fff; border-radius:8px; padding:12px 20px; box-shadow:0 1px 3px rgba(0,0,0,0.05); }
.zv-fw-num { font-size:24px; font-weight:700; color:#409eff; }
.zv-fw-lbl { font-size:12px; color:#909399; margin-top:2px; }
</style>