<template>
  <div>
    <!-- 页签导航：三个页签各自渲染标准独立页面（与控制台其他页面外观一致） -->
    <div class="zv-config-tabsbar">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <el-tab-pane label="日志留存" name="/log/config/retention" />
        <el-tab-pane label="通知配置" name="/log/config/notify" />
        <el-tab-pane label="告警配置" name="/log/config/threshold" />
      </el-tabs>
    </div>
    <router-view />
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()
const activeTab = ref(route.path)

watch(() => route.path, (p) => { activeTab.value = p })
const onTabChange = (name) => router.push(String(name))
</script>

<style scoped>
.zv-config-tabsbar {
  padding: 8px 24px 0;
}
</style>
