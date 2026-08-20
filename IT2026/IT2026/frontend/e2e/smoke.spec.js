// -*- coding: utf-8 -*-
// P3-02 最小 E2E 安全网：登录 → 关键页导航 → 远控对话框 → 断开
// 凭据：专用 e2e_runner 账号（operator），生产环境轮换/禁用
import { test, expect } from '@playwright/test'

const USERNAME = 'e2e_runner'
const PASSWORD = 'Zv-E2e-Runner-2026#k'

async function login(page) {
  await page.goto('/')
  // 已登录则跳过
  if (!page.url().includes('login')) {
    return
  }
  await page.getByPlaceholder(/用户名|username/i).fill(USERNAME)
  await page.getByPlaceholder(/密码|password/i).fill(PASSWORD)
  await page.getByRole('button', { name: /登\s*录|login/i }).click()
  await expect(page).not.toHaveURL(/login/, { timeout: 15_000 })
}

async function logout(page) {
  await page.evaluate(() => localStorage.clear())
  await page.evaluate(() => sessionStorage.clear())
}

test('health endpoint is public and healthy', async ({ request }) => {
  const resp = await request.get('http://127.0.0.1:8080/api/health')
  expect(resp.ok()).toBeTruthy()
  const body = await resp.json()
  expect(body.status).toBe('ok')
  expect(body.checks.database).toBe('ok')
})

test('login redirects to dashboard', async ({ page }) => {
  await login(page)
  // 登录后应离开登录页并渲染主布局（侧边栏存在）
  await expect(page.locator('.zv-sidebar').first()).toBeVisible({ timeout: 15_000 })
  await logout(page)
})

test('wrong password is rejected', async ({ page }) => {
  await page.goto('/')
  if (!page.url().includes('login')) return
  await page.getByPlaceholder(/用户名|username/i).fill(USERNAME)
  await page.getByPlaceholder(/密码|password/i).fill('wrong-password')
  await page.getByRole('button', { name: /登\s*录|login/i }).click()
  // 应仍在登录页（或出现错误提示），不进入主布局
  await page.waitForTimeout(2000)
  expect(page.url()).toContain("login")
})

test('terminal list page loads with data table', async ({ page }) => {
  await login(page)
  await page.goto('/asset/list')
  // Element Plus 表格或空态渲染
  await expect(page.locator('.el-table, .el-empty').first()).toBeVisible({ timeout: 15_000 })
  await logout(page)
})

test('security overview page loads', async ({ page }) => {
  await login(page)
  await page.goto('/security/overview')
  await expect(page.locator('.zv-sec-stats, .el-card, .zv-sec-card').first()).toBeVisible({ timeout: 15_000 })
  await logout(page)
})

test('remote desktop dialog opens canvas for online agent', async ({ page }) => {
  await login(page)
  // 本机终端详情页（asset 28 恒在线）
  await page.goto('/terminal/detail/28')
  const remoteBtn = page.getByRole('button', { name: /远程控制/ }).first()
  await expect(remoteBtn).toBeVisible({ timeout: 15_000 })
  await remoteBtn.click()
  // 远控对话框：canvas 渲染
  await expect(page.locator('.remote-container canvas').first()).toBeVisible({ timeout: 20_000 })
  // 关闭对话框（断开会话）
  await page.keyboard.press('Escape')
  await logout(page)
})
