import {
  App as AntApp,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Flex,
  Form,
  Input,
  Modal,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { BookOutlined, DeleteOutlined, FolderOpenOutlined, PlusOutlined, ReloadOutlined, SaveOutlined, UploadOutlined } from '@ant-design/icons'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useBooks } from '../hooks'
import { formatCount, formatDate } from '../utils'

const { Title, Text } = Typography

export function LibraryPage() {
  const { message, modal } = AntApp.useApp()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const booksQuery = useBooks()
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm<{ title: string }>()

  const createMutation = useMutation({
    mutationFn: (title: string) => api.createBook(title),
    onSuccess: async (result) => {
      message.success('书籍已创建')
      setCreateOpen(false)
      form.resetFields()
      await queryClient.invalidateQueries({ queryKey: ['books'] })
      navigate(`/books/${encodeURIComponent(result.book.id)}/upload`)
    },
    onError: (error: Error) => message.error(error.message),
  })

  const selectMutation = useMutation({
    mutationFn: api.selectBook,
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['books'] })
      navigate(`/books/${encodeURIComponent(result.book.id)}/upload`)
    },
    onError: (error: Error) => message.error(error.message),
  })

  const deleteMutation = useMutation({
    mutationFn: api.deleteBook,
    onSuccess: async () => {
      message.success('书籍已删除')
      await queryClient.invalidateQueries({ queryKey: ['books'] })
    },
    onError: (error: Error) => message.error(error.message),
  })
  const importConfigMutation = useMutation({
    mutationFn: ({ bookId, file }: { bookId: string; file: File }) => api.importBookConfig(bookId, file),
    onSuccess: async () => {
      message.success('书籍配置已导入')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['books'] }),
        queryClient.invalidateQueries({ queryKey: ['chapters'] }),
        queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
        queryClient.invalidateQueries({ queryKey: ['voices'] }),
        queryClient.invalidateQueries({ queryKey: ['characters'] }),
        queryClient.invalidateQueries({ queryKey: ['chunks'] }),
      ])
    },
    onError: (error: Error) => message.error(error.message),
  })

  const data = booksQuery.data
  const books = data?.books ?? []
  const currentBookId = data?.current_book_id
  const totalChapters = books.reduce((sum, book) => sum + (book.chapter_count ?? 0), 0)
  const totalChars = books.reduce((sum, book) => sum + (book.char_count ?? 0), 0)

  return (
    <Space direction="vertical" size={16} className="page-stack">
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card>
            <Flex justify="space-between" align="flex-start" gap={16} wrap>
              <Space direction="vertical" size={4}>
                <Title level={3} className="section-title">书库</Title>
                <Text type="secondary">选择一本书进入工作区，或创建新项目开始拆章与生成。</Text>
              </Space>
              <Space wrap>
                <Button icon={<ReloadOutlined />} onClick={() => booksQuery.refetch()}>
                  刷新
                </Button>
                <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                  新建书籍
                </Button>
              </Space>
            </Flex>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card>
            <Row gutter={12}>
              <Col span={8}><Statistic title="书籍" value={books.length} /></Col>
              <Col span={8}><Statistic title="章节" value={totalChapters} /></Col>
              <Col span={8}><Statistic title="字数" value={totalChars} /></Col>
            </Row>
          </Card>
        </Col>
      </Row>

      <Spin spinning={booksQuery.isLoading}>
        {books.length ? (
          <Row gutter={[16, 16]}>
            {books.map((book) => (
              <Col key={book.id} xs={24} md={12} xl={8}>
                <Card
                  className={book.id === currentBookId ? 'book-card active' : 'book-card'}
                  title={
                    <Space>
                      <BookOutlined />
                      <span className="book-title">{book.title}</span>
                    </Space>
                  }
                  extra={book.id === currentBookId ? <Tag color="green">当前</Tag> : null}
                  actions={[
                    <Button
                      key="open"
                      type="text"
                      icon={<FolderOpenOutlined />}
                      loading={selectMutation.isPending}
                      onClick={() => selectMutation.mutate(book.id)}
                    >
                      进入
                    </Button>,
                    <Button
                      key="export"
                      type="text"
                      icon={<SaveOutlined />}
                      onClick={() => window.open(api.exportBookConfigUrl(book.id), '_blank')}
                    >
                      导出
                    </Button>,
                    <Upload
                      key="import"
                      accept=".zip"
                      showUploadList={false}
                      beforeUpload={(file) => {
                        modal.confirm({
                          title: `导入配置到「${book.title}」？`,
                          content: '导入会覆盖该书籍的脚本、人物池、声音配置和章节相关状态。',
                          okText: '导入',
                          cancelText: '取消',
                          onOk: () => importConfigMutation.mutate({ bookId: book.id, file }),
                        })
                        return false
                      }}
                    >
                      <Button type="text" icon={<UploadOutlined />} loading={importConfigMutation.isPending}>
                        导入
                      </Button>
                    </Upload>,
                    <Button
                      key="delete"
                      type="text"
                      danger
                      icon={<DeleteOutlined />}
                      loading={deleteMutation.isPending}
                        onClick={() => {
                          modal.confirm({
                            title: `删除「${book.title}」？`,
                            content: '这会删除该书籍目录和已生成文件。',
                            okText: '删除',
                            okButtonProps: { danger: true },
                            cancelText: '取消',
                            onOk: () => deleteMutation.mutate(book.id),
                          })
                        }}
                    >
                      删除
                    </Button>,
                  ]}
                >
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="源稿">{book.source_filename ?? '未上传'}</Descriptions.Item>
                    <Descriptions.Item label="章节">{formatCount(book.chapter_count)}</Descriptions.Item>
                    <Descriptions.Item label="字数">{formatCount(book.char_count)}</Descriptions.Item>
                    <Descriptions.Item label="更新">{formatDate(book.updated_at)}</Descriptions.Item>
                  </Descriptions>
                </Card>
              </Col>
            ))}
          </Row>
        ) : (
          <Card>
            <Empty description="还没有书籍">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
                新建书籍
              </Button>
            </Empty>
          </Card>
        )}
      </Spin>

      <Modal
        open={createOpen}
        title="新建书籍"
        okText="创建"
        cancelText="取消"
        confirmLoading={createMutation.isPending}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()}
      >
        <Form form={form} layout="vertical" onFinish={({ title }) => createMutation.mutate(title)}>
          <Form.Item name="title" label="书名" rules={[{ required: true, message: '请输入书名' }]}>
            <Input autoFocus placeholder="例如：他没" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
