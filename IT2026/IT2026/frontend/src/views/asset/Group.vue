<template>
  <div class="group-container">
    <el-card>
      <template #header>
        <div class="card-header">
          <span>分组管理</span>
          <el-button type="primary" @click="showCreateDialog">新建分组</el-button>
        </div>
      </template>

      <el-table :data="groups" style="width: 100%">
        <el-table-column prop="name" label="分组名称" width="200" />
        <el-table-column prop="description" label="描述" />
        <el-table-column prop="asset_count" label="资产数量" width="120" align="center" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
        <el-table-column label="操作" width="200" align="center">
          <template #default="scope">
            <el-button size="small" @click="editGroup(scope.row)">编辑</el-button>
            <el-button size="small" type="danger" @click="handleDeleteGroup(scope.row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建/编辑分组对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogTitle"
      width="500px"
    >
      <el-form :model="formData" label-width="80px">
        <el-form-item label="分组名称" required>
          <el-input v-model="formData.name" placeholder="请输入分组名称" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="formData.description"
            type="textarea"
            :rows="3"
            placeholder="请输入描述"
          />
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
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getGroups, createGroup, updateGroup, deleteGroup } from '@/api/group'

const groups = ref([])
const dialogVisible = ref(false)
const dialogTitle = ref('新建分组')
const formData = ref({
  id: null,
  name: '',
  description: ''
})

const loadGroups = async () => {
  try {
    const response = await getGroups()
    groups.value = response.data
  } catch (error) {
    ElMessage.error('加载分组列表失败')
  }
}

const showCreateDialog = () => {
  dialogTitle.value = '新建分组'
  formData.value = {
    id: null,
    name: '',
    description: ''
  }
  dialogVisible.value = true
}

const editGroup = (row) => {
  dialogTitle.value = '编辑分组'
  formData.value = {
    id: row.id,
    name: row.name,
    description: row.description
  }
  dialogVisible.value = true
}

const submitForm = async () => {
  if (!formData.value.name.trim()) {
    ElMessage.warning('请输入分组名称')
    return
  }

  try {
    if (formData.value.id) {
      // 编辑
      await updateGroup(formData.value.id, {
        name: formData.value.name,
        description: formData.value.description
      })
      ElMessage.success('分组更新成功')
    } else {
      // 创建
      await createGroup({
        name: formData.value.name,
        description: formData.value.description
      })
      ElMessage.success('分组创建成功')
    }

    dialogVisible.value = false
    loadGroups()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败')
  }
}

const handleDeleteGroup = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除分组"${row.name}"吗？${row.asset_count > 0 ? `该分组下有 ${row.asset_count} 个资产，` : ''}`,
      '警告',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )

    await deleteGroup(row.id)
    ElMessage.success('分组删除成功')
    loadGroups()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error(error.response?.data?.detail || '删除失败')
    }
  }
}

onMounted(() => {
  loadGroups()
})
</script>

<style scoped>
.group-container {
  padding: 20px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
