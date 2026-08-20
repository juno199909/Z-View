// -*- coding: utf-8 -*-
// 远控分辨率格式化工具（P1-02 从 WebRemoteDesktop.vue 提取的纯函数）
// 无组件状态依赖，可独立单元测试。

export const formatResolutionText = (width, height, fallback = '-') => {
  const normalizedWidth = Math.max(0, Math.round(Number(width) || 0))
  const normalizedHeight = Math.max(0, Math.round(Number(height) || 0))
  if (!normalizedWidth || !normalizedHeight) {
    return fallback
  }
  return `${normalizedWidth}x${normalizedHeight}`
}

export const parseDesktopResolutionValue = (value, fallbackWidth = 0, fallbackHeight = 0) => {
  const text = String(value || '').trim()
  const match = text.match(/^(\d+)\s*x\s*(\d+)$/i)
  if (!match) {
    return {
      width: Math.max(0, Math.round(Number(fallbackWidth) || 0)),
      height: Math.max(0, Math.round(Number(fallbackHeight) || 0))
    }
  }

  return {
    width: Math.max(0, Math.round(Number(match[1]) || 0)),
    height: Math.max(0, Math.round(Number(match[2]) || 0))
  }
}

export const getDesktopResolutionValue = (settings) => {
  const width = Math.max(0, Math.round(Number(settings?.desktopWidth) || 0))
  const height = Math.max(0, Math.round(Number(settings?.desktopHeight) || 0))
  return width > 0 && height > 0 ? `${width}x${height}` : ''
}
