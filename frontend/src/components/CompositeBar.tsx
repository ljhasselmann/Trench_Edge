import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  ReferenceLine,
  Tooltip,
  ResponsiveContainer,
  LabelList,
} from 'recharts'
import type { MatchupScore } from '../types'

interface Props {
  matchup: MatchupScore
}

function parseScore(s: string): number | null {
  if (s === '' || s == null) return null
  const n = parseFloat(s)
  return isNaN(n) ? null : n
}

export function CompositeBar({ matchup }: Props) {
  const olColor = matchup.team_ol_color || '#2a78d6'
  const dlColor = matchup.team_dl_color || '#e34948'
  const score = parseScore(matchup.composite_score)
  const data = [{ label: 'Trench Edge', value: score }]
  const verdictColor = score != null && score >= 0 ? olColor : dlColor

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
      <ResponsiveContainer width="100%" height={52}>
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 0, right: 56, left: 88, bottom: 0 }}
          barCategoryGap="30%"
        >
          <XAxis type="number" domain={[-10, 10]} hide />
          <YAxis
            type="category"
            dataKey="label"
            width={88}
            tick={{ fontSize: 13, fontWeight: 700, fill: '#111318' }}
            axisLine={false}
            tickLine={false}
          />
          <ReferenceLine x={0} stroke="#9a9ea5" strokeWidth={1} />
          <Tooltip
            formatter={(v: unknown) => {
              const n = typeof v === 'number' ? v : parseFloat(String(v))
              if (isNaN(n)) return ['N/A', 'Composite']
              return [`${n > 0 ? '+' : ''}${n.toFixed(1)}`, 'Composite']
            }}
            contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #e6e5e1' }}
          />
          <Bar dataKey="value" radius={[0, 3, 3, 0]} maxBarSize={20}>
            <Cell fill={score == null ? '#edece8' : score >= 0 ? olColor : dlColor} />
            <LabelList
              dataKey="value"
              position="right"
              formatter={(v: unknown) => {
                if (v == null || v === '') return '—'
                const n = typeof v === 'number' ? v : parseFloat(String(v))
                return isNaN(n) ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(1)}`
              }}
              style={{ fontSize: 13, fontWeight: 700 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      {matchup.composite_verdict && (
        <div
          className="inline-flex items-center text-xs font-semibold px-2.5 py-1 rounded-full border mt-3"
          style={{ color: verdictColor, borderColor: verdictColor }}
        >
          {matchup.composite_verdict}
        </div>
      )}
    </div>
  )
}
