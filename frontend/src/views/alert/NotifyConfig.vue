<template>
  <div class="zv-page">
    <div class="zv-page-header">
      <div>
        <h2 class="zv-page-title">告警通知配置</h2>
        <div class="zv-page-subtitle">新告警触发时通过 Webhook / 邮件推送，每条告警至多通知一次，恢复后再次触发重新通知</div>
      </div>
      <div class="zv-page-actions">
        <el-button :icon="Refresh" @click="loadConfig" :loading="loading">刷新</el-button>
        <el-button type="success" :icon="Promotion" :loading="testing" @click="sendTest">发送测试通知</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon class="zv-notify-tip"
      title="通知规则"
      description="达到最低严重度的新告警会自动分发：支持企业微信群机器人 URL（自动识别，合并为 markdown 消息）与自定义 Webhook（每条 POST JSON）；邮件每轮合并为一封摘要。保存后由后台告警同步线程（60 秒周期）自动生效。" />

    <div class="zv-card zv-card-pad zv-notify-form">
      <el-form :model="form" label-width="130px" :disabled="loading">
        <el-form-item label="启用通知">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <el-form-item label="最低严重度">
          <el-radio-group v-model="form.min_severity">
            <el-radio-button value="warning">warning 及以上</el-radio-button>
            <el-radio-button value="critical">仅 critical</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-divider content-position="left">Webhook</el-divider>
        <el-form-item label="Webhook URL">
          <el-input v-model="form.webhook_url" placeholder="http://…（POST JSON），留空表示不启用" clearable />
        </el-form-item>

        <el-divider content-position="left">邮件（SMTP）</el-divider>
        <el-form-item label="SMTP 服务器">
          <el-input v-model="form.smtp_host" placeholder="如 smtp.exmail.com" clearable style="max-width: 320px" />
        </el-form-item>
        <el-form-item label="端口">
          <el-input-number v-model="form.smtp_port" :min="1" :max="65535" />
          <el-checkbox v-model="form.smtp_use_ssl" class="zv-notify-ssl">SSL</el-checkbox>
        </el-form-item>
        <el-form-item label="SMTP 用户名">
          <el-input v-model="form.smtp_user" clearable style="max-width: 320px" />
        </el-form-item>
        <el-form-item label="SMTP 密码">
          <el-input v-model="form.smtp_password" type="password" show-password
            :placeholder="form.has_smtp_password ? '已保存（留空保持不变）' : '未设置'" clearable style="max-width: 320px" />
        </el-form-item>
        <el-form-item label="发件地址">
          <el-input v-model="form.from_addr" placeholder="与 SMTP 用户名通常一致" clearable style="max-width: 320px" />
        </el-form-item>
        <el-form-item label="收件地址">
          <el-input v-model="form.to_addrs" type="textarea" :rows="2"
            placeholder="多个收件人用英文逗号分隔，如 ops@corp.com,admin@corp.com" />
        </el-form-item>

        <el-form-item>
          <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Promotion, Refresh } from '@element-plus/icons-vue'
import { fetchAlertNotifyConfig, testAlertNotifyConfig, updateAlertNotifyConfig } from '@/api/alert'

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const form = ref({
  enabled: false,
  min_severity: 'critical',
  webhook_url: '',
  smtp_host: '',
  smtp_port: 465,
  smtp_use_ssl: true,
  smtp_user: '',
  smtp_password: '',
  from_addr: '',
  to_addrs: '',
  has_smtp_password: false
})

const loadConfig = async () => {
  loading.value = true
  try {
    const cfg = await fetchAlertNotifyConfig()
    form.value = {
      enabled: Boolean(cfg.enabled),
      min_severity: cfg.min_severity || 'critical',
      webhook_url: cfg.webhook_url || '',
      smtp_host: cfg.smtp_host || '',
      smtp_port: Number(cfg.smtp_port) || 465,
      smtp_use_ssl: Boolean(cfg.smtp_use_ssl),
      smtp_user: cfg.smtp_user || '',
      smtp_password: '',
      from_addr: cfg.from_addr || '',
      to_addrs: cfg.to_addrs || '',
      has_smtp_password: Boolean(cfg.has_smtp_password)
    }
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    loading.value = false
  }
}

const sendTest = async () => {
  testing.value = true
  try {
    const result = await testAlertNotifyConfig()
    if (result && result.error) {
      ElMessage.warning(`测试未发送：${result.error}`)
      return
    }
    const parts = []
    if (result.wecom_ok !== undefined) parts.push(`企业微信: ${result.wecom_ok ? '已送达' : '失败 ' + (result.wecom_error || '')}`)
    if (result.webhook_ok !== undefined) parts.push(`Webhook: ${result.webhook_ok ? '已送达' : '失败'}`)
    if (result.email_ok !== undefined) parts.push(`邮件: ${result.email_ok ? '已发送' : '失败'}`)
    const ok = parts.some(p => p.includes('已送达') || p.includes('已发送'))
    if (ok) {
      ElMessage.success(`测试通知已发送（${parts.join('；')}），请到对应群组/邮箱确认`)
    } else {
      ElMessage.error(`测试发送失败：${parts.join('；') || '无可用通道'}`)
    }
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    testing.value = false
  }
}

const save = async () => {
  saving.value = true
  try {
    const patch = { ...form.value }
    if (!patch.smtp_password) delete patch.smtp_password // 留空 = 保持现有密码
    await updateAlertNotifyConfig(patch)
    ElMessage.success('通知配置已保存，60 秒内生效')
    loadConfig()
  } catch (e) {
    // 错误提示由全局拦截器弹出
  } finally {
    saving.value = false
  }
}

onMounted(loadConfig)
</script>

<style lang="scss" scoped>
@use '@/assets/styles/variables.scss' as *;

.zv-page { padding: $content-padding; max-width: 900px; margin: 0 auto; }
.zv-page-actions { display: flex; gap: 10px; align-items: center; }
.zv-notify-tip { margin-bottom: 16px; }
.zv-notify-ssl { margin-left: 16px; }
</style>
