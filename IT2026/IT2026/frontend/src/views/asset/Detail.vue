<template>
  <div class="zv-page">
    <AssetInfoPanel :asset-id="route.params.id" :auto-edit="route.query.edit === 'true' || route.query.edit === '1'" />
  </div>
</template>

<script setup>
import { useRoute } from 'vue-router'
import AssetInfoPanel from './components/AssetInfoPanel.vue'

const route = useRoute()
</script>
