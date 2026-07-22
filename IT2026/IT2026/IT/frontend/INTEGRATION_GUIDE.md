# 软件管理中心前端 - 集成说明

## 📁 已创建的前端页面

说明：`frontend/PackageList.vue` 和 `frontend/TaskMonitor.vue` 现在作为“独立集成示例”保留，已经统一改成走代理基址，不再写死固定 IP。
当前项目实际运行中的页面位于 `frontend/src/views/terminal/components/`。

### 1. PackageList.vue - 软件包管理
**路径**: `frontend/PackageList.vue`

**功能**:
- ✅ 软件包列表（分页、搜索、筛选）
- ✅ 上传软件包（表单+文件上传）
- ✅ 创建分发任务
- ✅ 删除软件包

**使用组件**:
- el-table - 列表展示
- el-dialog - 对话框
- el-upload - 文件上传
- el-form - 表单

### 2. TaskMonitor.vue - 任务监控
**路径**: `frontend/TaskMonitor.vue`

**功能**:
- ✅ 任务列表（实时刷新，每10秒）
- ✅ 统计卡片（总数、运行中、已完成、失败）
- ✅ 任务详情（执行结果、进度条）
- ✅ 取消任务（已实现）

**特性**:
- 自动刷新（10秒）
- 实时进度条
- 状态标签
- 详细日志

---

## 🔧 集成步骤

### 方式一：添加到现有前端项目

**1. 复制Vue组件**
```bash
# 复制页面文件到你的Vue项目
cp frontend/PackageList.vue your-project/src/views/Software/
cp frontend/TaskMonitor.vue your-project/src/views/Software/
```

**2. 配置路由**
在 `router/index.js` 中添加：

```javascript
{
  path: '/software',
  name: 'Software',
  component: Layout,
  meta: { title: '软件管理', icon: 'box' },
  children: [
    {
      path: 'packages',
      name: 'PackageList',
      component: () => import('@/views/Software/PackageList.vue'),
      meta: { title: '软件包管理', icon: 'document' }
    },
    {
      path: 'tasks',
      name: 'TaskMonitor',
      component: () => import('@/views/Software/TaskMonitor.vue'),
      meta: { title: '任务监控', icon: 'monitor' }
    }
  ]
}
```

**3. 添加菜单项**
在侧边栏配置中添加：

```javascript
{
  name: '软件管理',
  icon: 'box',
  children: [
    { name: '软件包管理', path: '/software/packages' },
    { name: '任务监控', path: '/software/tasks' },
    { name: '策略管理', path: '/software/policies' },
    { name: '合规检查', path: '/software/compliance' }
  ]
}
```

---

### 方式二：独立部署（推荐测试）

**1. 创建测试页面**

创建 `frontend/test.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>软件管理中心</title>
  <link rel="stylesheet" href="//unpkg.com/element-plus/dist/index.css">
  <script src="//unpkg.com/vue@3/dist/vue.global.js"></script>
  <script src="//unpkg.com/element-plus"></script>
  <script src="//unpkg.com/axios/dist/axios.min.js"></script>
</head>
<body>
  <div id="app">
    <el-container style="height: 100vh">
      <el-aside width="200px" style="background-color: #545c64">
        <el-menu
          default-active="packages"
          @select="handleSelect"
          background-color="#545c64"
          text-color="#fff"
          active-text-color="#ffd04b"
        >
          <el-menu-item index="packages">
            <span>软件包管理</span>
          </el-menu-item>
          <el-menu-item index="tasks">
            <span>任务监控</span>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <el-main>
        <h1>{{ currentView === 'packages' ? '软件包管理' : '任务监控' }}</h1>
        <div style="margin-top: 20px; color: #666">
          请在实际Vue项目中集成完整页面组件
        </div>
      </el-main>
    </el-container>
  </div>

  <script>
    const { createApp } = Vue
    const app = createApp({
      data() {
        return {
          currentView: 'packages'
        }
      },
      methods: {
        handleSelect(key) {
          this.currentView = key
        }
      }
    })
    app.use(ElementPlus)
    app.mount('#app')
  </script>
</body>
</html>
```

**2. 启动简单HTTP服务器**
```bash
cd frontend
python -m http.server 5174
# 访问当前开发服务器下的 /test.html
```

---

## 📊 API配置

当前推荐方式是让前端统一请求代理路径，而不是在页面里写死服务地址：

```javascript
// 软件管理接口走 /software-api/api/v1
// 资产接口走 /api/v1
```

代理目标通过 Vite 环境变量配置：
```
VITE_PROXY_SOFTWARE_TARGET=http://127.0.0.1:8081
VITE_PROXY_ASSETS_TARGET=http://127.0.0.1:8080
VITE_PROXY_POLICY_TARGET=http://127.0.0.1:8082
```

如需让独立示例组件直接改用其他网关前缀，也可以额外设置：
```
VITE_SOFTWARE_API_BASE=/software-api/api/v1
VITE_ASSETS_API_BASE=/api/v1
```

---

## 🎨 所需依赖

确保你的Vue项目已安装：

```json
{
  "dependencies": {
    "vue": "^3.4.0",
    "vue-router": "^4.0.0",
    "element-plus": "^2.9.0",
    "axios": "^1.7.0",
    "@element-plus/icons-vue": "^2.3.0"
  }
}
```

安装命令：
```bash
npm install element-plus axios @element-plus/icons-vue
```

---

## 🔗 页面功能对应的API

### PackageList.vue 使用的API:
- `GET /api/v1/software/packages` - 查询软件包
- `POST /api/v1/software/packages/upload` - 上传软件包
- `DELETE /api/v1/software/packages/{id}` - 删除软件包
- `POST /api/v1/software/tasks` - 创建任务
- `GET /api/v1/assets` - 获取资产列表
- `GET /api/v1/groups` - 获取分组列表

### TaskMonitor.vue 使用的API:
- `GET /api/v1/software/tasks` - 任务列表
- `GET /api/v1/software/tasks/{id}` - 任务详情
- `PUT /api/v1/software/tasks/{id}/cancel` - 取消任务

---

## ✨ 特性说明

### 软件包管理页面
1. **搜索和筛选**
   - 按软件名称搜索
   - 按分类筛选（办公/开发/安全/其他）
   - 按状态筛选（可用/已废弃）

2. **上传功能**
   - 支持拖拽上传
   - 自动计算哈希值
   - 表单验证

3. **分发功能**
   - 选择目标（资产/分组/全部）
   - 设置优先级
   - 支持定时执行

### 任务监控页面
1. **实时监控**
   - 每10秒自动刷新
   - 实时进度条
   - 状态颜色标识

2. **统计卡片**
   - 总任务数
   - 运行中任务
   - 完成数量
   - 失败数量

3. **详情查看**
   - 每个终端的执行情况
   - 下载/安装进度
   - 错误日志
   - 执行耗时

---

## 🎯 当前集成状态

### 已接入当前项目的页面:
1. ✅ `PackageRepository.vue` - 软件包仓库
   - 软件包列表
   - 软件包上传
   - 分发任务创建

2. ✅ `TaskManagement.vue` - 任务管理
   - 任务列表
   - 任务详情
   - 取消和重试

3. ✅ `InstalledSoftware.vue` - 已安装软件
   - 已装软件查询
   - 卸载任务创建

4. ✅ `ComplianceManagement.vue` - 软件合规
   - 合规规则
   - 合规扫描
   - 结果查看

5. ✅ `PolicyManagement.vue` - 策略管理
   - 黑白名单规则
   - 安装策略
   - 规则启停

说明：旧版独立示例中的待开发页面已经在当前运行页面 `IT/frontend/src/views/terminal/SoftwareCenter.vue` 中按标签页接入。生产可用性仍需以真实后端、数据库和 Agent 端到端验收为准。

---

## 📝 注意事项

1. **跨域问题**
   - 当前仓库默认依赖 `vite.config.mjs` 里的开发代理
   - 生产环境建议通过同源网关或 Nginx 反向代理统一转发

2. **权限控制**
   - 页面目前无认证
   - 生产环境需添加登录验证
   - 建议集成到现有权限系统

3. **错误处理**
   - 已添加基础错误提示
   - 可根据需要完善

4. **性能优化**
   - 列表使用分页
   - 任务监控自动刷新
   - 可添加虚拟滚动

---

## 🚀 快速测试

**1. 确保服务运行**
```bash
# API服务（端口 8081）
python software_management_api_complete_v2.py

# Agent（如果需要实际执行）
python cmdb_agent_unified_v2.py
```

**2. 集成到前端**
- 复制Vue组件到你的项目
- 配置路由
- 访问页面

**3. 功能验证**
- 上传一个测试软件包
- 创建分发任务
- 在任务监控页面查看进度

---

**版本**: v1.0  
**创建日期**: 2026-06-12  
**技术栈**: Vue 3 + Element Plus + Axios
