// -*- coding: utf-8 -*-
// 远控会话默认设置（P1-02 从 WebRemoteDesktop.vue 提取）

export const defaultSessionSettings = (overrides = {}) => ({
  quality: 60,
  fps: 60,
  scalePercent: 60,
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
