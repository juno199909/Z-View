import { createRouter, createWebHistory } from 'vue-router'
import { handleAuthExpired, hasAuthSession, isSessionExpired } from '@/api/auth'

const routes = [
  {
    path: '/',
    component: () => import('@/views/Layout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: '/dashboard',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/Index.vue'),
        meta: { title: '仪表板', icon: 'Monitor' }
      },
      {
        path: '/asset',
        name: 'Asset',
        meta: { title: '资产管理', icon: 'Box' },
        children: [
          {
            path: 'list',
            name: 'AssetList',
            component: () => import('@/views/asset/List.vue'),
            meta: { title: '资产列表' }
          },
          {
            path: 'group',
            name: 'AssetGroup',
            component: () => import('@/views/asset/Group.vue'),
            meta: { title: '分组管理' }
          },
          {
            path: 'detail/:id',
            name: 'AssetDetail',
            component: () => import('@/views/asset/Detail.vue'),
            meta: { title: '资产详情' }
          },
          {
            path: 'create',
            name: 'AssetCreate',
            component: () => import('@/views/asset/Create.vue'),
            meta: { title: '新增资产' }
          }
        ]
      },
      {
        path: '/alert',
        name: 'Alert',
        component: () => import('@/views/alert/Alert.vue'),
        meta: { title: '终端日志', icon: 'Bell' }
      },
      {
        path: '/log',
        name: 'LogCenter',
        component: () => import('@/views/log/Index.vue'),
        meta: { title: '日志总览', icon: 'Tickets' }
      },
      {
        path: '/log/operations',
        name: 'OperationLogs',
        component: () => import('@/views/log/Operations.vue'),
        meta: { title: '操作日志' }
      },
      {
        path: '/automation',
        name: 'Automation',
        component: () => import('@/views/automation/Batch.vue'),
        meta: { title: '批量操作', icon: 'Operation' }
      },
      {
        path: '/terminal',
        name: 'Terminal',
        meta: { title: '终端管理', icon: 'Monitor' },
        children: [
          {
            path: 'overview',
            name: 'TerminalOverview',
            component: () => import('@/views/terminal/Overview.vue'),
            meta: { title: '终端概览' }
          },
          {
            path: 'detail/:id',
            name: 'TerminalDetail',
            component: () => import('@/views/terminal/Detail.vue'),
            meta: { title: '终端详情' }
          },
          {
            path: 'software-center',
            name: 'SoftwareCenter',
            component: () => import('@/views/terminal/SoftwareCenter.vue'),
            meta: { title: '软件管理' }
          },
          {
            path: 'agent-upgrade',
            name: 'AgentUpgrade',
            component: () => import('@/views/terminal/AgentUpgrade.vue'),
            meta: { title: 'Agent升级' }
          }
        ]
      },
      {
        path: '/discovery',
        name: 'Discovery',
        component: () => import('@/views/discovery/Index.vue'),
        meta: { title: '资产发现', icon: 'Search' }
      },
      {
        path: '/security',
        name: 'Security',
        meta: { title: '安全管理', icon: 'Lock' },
        children: [
          {
            path: 'overview',
            name: 'SecurityOverview',
            component: () => import('@/views/security/Overview.vue'),
            meta: { title: '安全总览' }
          },
          {
            path: 'terminals',
            name: 'SecurityTerminals',
            component: () => import('@/views/security/Terminals.vue'),
            meta: { title: '终端安全' }
          },
          {
            path: 'terminals/:id',
            name: 'SecurityTerminalDetail',
            component: () => import('@/views/security/TerminalDetail.vue'),
            meta: { title: '终端安全详情' }
          },
          {
            path: 'firewall',
            name: 'SecurityFirewall',
            component: () => import('@/views/security/Firewall.vue'),
            meta: { title: '防火墙' }
          },
          {
            path: 'usb',
            name: 'SecurityUsb',
            component: () => import('@/views/security/Usb.vue'),
            meta: { title: 'USB管控' }
          },
          {
            path: 'app-control',
            name: 'SecurityAppControl',
            component: () => import('@/views/security/AppControl.vue'),
            meta: { title: '程序管控' }
          },
          {
            path: 'file-protect',
            name: 'SecurityFileProtect',
            component: () => import('@/views/security/FileProtect.vue'),
            meta: { title: '文件保护' }
          },
          {
            path: 'behavior',
            name: 'SecurityBehavior',
            component: () => import('@/views/security/Behavior.vue'),
            meta: { title: '行为监控' }
          },
          {
            path: 'events',
            name: 'SecurityEvents',
            component: () => import('@/views/security/Events.vue'),
            meta: { title: '安全事件' }
          },
          {
            path: 'policies',
            name: 'SecurityPolicies',
            component: () => import('@/views/security/Policies.vue'),
            meta: { title: '策略中心' }
          }
        ]
      },
      {
        path: '/settings/agent-policy',
        name: 'AgentPolicy',
        component: () => import('@/views/settings/AgentPolicy.vue'),
        meta: { title: '终端策略', icon: 'Setting' }
      }
    ]
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const expired = isSessionExpired()
  const loggedIn = hasAuthSession()

  if (to.path === '/login') {
    if (loggedIn) {
      return '/dashboard'
    }
    return true
  }

  if (expired) {
    handleAuthExpired('expired', to.fullPath)
    return false
  }

  if (!loggedIn) {
    return {
      path: '/login',
      query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : {}
    }
  }

  return true
})

export default router
