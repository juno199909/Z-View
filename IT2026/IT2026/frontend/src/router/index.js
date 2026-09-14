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
            // 资产详情与终端详情合并：直接重定向到终端详情页（实时监控+资产档案同页）
            redirect: (to) => ({ path: `/terminal/detail/${to.params.id}`, query: to.query })
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
        path: '/alert/notify',
        name: 'AlertNotify',
        component: () => import('@/views/alert/NotifyConfig.vue'),
        meta: { title: '通知配置' }
      },
      {
        path: '/alert/thresholds',
        name: 'AlertThresholds',
        component: () => import('@/views/alert/ThresholdConfig.vue'),
        meta: { title: '告警配置' }
      },
      {
        path: '/incidents',
        name: 'Incidents',
        component: () => import('@/views/alert/Incidents.vue'),
        meta: { title: '告警中心' }
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
        path: '/log/config',
        name: 'LogConfig',
        component: () => import('@/views/log/ConfigCenter.vue'),
        meta: { title: '日志配置' }
      },
      {
        path: '/system/users',
        name: 'SystemUsers',
        component: () => import('@/views/system/Users.vue'),
        meta: { title: '用户管理' }
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
        path: '/patches',
        name: 'PatchManagement',
        component: () => import('@/views/terminal/PatchManagement.vue'),
        meta: { title: '补丁管理' }
      },
      {
        path: 'detail/:id',
        name: 'TerminalDetail',
        component: () => import('@/views/terminal/Detail.vue'),
        meta: { title: '终端详情' }
      },
      {
        path: '/jobs',
        name: 'JobCenter',
        component: () => import('@/views/terminal/JobCenter.vue'),
        meta: { title: '任务中心' }
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
          },
          {
            path: 'agent-deploy',
            name: 'AgentDeploy',
            component: () => import('@/views/terminal/AgentDeploy.vue'),
            meta: { title: '终端部署' }
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
