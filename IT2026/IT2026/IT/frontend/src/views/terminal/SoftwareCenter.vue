<template>
  <div class="software-center">
    <!-- 顶部标签页 -->
    <el-tabs v-model="activeTab" type="card" class="main-tabs">
      <el-tab-pane label="软件包仓库" name="packages" lazy>
        <PackageRepository />
      </el-tab-pane>
      <el-tab-pane label="任务管理" name="tasks" lazy>
        <TaskManagement />
      </el-tab-pane>
      <el-tab-pane label="已安装软件" name="installed" lazy>
        <InstalledSoftware />
      </el-tab-pane>
      <el-tab-pane label="软件合规" name="compliance" lazy>
        <ComplianceManagement />
      </el-tab-pane>
      <el-tab-pane label="策略管理" name="policies" lazy>
        <PolicyManagement />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import PackageRepository from './components/PackageRepository.vue'
import TaskManagement from './components/TaskManagement.vue'
import InstalledSoftware from './components/InstalledSoftware.vue'
import ComplianceManagement from './components/ComplianceManagement.vue'
import PolicyManagement from './components/PolicyManagement.vue'

const activeTab = ref('packages')
</script>

<style scoped>
.software-center {
  padding: 20px;
}

.main-tabs {
  background: white;
  border-radius: 4px;
}

:deep(.el-tabs__content) {
  padding: 0;
}
</style>
