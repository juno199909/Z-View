<template>
  <div class="zv-sec-terminals">
    <div class="zv-sec-header">
      <h2 class="zv-sec-title">终端安全</h2>
      <el-button :icon="Refresh" plain @click="loadData">刷新</el-button>
    </div>
    <div class="zv-sec-filter">
<el-input v-model="keyword" placeholder="搜索主机名/IP" clearable style="width:240px" @keyup.enter="handleSearch" />
          <el-button type="primary" @click="handleSearch">查询</el-button>
    </div>
    <el-table :data="terminals" stripe v-loading="loading" style="margin-top:12px" @row-click="goDetail">
      <el-table-column prop="hostname" label="主机名" min-width="130" />
      <el-table-column prop="ip_address" label="IP地址" min-width="130" />
      <el-table-column prop="os_type" label="操作系统" min-width="160" show-overflow-tooltip />
      <el-table-column prop="real_status" label="状态" width="90">
        <template #default="{ row }"><el-tag :type="row.real_status==='online'?'success':'info'" size="small">{{ row.real_status==='online'?'在线':'离线' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="agent_install_status" label="Agent" width="100">
        <template #default="{ row }"><el-tag :type="row.agent_install_status==='installed'?'success':'danger'" size="small">{{ row.agent_install_status==='installed'?'已安装':'未安装' }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="open_events" label="未处置事件" width="110">
        <template #default="{ row }"><el-tag :type="row.open_events>0?'danger':'success'" size="small">{{ row.open_events }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="last_event_time" label="最近事件" width="160" />
      <el-table-column prop="last_seen" label="最后心跳" width="160" />
    </el-table>
    <el-pagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" :page-sizes="[20,50,100]" layout="total,sizes,prev,pager,next" style="margin-top:16px;justify-content:flex-end;display:flex" @size-change="loadData" @current-change="loadData" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { getSecurityTerminals } from '@/api/security'

const router = useRouter()
const loading = ref(false)
const terminals = ref([])
const keyword = ref('')
const page = ref(1); const pageSize = ref(20); const total = ref(0)

const handleSearch = () => { page.value = 1; loadData() }
const loadData = async () => {
  loading.value = true
  try {
    const res = await getSecurityTerminals({ page: page.value, page_size: pageSize.value, keyword: keyword.value || undefined })
    terminals.value = res.data || []; total.value = res.total || 0
  } catch (e) { ElMessage.error('加载终端安全列表失败') }
  finally { loading.value = false }
}
const goDetail = (row) => router.push(`/security/terminals/${row.id}`)
onMounted(loadData)
</script>

<style scoped lang="scss">
.zv-sec-terminals { padding: 16px; }
.zv-sec-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.zv-sec-title { font-size: 20px; font-weight: 600; margin: 0; color: #303133; }
.zv-sec-filter { display: flex; gap: 10px; }
</style>