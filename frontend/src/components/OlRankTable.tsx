import { useState } from 'react'
import type { OlRankRow } from '../types'
import { getTeamColor, heatColorDiverging, heatTextColor } from '../lib/colors'

type SortKey = keyof OlRankRow

interface ColDef {
  key: SortKey
  label: string
  fmt: (v: string) => string
  heat?: boolean
  // heat cells for *_success_rate columns are 0-1 fractions in the CSV;
  // *_pctile columns are already 0-100 -- scale tells the heatmap which.
  heatScale?: 'fraction' | 'pctile'
}

// left/middle/right_success_rate come out of the CSV as 0-1 fractions
// (fetch_cfbd.DirectionSplit.success_rate) -- multiply by 100 to display
// as a percentage.
function fmtFractionAsPct(v: string) {
  const n = parseFloat(v)
  return isNaN(n) ? '—' : `${(n * 100).toFixed(1)}%`
}

// performance_pctile (and every other *_pctile column) is ALREADY on a
// 0-100 scale (compute_ol_rank.percentile_rank's return value) -- do NOT
// multiply by 100 again, just append the sign. Multiplying here produced
// nonsense values like "9818.8%" for a real 98.2nd-percentile team.
function fmtPctileAsPct(v: string) {
  const n = parseFloat(v)
  return isNaN(n) ? '—' : `${n.toFixed(1)}%`
}

function fmtNum(v: string, decimals = 1) {
  const n = parseFloat(v)
  return isNaN(n) ? '—' : n.toFixed(decimals)
}

const COLS: ColDef[] = [
  { key: 'rank', label: '#', fmt: (v) => v },
  { key: 'team', label: 'Team', fmt: (v) => v },
  { key: 'composite_0_100', label: 'Score', fmt: (v) => fmtNum(v) },
  { key: 'mass_pctile', label: 'Mass', fmt: fmtPctileAsPct, heat: true, heatScale: 'pctile' },
  { key: 'experience_pctile', label: 'Exp', fmt: fmtPctileAsPct, heat: true, heatScale: 'pctile' },
  { key: 'recruiting_pctile', label: 'Recruit', fmt: fmtPctileAsPct, heat: true, heatScale: 'pctile' },
  { key: 'performance_pctile', label: 'Perf', fmt: fmtPctileAsPct, heat: true, heatScale: 'pctile' },
  { key: 'left_success_rate', label: 'L SR', fmt: fmtFractionAsPct, heat: true, heatScale: 'fraction' },
  { key: 'middle_success_rate', label: 'M SR', fmt: fmtFractionAsPct, heat: true, heatScale: 'fraction' },
  { key: 'right_success_rate', label: 'R SR', fmt: fmtFractionAsPct, heat: true, heatScale: 'fraction' },
]

interface Props {
  rows: OlRankRow[]
  onSelectTeam?: (team: string) => void
}

export function OlRankTable({ rows, onSelectTeam }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('rank')
  const [sortAsc, setSortAsc] = useState(true)
  const [query, setQuery] = useState('')

  const filtered = query
    ? rows.filter((r) => r.team.toLowerCase().includes(query.toLowerCase()))
    : rows

  const sorted = [...filtered].sort((a, b) => {
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
    <div>
      <input
        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-gray-300"
        placeholder="Search team…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="overflow-y-auto max-h-[70vh] border border-gray-100 rounded-lg">
        <table className="w-full text-sm table-fixed">
          <thead>
            <tr>
              {COLS.map((c) => (
                <th
                  key={c.key}
                  className={`sticky top-0 z-10 bg-white text-left py-2 px-2 text-[11px] font-semibold uppercase tracking-wider text-gray-500 cursor-pointer select-none whitespace-nowrap hover:text-gray-900 transition-colors border-b border-gray-200${c.key === 'team' ? ' w-[26%]' : ''}`}
                  onClick={() => handleSort(c.key)}
                >
                  {c.label}
                  {sortKey === c.key ? (sortAsc ? ' ↑' : ' ↓') : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row, i) => {
              const teamColor = getTeamColor(row.team)
              return (
                <tr
                  key={i}
                  className={`border-b border-gray-100 hover:bg-gray-50 transition-colors${onSelectTeam ? ' cursor-pointer' : ''}`}
                  onClick={() => onSelectTeam?.(row.team)}
                >
                  {COLS.map((c) => {
                    if (c.key === 'team') {
                      return (
                        <td key={c.key} className="py-1.5 px-2 tabular-nums truncate">
                          <span
                            className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle shrink-0"
                            style={{ background: teamColor }}
                          />
                          {row.team}
                        </td>
                      )
                    }
                    if (c.heat) {
                      const raw = parseFloat(row[c.key])
                      const pctile = c.heatScale === 'fraction' ? raw * 100 : raw
                      return (
                        <td
                          key={c.key}
                          className="py-1.5 px-2 tabular-nums text-xs"
                          style={
                            isNaN(pctile)
                              ? undefined
                              : { backgroundColor: heatColorDiverging(pctile), color: heatTextColor(pctile) }
                          }
                        >
                          {c.fmt(row[c.key])}
                        </td>
                      )
                    }
                    return (
                      <td key={c.key} className="py-1.5 px-2 tabular-nums text-xs">
                        {c.fmt(row[c.key])}
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
