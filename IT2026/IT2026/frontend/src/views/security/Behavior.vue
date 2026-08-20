<template>
  <div class="zv-sec-page">
    <div class="zv-sec-header"><h2 class="zv-sec-title">行为监控</h2><el-button :icon="Refresh" plain @click="loadData">刷新</el-button></div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px">终端行为事件流：进程创建、注册表修改、服务创建、启动项变化、PowerShell/脚本执行、网络连接、可疑行为。</el-alert>
    <el-table :data="events" stripe v-loading="loading" style="margin-top:8px">
      <el-table-column prop="hostname" label="终端" min-width="110" />
      <el-table-column prop="event_type" label="类型" width="130"><template #default="{row}"><el-tag size="small">{{typeLabel(row.event_type)}}</el-tag></template></el-table-column>
      <el-table-column prop="severity" label="级别" width="80"><template #default="{row}"><el-tag :type="sevTag(row.severity)" size="small" effect="dark">{{row.severity}}</el-tag></template></el-table-column>
      <el-table-column prop="title" label="标题" min-width="200" show-overflow-tooltip />
      <el-table-column prop="process_name" label="进程" min-width="120" />
      <el-table-column prop="occurred_at" label="时间" width="160" />
    </el-table>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getBehaviorEvents } from '@/api/security'
const loading = ref(false); const events = ref([])
const typeLabel = (v) => ({suspicious_process:'可疑进程',registry_anomaly:'注册表异常',network_anomaly:'网络异常',file_anomaly:'文件异常',virus:'病毒/木马'})[v]||v
const sevTag = (v) => ({critical:'danger',high:'warning',medium:'primary',low:'success',info:'info'})[v]||'info'
const loadData = async () => { loading.value=true; try{const r=await getBehaviorEvents({page:1,page_size:50}); events.value=r.data||[]}catch(e){ElMessage.error('加载行为事件失败')}finally{loading.value=false} }
onMounted(loadData)
</script>
<style scoped lang="scss">
.zv-sec-page { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
</style>