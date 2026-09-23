<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">终端分组</h2>
        <div class="zv-page-subtitle">共 {{ groups.length }} 个业务分组 · {{ totalAssets }} 台设备</div>
      </div>
      <div class="zv-page-actions">
        <el-button type="primary" :icon="Plus" @click="showCreateDialog">新建分组</el-button>
      </div>
    </div>

    <div v-loading="loading" class="zv-group-grid">
      <div v-for="(g, idx) in groups" :key="g.id" class="zv-group-card" :style="{ '--accent': getGroupColor(g.id, idx) }">
        <div class="zv-group-icon">
          <el-icon :size="22"><Files /></el-icon>
        </div>
        <div class="zv-group-body">
          <div class="zv-group-name">{{ g.name }}</div>
          <div class="zv-group-desc">{{ g.description || '暂无描述' }}</div>
          <div class="zv-group-meta">
            <span class="zv-meta-item">
              <el-icon :size="12"><Box /></el-icon>
              {{ g.asset_count || 0 }} 设备
            </span>
            <span class="zv-meta-item">
              <el-icon :size="12"><Clock /></el-icon>
              {{ formatDate(g.created_at) }}
            </span>
          </div>
        </div>
        <div class="zv-group-actions">
          <el-button text type="primary" size="small" :icon="Edit" @click="editGroup(g)">编辑</el-button>
          <el-button text type="danger" size="small" :icon="Delete" @click="handleDeleteGroup(g)">删除</el-button>
        </div>
      </div>

      <div v-if="!groups.length && !loading" class="zv-empty">
        <el-empty description="暂无分组" :image-size="100">
          <el-button type="primary" :icon="Plus" @click="showCreateDialog">新建分组</el-button>
        </el-empty>
      </div>
    </div>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="500px" destroy-on-close>
      <el-form :model="formData" label-width="84px" label-position="top">
        <el-form-item label="分组名称" required>
          <el-input v-model="formData.name" placeholder="请输入分组名称" maxlength="32" show-word-limit />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="formData.description" type="textarea" :rows="3" placeholder="可选：分组用途说明" maxlength="120" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitForm">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Edit, Delete, Files, Box, Clock } from '@element-plus/icons-vue'
import { getGroups, createGroup, updateGroup, deleteGroup } from '@/api/group'
import dayjs from 'dayjs'

const groups = ref([])
const loading = ref(false)
const dialogVisible = ref(false)
const dialogTitle = ref('新建分组')
const formData = ref({ id: null, name: '', description: '' })

const totalAssets = computed(() => groups.value.reduce((sum, g) => sum + (g.asset_count || 0), 0))

const palette = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#ef4444', '#6366f1']
const getGroupColor = (id, idx) => palette[idx % palette.length]

const loadGroups = async () => {
  loading.value = true
  try {
    const res = await getGroups()
    groups.value = res.data || []
  } catch (error) {
    ElMessage.error('加载分组列表失败')
  } finally {
    loading.value = false
  }
}

const showCreateDialog = () => {
  dialogTitle.value = '新建分组'
  formData.value = { id: null, name: '', description: '' }
  dialogVisible.value = true
}

const editGroup = (row) => {
  dialogTitle.value = '编辑分组'
  formData.value = { id: row.id, name: row.name, description: row.description }
  dialogVisible.value = true
}

const submitForm = async () => {
  if (!formData.value.name.trim()) {
    ElMessage.warning('请输入分组名称')
    return
  }
  try {
    if (formData.value.id) {
      await updateGroup(formData.value.id, { name: formData.value.name, description: formData.value.description })
      ElMessage.success('分组更新成功')
    } else {
      await createGroup({ name: formData.value.name, description: formData.value.description })
      ElMessage.success('分组创建成功')
    }
    dialogVisible.value = false
    loadGroups()
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '操作失败')
  }
}

const handleDeleteGroup = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除分组"${row.name}"吗？${row.asset_count > 0 ? `该分组下有 ${row.asset_count} 个资产。` : ''}`,
      '警告',
      { confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning' }
    )
    await deleteGroup(row.id)
    ElMessage.success('分组删除成功')
    loadGroups()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error?.response?.data?.detail || '删除失败')
  }
}

const formatDate = (v) => v ? dayjs(v).format('YYYY-MM-DD') : '-'

onMounted(() => loadGroups())
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page {
  padding: $content-padding;
  max-width: 1600px;
  margin: 0 auto;
}

.zv-page-actions {
  display: flex;
  gap: 10px;
}

.zv-group-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.zv-group-card {
  --accent: #{$brand-primary};
  background: $bg-card;
  border: 1px solid $border-color-light;
  border-radius: $border-radius-lg;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  position: relative;
  overflow: hidden;
  transition: all $transition-base;
  box-shadow: $shadow-sm;

  &::before {
    content: '';
    position: absolute;
    left: 0;
    top: 0;
    bottom: 0;
    width: 3px;
    background: var(--accent);
  }

  &:hover {
    transform: translateY(-2px);
    box-shadow: $shadow-md;
    border-color: var(--accent);
  }
}

.zv-group-icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.zv-group-body {
  flex: 1;
  min-width: 0;
}

.zv-group-name {
  font-size: 15px;
  font-weight: 600;
  color: $text-primary;
  margin-bottom: 4px;
}

.zv-group-desc {
  font-size: 12px;
  color: $text-secondary;
  margin-bottom: 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.zv-group-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: $text-tertiary;
}

.zv-meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.zv-group-actions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-end;
}

.zv-empty {
  grid-column: 1 / -1;
  padding: 60px 0;
  text-align: center;
}
</style>
