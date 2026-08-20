import { ref } from 'vue'
import { getGroups } from '@/api/group'
import { getAssetOptions } from '@/api/asset'

/**
 * 安全模块共用的「终端组 / 终端」下拉数据源。
 * 组选项: { label: name, value: id }
 * 终端选项: { label: 'hostname (ip)', value: id }
 */
export function useAssetGroupOptions() {
  const groups = ref([])
  const assets = ref([])
  const loadingOptions = ref(false)

  const loadOptions = async () => {
    loadingOptions.value = true
    try {
      const [g, a] = await Promise.all([
        getGroups(),
        getAssetOptions()
      ])
      groups.value = (g.data || g || []).map(x => ({
        label: x.name,
        value: x.id
      }))
      const rows = a.data || a || []
      assets.value = (Array.isArray(rows) ? rows : []).map(x => ({
        label: `${x.hostname} (${x.ip_address || '-'})`,
        value: x.id
      }))
    } catch (e) {
      console.warn('[options] 加载终端组/终端下拉数据失败', e)
    } finally {
      loadingOptions.value = false
    }
  }

  return { groups, assets, loadingOptions, loadOptions }
}