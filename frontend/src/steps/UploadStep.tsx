import {
  App as AntApp,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Form,
  Input,
  Row,
  Space,
  Spin,
  Statistic,
  Table,
  Typography,
  Upload,
} from 'antd'
import type { UploadProps } from 'antd'
import { CloudUploadOutlined, HighlightOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import { api } from '../api'
import type { Chapter } from '../types'
import { formatCount, formatDate } from '../utils'

const { Paragraph } = Typography

export function UploadStep() {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [editingChapterId, setEditingChapterId] = useState<string | null>(null)
  const [appendPreview, setAppendPreview] = useState<Record<string, unknown> | null>(null)
  const chaptersQuery = useQuery({ queryKey: ['chapters'], queryFn: api.chapters })
  const refreshAfterChapterMutation = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['books'] }),
      queryClient.invalidateQueries({ queryKey: ['chapters'] }),
      queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
      queryClient.invalidateQueries({ queryKey: ['chunks'] }),
    ])
  }
  const uploadMutation = useMutation({
    mutationFn: api.uploadSource,
    onSuccess: async () => {
      message.success('源稿已上传并拆章')
      await refreshAfterChapterMutation()
    },
    onError: (error: Error) => message.error(error.message),
  })
  const appendMutation = useMutation({
    mutationFn: ({ file, dryRun }: { file: File; dryRun: boolean }) => api.appendChapters(file, dryRun),
    onSuccess: async (result, variables) => {
      if (variables.dryRun) {
        setAppendPreview(result as Record<string, unknown>)
        const appendCount = Number((result as Record<string, unknown>).append_count ?? 0)
        modal.confirm({
          title: `预计追加 ${appendCount} 章`,
          content: '确认后会追加新章节，现有脚本、片段和音频会尽量保留。',
          okText: '确认追加',
          cancelText: '取消',
          onOk: () => appendMutation.mutate({ file: variables.file, dryRun: false }),
        })
        return
      }
      message.success('章节已追加')
      setAppendPreview(result as Record<string, unknown>)
      await refreshAfterChapterMutation()
    },
    onError: (error: Error) => message.error(error.message),
  })
  const resplitMutation = useMutation({
    mutationFn: (confirmInvalidate: boolean) => api.resplitChapters(confirmInvalidate
      ? { dry_run: false, confirm_invalidate: true }
      : { dry_run: true }),
    onSuccess: async (result, confirmInvalidate) => {
      if (!confirmInvalidate) {
        const payload = result as Record<string, unknown>
        const preview = (payload.preview ?? {}) as Record<string, unknown>
        modal.confirm({
          title: '按当前规则重新拆章？',
          content: `当前 ${formatCount(payload.current_chapter_count as number | undefined)} 章，预览 ${formatCount(preview.chapter_count as number | undefined)} 章。确认后会清理相关生成状态。`,
          okText: '重新拆章',
          cancelText: '取消',
          onOk: () => resplitMutation.mutate(true),
        })
        return
      }
      message.success('章节已重新拆分')
      await refreshAfterChapterMutation()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const uploadProps: UploadProps = {
    accept: '.txt,.md',
    maxCount: 1,
    showUploadList: false,
    beforeUpload: (file) => {
      uploadMutation.mutate(file)
      return false
    },
  }

  const chapters = chaptersQuery.data?.chapters ?? []

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={9}>
        <Card title="源稿上传">
          <Space direction="vertical" size={16} className="full-width">
            <Upload.Dragger {...uploadProps} disabled={uploadMutation.isPending}>
              <p className="ant-upload-drag-icon"><CloudUploadOutlined /></p>
              <p className="ant-upload-text">点击或拖拽 TXT/Markdown 源稿到这里</p>
              <p className="ant-upload-hint">上传后后端会写入当前书籍并生成章节清单。</p>
            </Upload.Dragger>
            <Button loading={chaptersQuery.isFetching} icon={<ReloadOutlined />} onClick={() => chaptersQuery.refetch()}>
              重新读取章节
            </Button>
            <Upload
              accept=".txt,.md"
              showUploadList={false}
              beforeUpload={(file) => {
                appendMutation.mutate({ file, dryRun: true })
                return false
              }}
            >
              <Button icon={<PlusOutlined />} loading={appendMutation.isPending}>
                追加新章节
              </Button>
            </Upload>
            <Button danger loading={resplitMutation.isPending} onClick={() => resplitMutation.mutate(false)}>
              重新拆章
            </Button>
            {appendPreview ? (
              <Card size="small" title="追加预览">
                <Descriptions size="small" column={1}>
                  <Descriptions.Item label="模式">{String(appendPreview.append_mode ?? '-')}</Descriptions.Item>
                  <Descriptions.Item label="现有章节">{formatCount(appendPreview.current_chapter_count as number | undefined)}</Descriptions.Item>
                  <Descriptions.Item label="输入章节">{formatCount(appendPreview.incoming_chapter_count as number | undefined)}</Descriptions.Item>
                  <Descriptions.Item label="追加章节">{formatCount(appendPreview.append_count as number | undefined)}</Descriptions.Item>
                </Descriptions>
              </Card>
            ) : null}
          </Space>
        </Card>
      </Col>
      <Col xs={24} lg={15}>
        <Card title="章节概览">
          <Row gutter={12} className="metric-row">
            <Col span={8}><Statistic title="章节数" value={chaptersQuery.data?.chapter_count ?? chapters.length} /></Col>
            <Col span={8}><Statistic title="总字数" value={chaptersQuery.data?.total_chars ?? 0} /></Col>
            <Col span={8}><Statistic title="更新时间" value={formatDate(chaptersQuery.data?.generated_at)} /></Col>
          </Row>
          <Table<Chapter>
            size="small"
            rowKey={(record, index) => record.chapter_id ?? String(index)}
            loading={chaptersQuery.isLoading}
            dataSource={chapters}
            pagination={{ pageSize: 8 }}
            columns={[
              { title: '#', dataIndex: 'index', width: 72 },
              { title: '标题', render: (_, record) => record.title ?? record.chapter_title ?? record.chapter_id },
              { title: '字数', dataIndex: 'char_count', width: 120, render: formatCount },
              {
                title: '操作',
                width: 90,
                render: (_, record) => (
                  <Button size="small" onClick={() => setEditingChapterId(record.chapter_id ?? record.id ?? null)}>
                    编辑
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      </Col>
      <ChapterDrawer
        chapterId={editingChapterId}
        onClose={() => setEditingChapterId(null)}
        onMutated={async () => {
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ['chapters'] }),
            queryClient.invalidateQueries({ queryKey: ['books'] }),
            queryClient.invalidateQueries({ queryKey: ['script-progress'] }),
            queryClient.invalidateQueries({ queryKey: ['chunks'] }),
          ])
        }}
      />
    </Row>
  )
}

function ChapterDrawer({ chapterId, onClose, onMutated }: {
  chapterId: string | null
  onClose: () => void
  onMutated: () => Promise<void>
}) {
  const { message, modal } = AntApp.useApp()
  const queryClient = useQueryClient()
  const [form] = Form.useForm<{ title: string; content: string; splitTitle: string }>()
  const contentRef = useRef<TextAreaRef>(null)
  const chapterQuery = useQuery({
    queryKey: ['chapter', chapterId],
    queryFn: () => api.chapter(chapterId ?? ''),
    enabled: !!chapterId,
  })

  useEffect(() => {
    if (chapterQuery.data) {
      form.setFieldsValue({
        title: chapterQuery.data.title ?? chapterQuery.data.chapter_title ?? '',
        content: chapterQuery.data.content ?? '',
        splitTitle: '',
      })
    }
  }, [chapterQuery.data, form])

  const saveMutation = useMutation({
    mutationFn: (values: { title: string; content: string }) => api.updateChapter(chapterId ?? '', values),
    onSuccess: async () => {
      message.success('章节已保存，相关生成产物已按后端规则失效')
      await onMutated()
      await chapterQuery.refetch()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const splitMutation = useMutation({
    mutationFn: (values: { title: string; content: string; splitTitle: string; splitAt: number }) => {
      const content = values.content ?? ''
      return api.splitChapter(chapterId ?? '', {
        split_at: values.splitAt,
        title: values.title,
        new_title: values.splitTitle,
        content_before: content.slice(0, values.splitAt),
        content_after: content.slice(values.splitAt),
      })
    },
    onSuccess: async () => {
      message.success('章节已拆分')
      await onMutated()
      onClose()
    },
    onError: (error: Error) => message.error(error.message),
  })

  const mergeMutation = useMutation({
    mutationFn: (values: { title: string; content: string }) => api.mergeChapterNext(chapterId ?? '', values),
    onSuccess: async () => {
      message.success('已合并下一章')
      await onMutated()
      await chapterQuery.refetch()
    },
    onError: (error: Error) => message.error(error.message),
  })
  const rerunMutation = useMutation({
    mutationFn: (reuseCharacterBook: boolean) => api.generateScript({
      mode: 'script',
      chapter_ids: [chapterId],
      reuse_character_book: reuseCharacterBook,
    }),
    onSuccess: async () => {
      message.success('本章脚本重跑已启动')
      await queryClient.invalidateQueries({ queryKey: ['task-status', 'script'] })
    },
    onError: (error: Error) => message.error(error.message),
  })

  const chapter = chapterQuery.data
  const nextTitle = chapter?.next_chapter?.chapter_title
  const splitFromCursor = () => {
    const values = form.getFieldsValue()
    const content = values.content ?? ''
    const textarea = contentRef.current?.resizableTextArea?.textArea
    const splitAt = typeof textarea?.selectionStart === 'number' ? textarea.selectionStart : -1

    if (splitAt <= 0 || splitAt >= content.length) {
      message.warning('请先在正文中放置光标，光标不能在开头或结尾')
      return
    }

    modal.confirm({
      title: '从光标拆分章节？',
      content: chapter?.needs_regeneration
        ? '拆分后当前章节已有的脚本、片段和音频会被清理。'
        : '拆分后当前章节的脚本、片段和音频会按后端规则失效。',
      okText: '拆分',
      cancelText: '取消',
      onOk: () => splitMutation.mutate({ ...values, splitAt }),
    })
  }

  return (
    <Drawer open={!!chapterId} title="章节编辑" width={760} onClose={onClose}>
      <Spin spinning={chapterQuery.isLoading}>
        <Form form={form} layout="vertical" onFinish={(values) => saveMutation.mutate(values)}>
          <Form.Item name="title" label="章节标题" rules={[{ required: true, message: '请输入章节标题' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="content" label="正文" rules={[{ required: true, message: '请输入正文' }]}>
            <Input.TextArea ref={contentRef} rows={18} className="mono textarea-editor" />
          </Form.Item>
          <Form.Item name="splitTitle" label="拆分后新章节标题">
            <Input placeholder="留空则自动使用「原标题（下）」" />
          </Form.Item>
          <Space wrap>
            <Button type="primary" icon={<SaveOutlined />} loading={saveMutation.isPending} onClick={() => form.submit()}>
              保存章节
            </Button>
            <Button
              icon={<HighlightOutlined />}
              loading={splitMutation.isPending}
              onClick={splitFromCursor}
            >
              从光标拆分
            </Button>
            <Button
              disabled={!chapter?.next_chapter}
              loading={mergeMutation.isPending}
              onClick={() => {
                modal.confirm({
                  title: nextTitle ? `合并下一章「${nextTitle}」？` : '合并下一章？',
                  content: '合并会让当前章节及下一章相关脚本、片段和音频失效。',
                  okText: '合并',
                  cancelText: '取消',
                  onOk: () => mergeMutation.mutate(form.getFieldsValue()),
                })
              }}
            >
              合并下一章
            </Button>
            <Button loading={rerunMutation.isPending} onClick={() => rerunMutation.mutate(false)}>
              重跑本章脚本
            </Button>
            <Button loading={rerunMutation.isPending} onClick={() => rerunMutation.mutate(true)}>
              复用人物池重跑
            </Button>
          </Space>
          {chapter?.needs_regeneration ? (
            <Paragraph type="secondary" className="mt-16">
              当前章节已有生成产物，保存、拆分或合并后需要重新生成脚本和音频。
            </Paragraph>
          ) : null}
        </Form>
      </Spin>
    </Drawer>
  )
}
