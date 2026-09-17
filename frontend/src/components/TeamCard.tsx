import type { OlRankRow, LinemanStat } from '../types'
import { StatTile } from './StatTile'
import { getTeamColor } from '../lib/colors'

interface Props {
  row: OlRankRow
  linemen: LinemanStat[]
  onBack: () => void
}

function fmtPctile(v: string): string {
  const n = parseFloat(v)
  return isNaN(n) ? '—' : `${n.toFixed(1)}%`
}

function fmtWeight(v: string): string | null {
  const n = parseFloat(v)
  return isNaN(n) ? null : `${Math.round(n)} lbs`
}

interface SubscoreBarProps {
  label: string
  description: string
  value: string
  color: string
}

// All four subscore percentiles here are already on a 0-100 scale
// (compute_ol_rank.percentile_rank's output) -- render the bar width and
// the label from the same raw number, no extra scaling.
function SubscoreBar({ label, description, value, color }: SubscoreBarProps) {
  const n = parseFloat(value)
  const pct = isNaN(n) ? 0 : Math.max(0, Math.min(100, n))
  return (
    <div>
      <div className="flex items-center gap-3">
        <div className="w-24 text-xs text-gray-700 font-medium shrink-0">{label}</div>
        <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
          <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
        </div>
        <div className="w-12 text-right text-xs font-mono tabular-nums text-gray-700">
          {isNaN(n) ? '—' : fmtPctile(value)}
        </div>
      </div>
      <p className="text-[11px] text-gray-400 mt-1 ml-[108px]">{description}</p>
    </div>
  )
}

const SUBSCORE_DESCRIPTIONS = {
  mass: "Average listed weight of this team's starting offensive line, percentile-ranked against every FBS team",
  experience: 'Share of this year\'s starters\' snaps that were played by the same players last season, at this same team',
  recruiting: "Average 247Sports composite recruiting rating across this team's starting offensive linemen",
  performance: 'Real run-blocking output from CFBD\'s advanced stats — stuff rate allowed (inverted), line yards, and power success rate',
} as const

function StarRating({ rating, stars }: { rating: string; stars: string }) {
  const n = parseInt(stars, 10)
  if (!rating || isNaN(n)) return <span className="text-gray-300 text-xs">—</span>
  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-xs font-mono tabular-nums text-gray-700">{rating}</span>
      <span className="flex">
        {Array.from({ length: 5 }, (_, i) => (
          <svg key={i} viewBox="0 0 24 24" width="9" height="9" className="mr-px">
            <path
              d="M12 2.5l2.85 6.34 6.9.6-5.23 4.56 1.58 6.8-6.1-3.72-6.1 3.72 1.58-6.8L2.25 9.44l6.9-.6z"
              fill={i < n ? '#111318' : 'none'}
              stroke="#111318"
              strokeWidth="1.6"
            />
          </svg>
        ))}
      </span>
    </span>
  )
}

// left/middle/right_success_rate come out of the CSV as 0-1 fractions
// (fetch_cfbd.DirectionSplit.success_rate) -- multiply by 100 to display.
function DirectionTile({ label, value }: { label: string; value: string }) {
  const n = parseFloat(value)
  const display = isNaN(n) ? '—' : `${(n * 100).toFixed(1)}%`
  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-2.5 text-center">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="font-mono text-base font-semibold tabular-nums text-gray-900">
        {display}
      </div>
    </div>
  )
}

const OL_ORDER = ['LT', 'LG', 'C', 'RG', 'RT', 'T', 'G']

export function TeamCard({ row, linemen, onBack }: Props) {
  const teamColor = getTeamColor(row.team)
  const sorted = [...linemen].sort((a, b) => {
    const ia = OL_ORDER.indexOf(a.position_tag)
    const ib = OL_ORDER.indexOf(b.position_tag)
    return (ia === -1 ? OL_ORDER.length : ia) - (ib === -1 ? OL_ORDER.length : ib)
  })

  return (
    <div>
      <button
        onClick={onBack}
        className="text-xs text-gray-500 hover:text-gray-900 mb-4 transition-colors"
      >
        ← Back to rankings
      </button>

      <div className="pt-3 mb-1" style={{ borderTop: `3px solid ${teamColor}` }}>
        <div className="flex items-baseline justify-between">
          <h2 className="text-lg font-bold text-gray-900">{row.team} Offensive Line</h2>
          <span className="text-xs text-gray-500">
            #{row.rank} of {row.of}
          </span>
        </div>
      </div>
      <p className="text-xs text-gray-500 mb-6">TrenchEdge Offense Ranking</p>

      <div className="grid grid-cols-3 gap-2.5 mb-6">
        <StatTile label="Score" value={parseFloat(row.composite_0_100).toFixed(1)} color={teamColor} />
        <StatTile label="Avg OL weight" value={fmtWeight(row.avg_ol_weight)} />
        <StatTile
          label="Returning snap %"
          value={row.returning_ol_snap_pct ? `${parseFloat(row.returning_ol_snap_pct).toFixed(0)}%` : null}
        />
      </div>

      <section className="mb-8">
        <h3 className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">
          Subscores
        </h3>
        <div className="flex flex-col gap-4">
          <SubscoreBar label="Mass" description={SUBSCORE_DESCRIPTIONS.mass} value={row.mass_pctile} color={teamColor} />
          <SubscoreBar label="Experience" description={SUBSCORE_DESCRIPTIONS.experience} value={row.experience_pctile} color={teamColor} />
          <SubscoreBar label="Recruiting" description={SUBSCORE_DESCRIPTIONS.recruiting} value={row.recruiting_pctile} color={teamColor} />
          <SubscoreBar label="Performance" description={SUBSCORE_DESCRIPTIONS.performance} value={row.performance_pctile} color={teamColor} />
        </div>
      </section>

      <section>
        <h3 className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">
          Starters
        </h3>
        {sorted.length === 0 ? (
          <p className="text-sm text-gray-400">No starter data staged for this team.</p>
        ) : (
          <>
            <div
              className="grid gap-2.5"
              style={{ gridTemplateColumns: `repeat(${sorted.length}, minmax(0, 1fr))` }}
            >
              {sorted.map((p, i) => (
                <div
                  key={i}
                  className="border border-gray-200 rounded-lg p-3 text-center bg-gray-50 min-w-0"
                >
                  <div
                    className="w-7 h-7 mx-auto mb-1.5 rounded-full text-white text-xs font-mono flex items-center justify-center"
                    style={{ backgroundColor: teamColor }}
                  >
                    {p.jersey || '—'}
                  </div>
                  <div className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    {p.position_tag || 'OL'}
                  </div>
                  <div className="text-sm font-medium text-gray-900 leading-tight mt-0.5 break-words">
                    {p.name}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">
                    {p.class_year || '—'} &middot; {p.weight_lbs ? `${p.weight_lbs} lbs` : '—'}
                  </div>
                  <div className="text-xs text-gray-500">
                    {p.snaps_multi_year ? `${p.snaps_multi_year} snaps` : '—'}
                  </div>
                  <div className="mt-1.5">
                    <StarRating rating={p.recruit_rating} stars={p.recruit_stars} />
                  </div>
                </div>
              ))}
            </div>

            <div className="grid grid-cols-3 gap-2.5 mt-3">
              <DirectionTile label="Left" value={row.left_success_rate} />
              <DirectionTile label="Middle" value={row.middle_success_rate} />
              <DirectionTile label="Right" value={row.right_success_rate} />
            </div>
            <p className="text-[11px] text-gray-400 mt-1.5">
              Run success rate by direction (CFBD's rushDirection, left/middle/right of the
              line) — raw data, not part of the Performance subscore above.
            </p>
          </>
        )}
      </section>
    </div>
  )
}
