import { useState } from 'react'
import { useCSV } from './hooks/useCSV'
import { OlRankTable } from './components/OlRankTable'
import { LinemanStats } from './components/LinemanStats'
import { TeamCard } from './components/TeamCard'
import type { OlRankRow, LinemanStat } from './types'

type Tab = 'ol-rank' | 'lineman'

export default function App() {
  const [tab, setTab] = useState<Tab>('ol-rank')
  const [selectedTeam, setSelectedTeam] = useState<string | null>(null)

  const { data: olRank, loading: olLoading, error: olError } = useCSV<OlRankRow>(
    '/data/ol_rank_table.csv'
  )
  const { data: linemen, loading: lnLoading, error: lnError } = useCSV<LinemanStat>(
    '/data/lineman_stats.csv'
  )

  const selectedRow = selectedTeam ? olRank.find((r) => r.team === selectedTeam) : undefined
  const selectedLinemen = selectedTeam ? linemen.filter((l) => l.team === selectedTeam) : []

  const handleTabChange = (t: Tab) => {
    setTab(t)
    setSelectedTeam(null)
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="bg-[#181512] px-6 py-10 text-center">
        <p className="text-[11px] uppercase tracking-[0.2em] text-neutral-500 mb-3">
          Offensive Line Performance Rankings &middot; {olRank.length || 138} FBS Teams
        </p>
        <h1 className="text-4xl sm:text-5xl font-black tracking-tight leading-none">
          <span className="text-white">TRENCH</span>
          <span className="text-red-600">EDGE</span>
        </h1>
        <p className="text-sm italic text-neutral-400 mt-3">Mass kicks Ass</p>
      </header>

      <div className="flex gap-1 px-6 pt-4 border-b border-gray-100 pb-0">
        {(['ol-rank', 'lineman'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => handleTabChange(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? 'border-gray-900 text-gray-900'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t === 'ol-rank' ? 'OL Rankings' : 'Linemen'}
          </button>
        ))}
      </div>

      <main className={`px-6 py-6 ${tab === 'ol-rank' && !selectedRow ? 'max-w-6xl' : 'max-w-3xl'}`}>
        {tab === 'ol-rank' && (
          <>
            {olError && (
              <p className="text-sm text-red-500 mb-4">
                Could not load ol_rank_table.csv — run the pipeline with --with-ol-rank.
              </p>
            )}
            {olLoading ? (
              <p className="text-sm text-gray-400">Loading…</p>
            ) : selectedRow ? (
              <TeamCard
                row={selectedRow}
                linemen={selectedLinemen}
                onBack={() => setSelectedTeam(null)}
              />
            ) : (
              <OlRankTable rows={olRank} onSelectTeam={setSelectedTeam} />
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
