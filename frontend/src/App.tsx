import { useState } from 'react'
import { useCSV } from './hooks/useCSV'
import { MatchupSelector } from './components/MatchupSelector'
import { SubscoreChart } from './components/SubscoreChart'
import { CompositeBar } from './components/CompositeBar'
import { StatTile } from './components/StatTile'
import { OlRankTable } from './components/OlRankTable'
import { LinemanStats } from './components/LinemanStats'
import type { MatchupScore, OlRankRow, LinemanStat } from './types'

type Tab = 'matchup' | 'ol-rank' | 'lineman'

function fmtWeight(s: string): string | null {
  const n = parseFloat(s)
  return isNaN(n) ? null : `${Math.round(n)} lbs`
}

function fmtDiff(s: string): string | null {
  const n = parseFloat(s)
  return isNaN(n) ? null : `${n > 0 ? '+' : ''}${Math.round(n)} lbs`
}

export default function App() {
  const [tab, setTab] = useState<Tab>('matchup')
  const [selectedGame, setSelectedGame] = useState<string | null>(null)

  const { data: matchups, loading: mlLoading, error: mlError } = useCSV<MatchupScore>(
    '/data/matchup_scores.csv'
  )
  const { data: olRank, loading: olLoading, error: olError } = useCSV<OlRankRow>(
    '/data/ol_rank_table.csv'
  )
  const { data: linemen, loading: lnLoading, error: lnError } = useCSV<LinemanStat>(
    '/data/lineman_stats.csv'
  )

  const selectedMatchups = selectedGame
    ? matchups.filter((m) => m.matchup_label.replace(/-[ab]$/, '') === selectedGame)
    : []

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4">
        <h1 className="text-xl font-bold tracking-tight text-gray-900">TrenchEdge</h1>
        <p className="text-xs text-gray-500 mt-0.5">OL vs DL matchup analysis</p>
      </header>

      <div className="flex gap-1 px-6 pt-4 border-b border-gray-100 pb-0">
        {(['matchup', 'ol-rank', 'lineman'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? 'border-gray-900 text-gray-900'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t === 'matchup' ? 'Matchup' : t === 'ol-rank' ? 'OL Rankings' : 'Linemen'}
          </button>
        ))}
      </div>

      <main className="px-6 py-6 max-w-3xl">
        {tab === 'matchup' && (
          <>
            {mlError && (
              <p className="text-sm text-red-500 mb-4">
                Could not load matchup_scores.csv — run the Python pipeline first.
              </p>
            )}
            {mlLoading ? (
              <p className="text-sm text-gray-400">Loading…</p>
            ) : (
              <>
                <MatchupSelector
                  matchups={matchups}
                  selected={selectedGame}
                  onSelect={setSelectedGame}
                />
                {selectedMatchups.length === 0 && selectedGame && (
                  <p className="text-sm text-gray-400">No data for this game.</p>
                )}
                {selectedMatchups.map((m) => (
                  <div key={m.matchup_label} className="mb-10">
                    <div
                      className="h-0.5 rounded mb-4"
                      style={{
                        background: `linear-gradient(90deg, ${m.team_ol_color || '#2a78d6'} 50%, ${m.team_dl_color || '#e34948'} 50%)`,
                      }}
                    />
                    <h2 className="text-base font-bold mb-1 text-gray-900">
                      {m.team_ol} OL vs {m.team_dl} DL
                    </h2>
                    {(m.ol_offense_scheme || m.dl_defense_scheme) && (
                      <p className="text-xs text-gray-500 mb-4">
                        {m.ol_offense_scheme || 'Scheme unknown'} vs{' '}
                        {m.dl_defense_scheme || 'scheme unknown'}
                      </p>
                    )}

                    <div className="grid grid-cols-3 gap-2.5 mb-6">
                      <StatTile
                        label={`${m.team_ol} OL avg wt`}
                        value={fmtWeight(m.ol_avg_weight)}
                      />
                      <StatTile
                        label={`${m.team_dl} DL avg wt`}
                        value={fmtWeight(m.dl_avg_weight)}
                      />
                      <StatTile
                        label="Wt differential"
                        value={fmtDiff(m.weight_diff_lbs)}
                        color={
                          m.weight_diff_lbs !== ''
                            ? parseFloat(m.weight_diff_lbs) >= 0
                              ? m.team_ol_color
                              : m.team_dl_color
                            : undefined
                        }
                      />
                    </div>

                    <section className="mb-6">
                      <h3 className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">
                        Subscores
                      </h3>
                      <SubscoreChart matchup={m} />
                    </section>

                    <section>
                      <h3 className="text-xs font-semibold uppercase tracking-widest text-gray-500 mb-3">
                        Composite
                      </h3>
                      <CompositeBar matchup={m} />
                    </section>
                  </div>
                ))}
              </>
            )}
          </>
        )}

        {tab === 'ol-rank' && (
          <>
            {olError && (
              <p className="text-sm text-red-500 mb-4">
                Could not load ol_rank_table.csv — run the pipeline with --with-ol-rank.
              </p>
            )}
            {olLoading ? (
              <p className="text-sm text-gray-400">Loading…</p>
            ) : (
              <OlRankTable rows={olRank} />
            )}
          </>
        )}

        {tab === 'lineman' && (
          <>
            {lnError && (
              <p className="text-sm text-red-500 mb-4">
                Could not load lineman_stats.csv — run the pipeline with --with-ol-rank.
              </p>
            )}
            {lnLoading ? (
              <p className="text-sm text-gray-400">Loading…</p>
            ) : (
              <LinemanStats rows={linemen} />
            )}
          </>
        )}
      </main>
    </div>
  )
}
