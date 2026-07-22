<template>
  <div class="app-container">
    <el-card>
      <template #header>
        <span>新增资产</span>
      </template>

      <el-form :model="form" :rules="rules" ref="formRef" label-width="120px">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="资产类型" prop="asset_type">
              <el-select v-model="form.asset_type" placeholder="请选择">
                <el-option label="服务器" value="server" />
                <el-option label="交换机" value="switch" />
                <el-option label="路由器" value="router" />
                <el-option label="PC终端" value="pc" />
              </el-select>
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="主机名" prop="hostname">
              <el-input v-model="form.hostname" placeholder="例如: SRV-WEB-01" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="IP地址" prop="ip_address">
              <el-input v-model="form.ip_address" placeholder="例如: 192.168.1.100" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="MAC地址">
              <el-input v-model="form.mac_address" placeholder="例如: 00:11:22:33:44:55" />
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="状态">
              <el-select v-model="form.status">
                <el-option label="在线" value="online" />
                <el-option label="离线" value="offline" />
                <el-option label="未知" value="unknown" />
              </el-select>
            </el-form-item>
          </el-col>

          <el-col :span="12">
            <el-form-item label="位置">
              <el-input v-model="form.location" placeholder="例如: 数据中心A-机柜01" />
            </el-form-item>
          </el-col>

          <el-col :span="24">
            <el-form-item label="备注">
              <el-input v-model="form.remarks" type="textarea" :rows="3" />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item>
          <el-button type="primary" @click="handleSubmit" :loading="loading">提交</el-button>
          <el-button @click="$router.back()">取消</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { createAsset } from '@/api/asset'

const router = useRouter()
const formRef = ref(null)
const loading = ref(false)

const form = reactive({
  asset_type: '',
  hostname: '',
  ip_address: '',
  mac_address: '',
  status: 'unknown',
  location: '',
  remarks: ''
})

const rules = {
  asset_type: [{ required: true, message: '请选择资产类型', trigger: 'change' }],
  hostname: [{ required: true, message: '请输入主机名', trigger: 'blur' }],
  ip_address: [{ required: true, message: '请输入IP地址', trigger: 'blur' }]
}

const handleSubmit = async () => {
  if (!formRef.value) return

  await formRef.value.validate(async (valid) => {
    if (valid) {
      loading.value = true
      try {
        await createAsset(form)
        ElMessage.success('创建成功')
        router.push('/asset/list')
      } catch (error) {
        console.error('创建失败:', error)
      } finally {
        loading.value = false
      }
    }
  })
}
</script>
