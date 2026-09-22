// -*- coding: utf-8 -*-
// 远控会话默认设置（P1-02 从 WebRemoteDesktop.vue 提取）

export const defaultSessionSettings = (overrides = {}) => ({
  quality: 75,
  fps: 18,
  scalePercent: 90,
  adaptive: true,
  profile: 'interactive',
  wheelSpeed: 1,
  mouseSensitivity: 1,
  preset: 'balanced',
  desktopWidth: 0,
  desktopHeight: 0,
  autoReconnect: false,
  ...overrides
})
