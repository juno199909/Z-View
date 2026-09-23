<template>
  <div class="zv-sec-page">
    <div class="zv-sec-header">
      <h2 class="zv-sec-title">USB管控</h2>
      <div><el-button type="primary" @click="openPolicy">下发USB策略</el-button><el-button :icon="Refresh" plain @click="loadData" style="margin-left:8px">刷新</el-button></div>
    </div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">
      企业USB设备管理：识别USB存储设备、插拔审计、按终端/组下发禁用/允许策略。仅管控USB存储类（U盘/移动硬盘），USB键盘鼠标、MTP手机不受影响。
    </el-alert>

    <div class="zv-usb-status">
      <div class="zv-usb-stat"><div class="zv-usb-num">{{ devices.length }}</div><div class="zv-usb-lbl">已识别设备</div></div>
      <div class="zv-usb-stat"><div class="zv-usb-num">{{ blockedCount }}</div><div class="zv-usb-lbl">已阻止</div></div>
      <div class="zv-usb-stat"><div class="zv-usb-num">{{ events.length }}</div><div class="zv-usb-lbl">插拔事件</div></div>
    </div>

    <el-tabs v-model="activeTab">
      <el-tab-pane label="设备台账" name="devices">
        <el-table :data="devices" stripe v-loading="loading" style="margin-top:8px">
          <el-table-column prop="hostname" label="终端" min-width="120" />
          <el-table-column prop="vid_pid" label="VID/PID" width="130" />
          <el-table-column prop="friendly_name" label="设备名称" min-width="170" show-overflow-tooltip />
          <el-table-column prop="device_class" label="设备类" width="110"><template #default="{row}">{{ classLabel(row.device_class) }}</template></el-table-column>
          <el-table-column prop="serial_number" label="序列号" min-width="140" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="90"><template #default="{row}"><el-tag :type="row.status==='allowed'?'success':row.status==='blocked'?'danger':'info'" size="small">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column prop="last_seen" label="最后发现" width="160" />
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="插拔日志" name="events">
        <el-table :data="events" stripe v-loading="loading" style="margin-top:8px">
          <el-table-column prop="hostname" label="终端" min-width="120" />
          <el-table-column prop="event_type" label="事件" width="100"><template #default="{row}"><el-tag :type="row.event_type==='insert'?'success':row.event_type==='blocked'?'danger':row.event_type==='allowed'?'success':'info'" size="small">{{ evLabel(row.event_type) }}</el-tag></template></el-table-column>
          <el-table-column prop="vid_pid" label="VID/PID" width="130" />
          <el-table-column prop="friendly_name" label="设备名称" min-width="170" show-overflow-tooltip />
          <el-table-column prop="device_class" label="设备类" width="110"><template #default="{row}">{{ classLabel(row.device_class) }}</template></el-table-column>
          <el-table-column prop="occurred_at" label="时间" width="160" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <el-dialog v-model="policyVisible" title="下发USB管控策略" width="520px">
      <el-form :model="form" label-width="120px">
        <el-form-item label="管控动作">
          <el-radio-group v-model="form.action">
            <el-radio value="block">禁止USB存储设备</el-radio>
            <el-radio value="allow">允许USB存储设备</el-radio>
          </el-radio-group>
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
        <el-form-item label="设备白名单" v-if="form.action==='block'">
          <el-input v-model="form.device_whitelist_str" type="textarea" :rows="2" placeholder="可选：放行的VID/PID，每行一个，如 VID_0951&PID_1666" />
          <div class="zv-hint">留空表示全部USB存储禁用；填写则仅放行指定设备</div>
        </el-form-item>
      </el-form>
      <div class="zv-hint" style="margin-top:8px;color:#e6a23c">
        说明：禁用通过修改注册表 USBSTOR 实现，对USB键盘鼠标、MTP手机等非存储类设备无影响。禁用后已插入的存储设备需重新插拔生效。
      </div>
      <template #footer><el-button @click="policyVisible=false">取消</el-button><el-button type="primary" :loading="applying" @click="doApply">立即下发</el-button></template>
    </el-dialog>
  </div>
</template>
<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getUsbDevices, getUsbEvents, applyUsbPolicy } from '@/api/security'
import { useAssetGroupOptions } from '@/composables/useAssetGroupOptions'
const activeTab = ref('devices'); const loading = ref(false)
const devices = ref([]); const events = ref([])
const blockedCount = computed(() => devices.value.filter(d => d.status === 'blocked').length)
const policyVisible = ref(false); const applying = ref(false)
const { groups, assets, loadOptions } = useAssetGroupOptions()
const form = reactive({ action: 'block', scope_type: 'global', group_id: '', asset_ids: [], device_whitelist_str: '' })
const classLabel = (v) => ({ USBStorage: 'U盘/存储', HID: '键鼠', Net: '网卡', MTP: '手机/MTP', Image: '图像设备' })[v] || v || '未知'
const statusLabel = (v) => ({ allowed: '允许', blocked: '已阻止', unknown: '未知' })[v] || v
const evLabel = (v) => ({ insert: '插入', remove: '拔出', blocked: '已阻止', allowed: '已允许' })[v] || v
const loadDevices = async () => { loading.value=true; try { const r=await getUsbDevices({page:1,page_size:50}); devices.value=r.data||[] } catch(e){ElMessage.error('加载USB设备失败')} finally{loading.value=false} }
const loadEvents = async () => { loading.value=true; try { const r=await getUsbEvents({page:1,page_size:50}); events.value=r.data||[] } catch(e){ElMessage.error('加载USB日志失败')} finally{loading.value=false} }
const loadData = () => activeTab.value==='devices'?loadDevices():loadEvents()
const openPolicy = () => { form.action='block'; form.scope_type='global'; form.group_id=''; form.asset_ids=[]; form.device_whitelist_str=''; policyVisible.value=true }
const doApply = async () => {
  applying.value=true
  try {
    const target = { scope_type: form.scope_type, action: form.action }
    if (form.scope_type==='group') target.group_id = Number(form.group_id)
    if (form.scope_type==='asset') target.asset_ids = [...form.asset_ids].map(Number).filter(Boolean)
    target.device_whitelist = form.device_whitelist_str.split('\n').map(s=>s.trim()).filter(Boolean)
    const r = await applyUsbPolicy(target)
    const ok = (r.dispatch_results||[]).filter(x=>x.success).length
    ElMessage.success(`USB策略已下发（${ok}/${r.targets||0} 台执行成功）`)
    policyVisible.value=false; loadData()
  } catch(e){ ElMessage.error('下发失败') } finally { applying.value=false }
}
watch(activeTab, loadData); onMounted(() => { loadDevices(); loadOptions() })
</script>
<style scoped lang="scss">
.zv-sec-page { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
.zv-usb-status { display: flex; gap: 16px; margin-bottom: 16px; }
.zv-usb-stat { flex: 1; text-align: center; padding: 14px; background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
.zv-usb-num { font-size: 26px; font-weight: 700; color: #409eff; }
.zv-usb-lbl { font-size: 13px; color: #909399; margin-top: 4px; }
.zv-hint { font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.5; }
</style>