<template>
  <div class="installed-software">
    <el-card>
      <p style="color: #909399; margin-bottom: 15px;">
        查看所有终端已安装的软件清单
      </p>

      <!-- 搜索 -->
      <el-input
        v-model="searchKeyword"
        placeholder="搜索软件名称、主机名、IP地址..."
        clearable
        style="width: 400px; margin-bottom: 15px"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>

      <el-table :data="filteredSoftware" v-loading="loading" style="width: 100%">
        <el-table-column prop="software_name" label="软件名称" width="250" />
        <el-table-column prop="version" label="版本" width="150" />
        <el-table-column prop="vendor" label="厂商" width="150" />
        <el-table-column prop="hostname" label="主机名" width="150" />
        <el-table-column prop="ip_address" label="IP地址" width="150" />
        <el-table-column prop="install_date" label="安装日期" width="120" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button size="small" type="danger" @click="uninstallSoftware(row)">
              卸载
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { createSoftwareTask } from '@/api/software'
import { getInstalledSoftwareInventory } from '@/api/asset'

const softwareList = ref([])
const loading = ref(false)
const searchKeyword = ref('')

const filteredSoftware = computed(() => {
  if (!searchKeyword.value) return softwareList.value

  const keyword = searchKeyword.value.toLowerCase()
  return softwareList.value.filter(item =>
    item.software_name?.toLowerCase().includes(keyword) ||
    item.hostname?.toLowerCase().includes(keyword) ||
    item.ip_address?.includes(keyword) ||
    item.vendor?.toLowerCase().includes(keyword)
  )
})

const loadSoftwareList = async () => {
  loading.value = true
  try {
    const response = await getInstalledSoftwareInventory()
    softwareList.value = response.data || []
  } catch (error) {
    ElMessage.error('加载失败：' + (error.response?.data?.detail || error.message))
  } finally {
    loading.value = false
  }
}

const uninstallSoftware = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定要在终端 "${row.hostname}" 上卸载软件 "${row.software_name}" 吗？`,
      '卸载确认',
      {
        type: 'warning',
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      }
    )

    // 创建卸载任务
    const taskData = {
      task_name: `卸载 ${row.software_name}`,
      task_type: 'uninstall',
      software_name: row.software_name,
      target_type: 'asset',
      target_ids: [row.asset_id],
      schedule_type: 'immediate',
      priority: 'normal'
    }

    await createSoftwareTask(taskData)
    ElMessage.success('卸载任务已创建，请在任务管理中查看进度')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('创建卸载任务失败：' + (error.response?.data?.detail || error.message))
    }
  }
}

onMounted(() => {
  loadSoftwareList()
})
</script>

<style scoped>
.installed-software {
  padding: 0;
}
</style>
