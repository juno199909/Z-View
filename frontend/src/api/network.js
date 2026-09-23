import request from './request'

// 终端实时网络状态（网卡 + 实时速率 + 网络质量）
export function getAssetNetwork(assetId) {
  return request({ url: `/assets/${assetId}/network`, method: 'get' })
}

// 终端网络流量/质量历史（1 分钟粒度）
export function getAssetNetworkHistory(assetId, range = '15m') {
  return request({ url: `/assets/${assetId}/network/history`, method: 'get', params: { range } })
}
