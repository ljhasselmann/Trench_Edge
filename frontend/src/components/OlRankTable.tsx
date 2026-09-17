import { useState } from 'react'
import type { OlRankRow } from '../types'

type SortKey = keyof OlRankRow

interface ColDef {
  key: SortKey
  label: string
  fmt: (v: string) => string
}

function fmtPct(v: string) {
  const n = parseFloat(v)
  return isNaN(n) ? '—' : `${(n * 100).toFixed(1)}%`
}

function fmtNum(v: string, decimals = 1) {
  const n = parseFloat(v)
  return isNaN(n) ? '—' : n.toFixed(decimals)
}

const COLS: ColDef[] = [
  { key: 'rank', label: '#', fmt: (v) => v },
  { key: 'team', label: 'Team', fmt: (v) => v },
  { key: 'composite_0_100', label: 'Score', fmt: (v) => fmtNum(v) },
  { key: 'performance_pctile', label: 'Perf %ile', fmt: fmtPct },
  { key: 'left_success_rate', label: 'Left SR', fmt: fmtPct },
  { key: 'middle_success_rate', label: 'Mid SR', fmt: fmtPct },
  { key: 'right_success_rate', label: 'Right SR', fmt: fmtPct },
]

interface Props {
  rows: OlRankRow[]
}

export function OlRankTable({ rows }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('rank')
  const [sortAsc, setSortAsc] = useState(true)

  const sorted = [...rows].sort((a, b) => {
    const va = parseFloat(a[sortKey]) || 0
    const vb = parseFloat(b[sortKey]) || 0
    return sortAsc ? va - vb : vb - va
  })

  const handleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc((prev) => !prev)
    } else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200">
            {COLS.map((c) => (
              <th
                key={c.key}
                className="text-left py-2 px-3 text-xs font-semibold uppercase tracking-wider text-gray-500 cursor-pointer select-none whitespace-nowrap hover:text-gray-900 transition-colors"
                onClick={() => handleSort(c.key)}
              >
                {c.label}
                {sortKey === c.key ? (sortAsc ? ' ↑' : ' ↓') : ''}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr key={i} className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
              {COLS.map((c) => (
                <td key={c.key} className="py-2 px-3 tabular-nums">
                  {c.fmt(row[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
