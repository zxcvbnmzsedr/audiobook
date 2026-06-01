import {
  App as AntApp,
  Badge,
  Button,
  Card,
  ConfigProvider,
  Layout,
  Menu,
  Space,
  Spin,
  Typography,
} from 'antd'
import type { MenuProps } from 'antd'
import {
  BookOutlined,
  HomeOutlined,
  AppstoreOutlined,
  ReloadOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import zhCN from 'antd/locale/zh_CN'
import { lazy, Suspense } from 'react'
import { useWorkspaceStore } from './store'
import { theme } from './theme'
import { useBooks } from './hooks'
import { api } from './api'
import type { TaskStatus } from './types'

const LibraryPage = lazy(() => import('./pages/LibraryPage').then((module) => ({ default: module.LibraryPage })))
const ModulesPage = lazy(() => import('./pages/ModulesPage').then((module) => ({ default: module.ModulesPage })))
const SettingsPage = lazy(() => import('./pages/SettingsPage').then((module) => ({ default: module.SettingsPage })))
const WorkspacePage = lazy(() => import('./pages/WorkspacePage').then((module) => ({ default: module.WorkspacePage })))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

type GlobalTask = {
  key: string
  label: string
  task: string
  step: 'script' | 'result'
}

const globalTasks: GlobalTask[] = [
  { key: 'script', label: '脚本', task: 'script', step: 'script' },
  { key: 'review', label: '审校', task: 'review', step: 'script' },
  { key: 'audio', label: '音频', task: 'audio', step: 'result' },
  { key: 'audacity', label: 'Audacity', task: 'audacity_export', step: 'result' },
  { key: 'm4b', label: 'M4B', task: 'm4b_export', step: 'result' },
]

function taskBadgeStatus(status?: TaskStatus) {
  if (status?.running) return 'processing' as const
  if (status?.cancel) return 'warning' as const
  return 'default' as const
}

function GlobalTaskStatus({ bookId }: { bookId?: string | null }) {
  return (
    <Space wrap size={4} className="global-task-strip">
      {globalTasks.map((task) => <GlobalTaskChip key={task.key} task={task} bookId={bookId} />)}
    </Space>
  )
}

function GlobalTaskChip({ task, bookId }: { task: GlobalTask; bookId?: string | null }) {
  const navigate = useNavigate()
  const query = useQuery({
    queryKey: ['task-status', task.task],
    queryFn: () => api.taskStatus(task.task),
    enabled: !!bookId,
    refetchInterval: (currentQuery) => currentQuery.state.data?.running ? 1500 : 15000,
  })
  const status = query.data
  const running = !!status?.running
  const latestLog = status?.logs?.at(-1)

  return (
    <Button
      type="text"
      size="small"
      className={running ? 'global-task-chip active' : 'global-task-chip'}
      disabled={!bookId}
      title={latestLog ?? `${task.label}${running ? '运行中' : '空闲'}`}
      onClick={() => bookId && navigate(`/books/${encodeURIComponent(bookId)}/${task.step}`)}
    >
      <Badge status={taskBadgeStatus(status)} text={`${task.label}${running ? '运行中' : '空闲'}`} />
    </Button>
  )
}

function AppFrame() {
  const location = useLocation()
  const navigate = useNavigate()
  const booksQuery = useBooks()
  const currentBook = useWorkspaceStore((state) => state.currentBook)
  const selectedMenu = location.pathname.startsWith('/settings')
    ? '/settings'
    : location.pathname.startsWith('/modules')
      ? '/modules'
    : location.pathname.startsWith('/books')
      ? '/library'
      : '/library'

  const items: MenuProps['items'] = [
    { key: '/library', icon: <HomeOutlined />, label: '书库' },
    { key: '/modules', icon: <AppstoreOutlined />, label: '能力中心' },
    { key: '/settings', icon: <SettingOutlined />, label: '设置' },
  ]

  return (
    <Layout className="app-shell">
      <Sider width={232} breakpoint="lg" collapsedWidth={0}>
        <div className="brand">
            <BookOutlined className="brand-icon" />
          <div>
            <div className="brand-title">Voc Studio</div>
            <div className="brand-subtitle">Voice Creation Suite</div>
          </div>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedMenu]}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
        <div className="sider-footer">
          <Text type="secondary">当前书籍</Text>
          <div className="sider-book">{currentBook?.title ?? '未选择'}</div>
        </div>
      </Sider>
      <Layout>
        <Header className="topbar">
          <Space direction="vertical" size={0}>
            <Text type="secondary">Voc Studio</Text>
            <Title level={4} className="topbar-title">
              {currentBook?.title ?? '有声书工作台'}
            </Title>
          </Space>
          <Space>
            <GlobalTaskStatus bookId={currentBook?.id} />
            <Badge
              status={booksQuery.isFetching ? 'processing' : currentBook ? 'success' : 'default'}
              text={currentBook ? '已连接项目' : '等待选择'}
            />
            <Button icon={<ReloadOutlined />} onClick={() => booksQuery.refetch()}>
              刷新
            </Button>
          </Space>
        </Header>
        <Content className="content">
          <Suspense fallback={<PageLoading />}>
            <Routes>
              <Route path="/" element={<Navigate to="/library" replace />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/modules" element={<ModulesPage />} />
              <Route path="/books/:bookId/:step" element={<WorkspacePage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/library" replace />} />
            </Routes>
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  )
}

function PageLoading() {
  return (
    <Card>
      <Spin />
    </Card>
  )
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={theme}>
        <AntApp>
          <AppFrame />
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  )
}
