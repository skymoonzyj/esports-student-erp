import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ItemRankingPanel } from './ItemRankingPanel'
import type { ItemRankingView } from './itemContracts'

const view: ItemRankingView = {
  status: 'ranked', reason: null, group_label: '同规则鼠标组', calculated_at: '2026-10-31T02:03:04Z', participant_count: 5,
  metrics: [{ key: 'score', label: '评分', unit: '分', better: 'higher', decimals: 0 }],
  current: { rank: 4, values: { score: 70 } }, best: { rank: 1, values: { score: 95 } },
  top_three: [
    { rank: 1, values: { score: 95 }, is_self: true, tie_count: 1, gaps: [{ key: 'score', amount: 25, state: 'behind' }] },
    { rank: 2, values: { score: 90 }, is_self: false, tie_count: 3, gaps: [{ key: 'score', amount: 20, state: 'behind' }] },
    { rank: 2, values: { score: 90 }, is_self: false, tie_count: 3, gaps: [{ key: 'score', amount: 20, state: 'behind' }] },
  ],
}

it('shows server current/best ranks, anonymous best top rows, gaps and truncated score ties', () => {
  render(<ItemRankingPanel view={view} loading={false} error={null} onRetry={vi.fn()} />)
  expect(screen.getByRole('heading', { name: '当前数据库排名' })).toBeVisible()
  expect(screen.getByText(/本次成绩：第 4 名/)).toHaveTextContent('70 分')
  expect(screen.getByText(/个人历史最佳：第 1 名/)).toHaveTextContent('95 分')
  const table = screen.getByRole('table', { name: '同组历史最佳前三' })
  expect(within(table).getAllByRole('row')).toHaveLength(4)
  expect(within(table).getByText('本人最佳')).toBeVisible()
  expect(within(table).getAllByText('匿名参测者')).toHaveLength(2)
  expect(within(table).getByText('本次落后 25 分')).toBeVisible()
  expect(screen.getByText(/第 2 名共 3 人同分/)).toHaveTextContent('仅展示 3 条')
  expect(screen.getByText(/1、1、3/)).toBeVisible()
  expect(screen.getByText(/计算时间/).querySelector('time')).toHaveAttribute('dateTime', view.calculated_at)
  expect(screen.getByText(/计算时间/)).toHaveTextContent('2026-10-31 10:03')
})

it('renders metric catalog and server gap direction without recalculating lower-is-better values', () => {
  const lower: ItemRankingView = { ...view, metrics: [{ key: 'time', label: '用时', unit: '毫秒', decimals: 1, better: 'lower' }],
    participant_count: 1, current: { rank: 1, values: { time: 110.25 } }, best: { rank: 1, values: { time: 100 } },
    top_three: [{ rank: 1, values: { time: 100 }, is_self: true, tie_count: 1, gaps: [{ key: 'time', amount: 10.25, state: 'behind' }] }],
  }
  render(<ItemRankingPanel view={lower} loading={false} error={null} onRetry={vi.fn()} />)
  expect(screen.getByText('本次落后 10.3 毫秒')).toBeVisible()
  expect(screen.getByText(/用时.*110.3 毫秒/)).toBeVisible()
})

it('does not invent an empty board during loading, errors or ineligible states and offers retry', () => {
  const retry = vi.fn()
  const page = render(<ItemRankingPanel view={null} loading error={null} onRetry={retry} />)
  expect(screen.getByRole('status')).toHaveTextContent('正在读取单项排名')
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  page.rerender(<ItemRankingPanel view={view} loading={false} error={new Error('private diagnostic')} onRetry={retry} />)
  expect(screen.getByRole('alert')).toHaveTextContent('单项排名暂不可用')
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.queryByText('private diagnostic')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '重试单项排名' }))
  expect(retry).toHaveBeenCalledOnce()
  page.rerender(<ItemRankingPanel view={{ ...view, status: 'not_eligible', reason: 'quality_warning', participant_count: 0, current: null, best: null, top_three: [] }} loading={false} error={null} onRetry={retry} />)
  expect(screen.getByText(/本项成绩存在质量提示/)).toBeVisible()
  expect(screen.queryByRole('table')).not.toBeInTheDocument()
  expect(screen.queryByText(/第 \d+ 名/)).not.toBeInTheDocument()
})
