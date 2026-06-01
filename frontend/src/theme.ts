import type { ThemeConfig } from 'antd'

export const theme: ThemeConfig = {
  token: {
    colorPrimary: '#2563eb',
    colorInfo: '#2563eb',
    colorSuccess: '#16a34a',
    colorWarning: '#f59e0b',
    colorError: '#dc2626',
    colorText: '#18202f',
    colorTextSecondary: '#64748b',
    colorBgLayout: '#f4f6f8',
    colorBorder: '#dbe2ea',
    borderRadius: 6,
    fontSize: 14,
    wireframe: false,
  },
  components: {
    Layout: {
      headerBg: '#0f172a',
      siderBg: '#111827',
      triggerBg: '#111827',
    },
    Menu: {
      darkItemBg: '#111827',
      darkSubMenuItemBg: '#111827',
      darkItemSelectedBg: '#2563eb',
    },
    Card: {
      borderRadiusLG: 8,
      headerBg: '#ffffff',
    },
    Table: {
      headerBg: '#f8fafc',
      rowHoverBg: '#f8fafc',
    },
    Button: {
      borderRadius: 6,
    },
    Tabs: {
      itemSelectedColor: '#2563eb',
    },
  },
}
