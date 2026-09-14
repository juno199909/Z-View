<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">日志配置</h2>
        <div class="zv-page-subtitle">日志留存、告警通知与告警配置的统一入口</div>
      </div>
    </div>
    <el-tabs v-model="activeTab" class="zv-config-tabs">
      <el-tab-pane label="日志留存" name="retention" lazy>
        <RetentionConfig />
      </el-tab-pane>
      <el-tab-pane label="通知配置" name="notify" lazy>
        <NotifyConfig />
      </el-tab-pane>
      <el-tab-pane label="告警配置" name="threshold" lazy>
        <ThresholdConfig />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import RetentionConfig from '@/views/log/RetentionConfig.vue'
import NotifyConfig from '@/views/alert/NotifyConfig.vue'
import ThresholdConfig from '@/views/alert/ThresholdConfig.vue'

const activeTab = ref('retention')
</script>

<style scoped>
.zv-config-tabs :deep(.el-tabs__content) {
  padding-top: 4px;
}
</style>
