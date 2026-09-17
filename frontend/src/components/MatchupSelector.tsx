import type { MatchupScore } from '../types'

interface Props {
  matchups: MatchupScore[]
  selected: string | null
  onSelect: (key: string) => void
}

export function MatchupSelector({ matchups, selected, onSelect }: Props) {
  const seen = new Set<string>()
  const games: { label: string }[] = []
  for (const m of matchups) {
    const gameLabel = m.matchup_label.replace(/-[ab]$/, '')
    if (!seen.has(gameLabel)) {
      seen.add(gameLabel)
      games.push({ label: gameLabel })
    }
  }

  return (
    <div className="mb-6">
      <label className="block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">
        Matchup
      </label>
      <select
        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-gray-300"
        value={selected ?? ''}
        onChange={(e) => onSelect(e.target.value)}
      >
        <option value="" disabled>
          Select a game…
        </option>
        {games.map((g) => (
          <option key={g.label} value={g.label}>
            {g.label}
          </option>
        ))}
      </select>
    </div>
  )
}
