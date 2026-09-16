<template>
  <div>
    <!-- 页签导航：三个页签各自渲染原独立页面（与控制台其他页面外观一致） -->
    <div class="zv-config-tabsbar">
      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <el-tab-pane label="Agent策略" name="/agent/manage/policy" />
        <el-tab-pane label="Agent部署" name="/agent/manage/deploy" />
        <el-tab-pane label="Agent升级" name="/agent/manage/upgrade" />
        <el-tab-pane label="健康度" name="/agent/manage/health" />
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
