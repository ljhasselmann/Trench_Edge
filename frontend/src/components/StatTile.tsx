interface Props {
  label: string
  value: string | null
  color?: string
}

export function StatTile({ label, value, color }: Props) {
  return (
    <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
      <div className="text-xs text-gray-500 mb-1.5">{label}</div>
      <div
        className="font-mono text-xl font-semibold leading-tight tabular-nums"
        style={color ? { color } : undefined}
      >
        {value ?? <span className="text-gray-400">—</span>}
      </div>
    </div>
  )
}
