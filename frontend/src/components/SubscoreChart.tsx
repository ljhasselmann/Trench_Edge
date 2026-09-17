import {
  BarChart,
  Bar,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  ReferenceLine,
  Tooltip,
  ResponsiveContainer,
  LabelList,
} from 'recharts'
import type { MatchupScore } from '../types'

interface SubscoreRow {
  label: string
  value: number | null
}

interface Props {
  matchup: MatchupScore
}

function parseScore(s: string): number | null {
  if (s === '' || s == null) return null
  const n = parseFloat(s)
  return isNaN(n) ? null : n
}

export function SubscoreChart({ matchup }: Props) {
  const rows: SubscoreRow[] = [
    { label: 'Mass', value: parseScore(matchup.mass_score) },
    { label: 'Push', value: parseScore(matchup.push_score) },
    { label: 'Experience', value: parseScore(matchup.experience_score) },
    { label: 'Recruiting', value: parseScore(matchup.recruiting_score) },
  ]

  const olColor = matchup.team_ol_color || '#2a78d6'
  const dlColor = matchup.team_dl_color || '#e34948'

  const formatTooltip = (value: unknown, _name: string, props: { payload?: SubscoreRow }) => {
    const row = props.payload
    if (!row || row.value == null) return ['N/A', row?.label ?? '']
    const v = row.value
    const sign = v > 0 ? '+' : ''
    const favors = v > 0 ? matchup.team_ol : matchup.team_dl
    return [`${sign}${v.toFixed(1)} — favors ${favors}`, row.label]
  }

  return (
    <div className="w-full">
      <div className="flex gap-4 text-xs text-gray-500 mb-3">
        <span>
          <span
            className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
            style={{ background: olColor }}
          />
          Favors {matchup.team_ol}
        </span>
        <span>
          <span
            className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle"
            style={{ background: dlColor }}
          />
          Favors {matchup.team_dl}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart
          data={rows}
          layout="vertical"
          margin={{ top: 0, right: 56, left: 88, bottom: 0 }}
          barCategoryGap="30%"
        >
          <CartesianGrid horizontal={false} strokeDasharray="3 3" stroke="#e6e5e1" />
          <XAxis
            type="number"
            domain={[-10, 10]}
            tickCount={5}
            tickFormatter={(v: number) => (v > 0 ? `+${v}` : String(v))}
            tick={{ fontSize: 11, fill: '#6b6f76' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            width={88}
            tick={{ fontSize: 12, fontWeight: 500, fill: '#111318' }}
            axisLine={false}
            tickLine={false}
          />
          <ReferenceLine x={0} stroke="#9a9ea5" strokeWidth={1} />
          <Tooltip
            formatter={formatTooltip}
            contentStyle={{ fontSize: 12, borderRadius: 6, border: '1px solid #e6e5e1' }}
          />
          <Bar dataKey="value" radius={[0, 3, 3, 0]} isAnimationActive maxBarSize={16}>
            {rows.map((row, i) => (
              <Cell
                key={i}
                fill={row.value == null ? '#edece8' : row.value >= 0 ? olColor : dlColor}
              />
            ))}
            <LabelList
              dataKey="value"
              position="right"
              formatter={(v: unknown) => {
                if (v == null || v === '') return '—'
                const n = typeof v === 'number' ? v : parseFloat(String(v))
                return isNaN(n) ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(1)}`
              }}
              style={{ fontSize: 12, fontWeight: 600 }}
            />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
