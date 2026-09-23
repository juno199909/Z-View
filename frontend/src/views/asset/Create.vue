<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">新增资产</h2>
        <div class="zv-page-subtitle">手动登记一台资产到 Z-View 平台</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="ArrowLeft" @click="$router.back()">返回</el-button>
        <el-button type="primary" :icon="Check" :loading="submitting" @click="submitForm">保存</el-button>
      </div>
    </div>

    <div class="zv-card zv-card-pad">
      <el-form :model="form" :rules="rules" ref="formRef" label-position="top">
        <h3 class="zv-section-title">基础信息</h3>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="主机名" prop="hostname">
              <el-input v-model="form.hostname" placeholder="请输入主机名" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="资产类型" prop="asset_type">
              <el-select v-model="form.asset_type" style="width: 100%">
                <el-option label="PC 终端" value="pc" />
                <el-option label="服务器" value="server" />
                <el-option label="交换机" value="switch" />
                <el-option label="路由器" value="router" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="IP 地址" prop="ip_address">
              <el-input v-model="form.ip_address" placeholder="例如：192.168.1.10" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="MAC 地址">
              <el-input v-model="form.mac_address" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="序列号">
              <el-input v-model="form.serial_number" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="操作系统">
              <el-input v-model="form.os_type" placeholder="例如：Windows 11" />
            </el-form-item>
          </el-col>
        </el-row>

        <h3 class="zv-section-title">硬件信息</h3>
        <el-row :gutter="20">
          <el-col :span="8">
            <el-form-item label="CPU 核心数">
              <el-input-number v-model="form.cpu_cores" :min="1" :max="128" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="内存 (MB)">
              <el-input-number v-model="form.memory_mb" :min="0" :step="1024" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="磁盘 (GB)">
              <el-input-number v-model="form.disk_gb" :min="0" :step="100" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>

        <h3 class="zv-section-title">使用信息</h3>
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="所属分组">
              <el-select v-model="form.group_id" clearable filterable style="width: 100%">
                <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="使用人">
              <el-input v-model="form.user_name" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="使用部门">
              <el-input v-model="form.department" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="位置">
              <el-input v-model="form.location" />
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.notes" type="textarea" :rows="3" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Check } from '@element-plus/icons-vue'
import { createAsset } from '@/api/asset'
import { getGroups } from '@/api/group'

const router = useRouter()
const formRef = ref(null)
const submitting = ref(false)
const groups = ref([])

const form = reactive({
  hostname: '',
  asset_type: 'pc',
  ip_address: '',
  mac_address: '',
  serial_number: '',
  os_type: '',
  cpu_cores: 4,
  memory_mb: 8192,
  disk_gb: 256,
  group_id: null,
  user_name: '',
  department: '',
  location: '',
  notes: ''
})

const rules = {
  hostname: [{ required: true, message: '请输入主机名', trigger: 'blur' }],
  asset_type: [{ required: true, message: '请选择资产类型', trigger: 'change' }],
  ip_address: [
    { required: true, message: '请输入 IP 地址', trigger: 'blur' },
    { pattern: /^(\d{1,3}\.){3}\d{1,3}$/, message: 'IP 地址格式不正确', trigger: 'blur' }
  ]
}

const submitForm = async () => {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await createAsset(form)
    ElMessage.success('创建成功')
    router.push('/asset/list')
  } catch (error) {
    ElMessage.error('创建失败')
  } finally {
    submitting.value = false
  }
}

const loadGroups = async () => {
  try {
    const res = await getGroups()
    groups.value = res.data || []
  } catch {}
}

onMounted(loadGroups)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 1000px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; }

.zv-card-pad { padding: 24px 28px; }

.zv-section-title {
  font-size: 15px;
  font-weight: 600;
  color: $text-primary;
  margin: 0 0 16px 0;
  padding-bottom: 12px;
  border-bottom: 1px solid $border-color-light;
}

:deep(.el-form-item__label) {
  color: $text-secondary;
  font-weight: 500;
}

:deep(.el-input__wrapper),
:deep(.el-select__wrapper) {
  background: $slate-50;
  box-shadow: none;
  border-radius: $border-radius;
  transition: all $transition-base;
  &:hover { background: $bg-card; box-shadow: 0 0 0 1px $brand-primary-100; }
  &.is-focus { background: $bg-card; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.10); }
}
</style>
