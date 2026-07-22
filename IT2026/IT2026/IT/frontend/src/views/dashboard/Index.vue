<template>
  <div class="app-container">
    <div class="dashboard">
      <!-- 统计卡片 -->
      <el-row :gutter="20" class="stats-row">
        <el-col :xs="24" :sm="12" :md="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-icon primary">
                <el-icon :size="40"><Box /></el-icon>
              </div>
              <div class="stat-info">
                <div class="stat-value">{{ stats.total || 0 }}</div>
                <div class="stat-label">总资产数</div>
              </div>
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :sm="12" :md="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-icon success">
                <el-icon :size="40"><CircleCheck /></el-icon>
              </div>
              <div class="stat-info">
                <div class="stat-value">{{ stats.online || 0 }}</div>
                <div class="stat-label">在线设备</div>
              </div>
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :sm="12" :md="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-icon danger">
                <el-icon :size="40"><CircleClose /></el-icon>
              </div>
              <div class="stat-info">
                <div class="stat-value">{{ stats.offline || 0 }}</div>
                <div class="stat-label">离线设备</div>
              </div>
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :sm="12" :md="6">
          <el-card shadow="hover" class="stat-card">
            <div class="stat-content">
              <div class="stat-icon warning">
                <el-icon :size="40"><Monitor /></el-icon>
              </div>
              <div class="stat-info">
                <div class="stat-value">{{ stats.server || 0 }}</div>
                <div class="stat-label">服务器</div>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 在线率和类型分布 -->
      <el-row :gutter="20" class="charts-row">
        <el-col :xs="24" :md="12">
          <el-card shadow="never">
            <template #header>
              <span>在线率统计</span>
            </template>
            <div class="online-rate-display">
              <div class="rate-circle">
                <div class="rate-value">{{ onlineRate }}%</div>
                <div class="rate-label">在线率</div>
              </div>
              <div class="rate-details">
                <el-descriptions :column="1" border size="small">
                  <el-descriptions-item label="总资产">{{ stats.total || 0 }}</el-descriptions-item>
                  <el-descriptions-item label="在线">
                    <el-tag type="success">{{ stats.online || 0 }}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="离线">
                    <el-tag type="danger">{{ stats.offline || 0 }}</el-tag>
                  </el-descriptions-item>
                  <el-descriptions-item label="未知">
                    <el-tag type="info">{{ stats.unknown || 0 }}</el-tag>
                  </el-descriptions-item>
                </el-descriptions>
              </div>
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="12">
          <el-card shadow="never">
            <template #header>
              <span>资产分组分布</span>
            </template>
            <div class="type-distribution">
              <div v-for="(value, key) in stats.byGroup" :key="key" class="type-item">
                <div class="type-info">
                  <span class="type-name">{{ key }}</span>
                  <span class="type-count">{{ value }}</span>
                </div>
                <el-progress :percentage="getPercentage(value)" :color="getGroupColor(key)" />
              </div>
              <el-empty v-if="!Object.keys(stats.byGroup || {}).length" description="暂无数据" />
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 软件统计 -->
      <el-row :gutter="20" style="margin-top: 20px;">
        <el-col :span="24">
          <el-card shadow="never">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <span>软件安装统计（Top 10）</span>
                <el-button type="primary" size="small" @click="$router.push('/terminal/software-center')">
                  查看全部
                </el-button>
              </div>
            </template>
            <el-table :data="topSoftware" v-loading="softwareLoading" stripe>
              <el-table-column prop="software_name" label="软件名称" min-width="200" show-overflow-tooltip />
              <el-table-column prop="version" label="版本" width="150" />
              <el-table-column prop="vendor" label="厂商" width="180" show-overflow-tooltip />
              <el-table-column prop="install_count" label="安装数量" width="120" align="center">
                <template #default="{ row }">
                  <el-tag type="success">{{ row.install_count }} 台</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="hostnames" label="已安装设备" min-width="250" show-overflow-tooltip>
                <template #default="{ row }">
                  <span style="color: #606266;">{{ row.hostnames }}</span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!topSoftware.length && !softwareLoading" description="暂无软件数据" />
          </el-card>
        </el-col>
      </el-row>

      <!-- 最近资产 -->
      <el-row :gutter="20">
        <el-col :span="24">
          <el-card shadow="never">
            <template #header>
              <div style="display: flex; justify-content: space-between; align-items: center;">
                <span>最近资产</span>
                <el-button type="primary" size="small" @click="$router.push('/asset/list')">
                  查看全部
                </el-button>
              </div>
            </template>
            <el-table :data="recentAssets" v-loading="loading" stripe>
              <el-table-column prop="hostname" label="主机名" min-width="150" />
              <el-table-column prop="ip_address" label="IP地址" width="140" />
              <el-table-column label="分组" width="100">
                <template #default="{ row }">
                  <el-tag type="info">
                    {{ row.group_name || '未分组' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }">
                  <el-tag :type="getStatusTagType(row.status)">
                    {{ getStatusName(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="location" label="位置" min-width="150" />
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button type="primary" link size="small" @click="viewAsset(row.id)">
                    详情
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { Box, CircleCheck, CircleClose, Monitor } from '@element-plus/icons-vue'
import { getAssetStats, getAssetList, getSoftwareStats } from '@/api/asset'

const router = useRouter()
const loading = ref(false)
const softwareLoading = ref(false)
const stats = ref({
  total: 0,
  online: 0,
  offline: 0,
  unknown: 0,
  server: 0,
  byType: {},
  byGroup: {}
})
const recentAssets = ref([])
const topSoftware = ref([])
let refreshTimer = null

// 计算在线率
const onlineRate = computed(() => {
  if (stats.value.total === 0) return 0
  return Math.round((stats.value.online / stats.value.total) * 100)
})

// 加载统计数据
const loadStats = async () => {
  try {
    const data = await getAssetStats()
    stats.value = {
      total: data.total || 0,
      online: data.online || 0,
      offline: data.offline || 0,
      unknown: data.unknown || 0,
      server: (data.by_type && data.by_type.server) || 0,
      byType: data.by_type || {},
      byGroup: data.by_group || {}
    }
  } catch (error) {
    console.error('加载统计失败:', error)
  }
}

// 加载最近资产
const loadRecentAssets = async () => {
  loading.value = true
  try {
    const res = await getAssetList({ page: 1, page_size: 5 })
    recentAssets.value = res.data || []
  } catch (error) {
    console.error('加载资产失败:', error)
  } finally {
    loading.value = false
  }
}

// 加载软件统计
const loadSoftwareStats = async () => {
  softwareLoading.value = true
  try {
    const res = await getSoftwareStats({ limit: 10 })
    const rows = Array.isArray(res?.data) ? res.data : []

    topSoftware.value = rows.map(item => ({
      ...item,
      hostnames: item.hostnames || item.installed_assets || '-'
    }))
  } catch (error) {
    console.error('加载软件统计失败:', error)
    topSoftware.value = []
  } finally {
    softwareLoading.value = false
  }
}

// 获取类型名称
const getTypeName = (type) => {
  const map = {
    server: '服务器',
    switch: '交换机',
    router: '路由器',
    pc: 'PC终端',
    unknown: '未知'
  }
  return map[type] || type
}

// 获取类型颜色
const getTypeColor = (type) => {
  const map = {
    server: '#409EFF',
    switch: '#67C23A',
    router: '#E6A23C',
    pc: '#909399',
    unknown: '#C0C4CC'
  }
  return map[type] || '#909399'
}

const getGroupColor = (groupName) => {
  const palette = ['#409EFF', '#67C23A', '#E6A23C', '#F56C6C', '#909399', '#36CFC9']
  const seed = String(groupName || '')
  let hash = 0

  for (let i = 0; i < seed.length; i += 1) {
    hash = (hash + seed.charCodeAt(i)) % palette.length
  }

  return palette[hash]
}

// 获取百分比
const getPercentage = (value) => {
  if (stats.value.total === 0) return 0
  return Math.round((value / stats.value.total) * 100)
}

// 获取类型标签类型
const getTypeTagType = (type) => {
  const map = {
    server: 'primary',
    switch: 'success',
    router: 'warning',
    pc: 'info',
    unknown: 'info'
  }
  return map[type] || 'info'
}

// 获取状态标签类型
const getStatusTagType = (status) => {
  const map = {
    online: 'success',
    offline: 'danger',
    degraded: 'warning',
    unknown: 'info'
  }
  return map[status] || 'info'
}

// 获取状态名称
const getStatusName = (status) => {
  const map = {
    online: '在线',
    offline: '离线',
    degraded: '降级',
    unknown: '未知'
  }
  return map[status] || status
}

// 查看资产详情
const viewAsset = (id) => {
  router.push(`/asset/detail/${id}`)
}

onMounted(() => {
  loadStats()
  loadRecentAssets()
  loadSoftwareStats()

  // 每30秒刷新一次
  refreshTimer = setInterval(() => {
    loadStats()
    loadSoftwareStats()
  }, 30000)
})

onBeforeUnmount(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>

<style lang="scss" scoped>
.dashboard {
  .stats-row {
    margin-bottom: 20px;
  }

  .stat-card {
    .stat-content {
      display: flex;
      align-items: center;
      gap: 20px;

      .stat-icon {
        width: 70px;
        height: 70px;
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;

        &.primary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        &.success { background: linear-gradient(135deg, #67C23A 0%, #42A85F 100%); }
        &.danger { background: linear-gradient(135deg, #F56C6C 0%, #E54D42 100%); }
        &.warning { background: linear-gradient(135deg, #E6A23C 0%, #D89F2C 100%); }
      }

      .stat-info {
        flex: 1;

        .stat-value {
          font-size: 32px;
          font-weight: 700;
          color: #333;
          line-height: 1;
          margin-bottom: 8px;
        }

        .stat-label {
          font-size: 14px;
          color: #999;
        }
      }
    }
  }

  .charts-row {
    margin-bottom: 20px;

    .el-card {
      height: 100%;
      min-height: 300px;
    }
  }

  .online-rate-display {
    display: flex;
    gap: 40px;
    align-items: center;
    padding: 20px 0;
    min-height: 200px;

    .rate-circle {
      flex-shrink: 0;
      width: 160px;
      height: 160px;
      border-radius: 50%;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      color: white;

      .rate-value {
        font-size: 48px;
        font-weight: 700;
        line-height: 1;
      }

      .rate-label {
        font-size: 14px;
        margin-top: 8px;
        opacity: 0.9;
      }
    }

    .rate-details {
      flex: 1;
    }
  }

  .type-distribution {
    padding: 10px 0;
    min-height: 200px;

    .type-item {
      margin-bottom: 20px;

      &:last-child {
        margin-bottom: 0;
      }

      .type-info {
        display: flex;
        justify-content: space-between;
        margin-bottom: 8px;

        .type-name {
          font-size: 14px;
          color: #333;
        }

        .type-count {
          font-size: 14px;
          font-weight: 600;
          color: #409EFF;
        }
      }
    }
  }
}
</style>
