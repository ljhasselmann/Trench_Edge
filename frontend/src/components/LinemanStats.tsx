import { useState } from 'react'
import type { LinemanStat } from '../types'

interface Props {
  rows: LinemanStat[]
  filterTeam?: string | null
}

export function LinemanStats({ rows, filterTeam }: Props) {
  const [query, setQuery] = useState('')

  const filtered = rows.filter((r) => {
    if (filterTeam && r.team !== filterTeam) return false
    if (!query) return true
    const q = query.toLowerCase()
    return r.name.toLowerCase().includes(q) || r.team.toLowerCase().includes(q)
  })

  return (
    <div>
      <input
        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-gray-300"
        placeholder="Search player or team…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-200">
              {['Team', 'Name', 'Pos', '#', 'Class', 'Wt (lbs)', 'Snaps', 'Rating', 'Stars'].map(
                (h) => (
                  <th
                    key={h}
                    className="text-left py-2 px-3 text-xs font-semibold uppercase tracking-wider text-gray-500 whitespace-nowrap"
                  >
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {filtered.map((r, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                <td className="py-2 px-3 whitespace-nowrap">{r.team}</td>
                <td className="py-2 px-3 font-medium whitespace-nowrap">{r.name}</td>
                <td className="py-2 px-3 font-mono text-xs">{r.position_tag || '—'}</td>
                <td className="py-2 px-3 font-mono tabular-nums">{r.jersey || '—'}</td>
                <td className="py-2 px-3">{r.class_year || '—'}</td>
                <td className="py-2 px-3 tabular-nums">{r.weight_lbs || '—'}</td>
                <td className="py-2 px-3 tabular-nums">{r.snaps_multi_year || '—'}</td>
                <td className="py-2 px-3 tabular-nums">{r.recruit_rating || '—'}</td>
                <td className="py-2 px-3 tabular-nums">{r.recruit_stars || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <p className="text-sm text-gray-400 text-center py-8">No results.</p>
        )}
      </div>
    </div>
  )
}
