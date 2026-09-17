// Team hex colors -- generated ONCE from CFBD's real /teams/fbs data
// (fetch_cfbd.team_colors_from_fbs_teams), not part of the live Python
// pipeline. Regenerate by re-running the one-liner documented in the
// plan (or in this repo's session notes) if CFBD's team colors change
// or new FBS teams are added:
//
//   python -c "
//   import json, sys
//   sys.path.insert(0, 'src')
//   import fetch_cfbd
//   teams = fetch_cfbd.fetch_fbs_teams(2026)
//   colors = fetch_cfbd.team_colors_from_fbs_teams(teams)
//   out = {t: {'color': v['color'] or '#111318', 'altColor': v['alt_color'] or '#6b7280'} for t, v in colors.items()}
//   json.dump(out, open('frontend/src/data/teamColors.json', 'w'), indent=2)
//   "
import teamColors from '../data/teamColors.json'

const TEAM_COLORS = teamColors as Record<string, { color: string; altColor: string }>

const DEFAULT_COLOR = '#111318' // matches the flat bg-gray-900 look this replaces
const DEFAULT_ALT_COLOR = '#6b7280' // matches Tailwind's gray-500

export function getTeamColor(team: string): string {
  return TEAM_COLORS[team]?.color || DEFAULT_COLOR
}

export function getTeamAltColor(team: string): string {
  return TEAM_COLORS[team]?.altColor || DEFAULT_ALT_COLOR
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace('#', '')
  const full = clean.length === 3 ? clean.split('').map((c) => c + c).join('') : clean
  const num = parseInt(full, 16)
  if (isNaN(num) || full.length !== 6) return [17, 19, 24] // DEFAULT_COLOR as rgb
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255]
}

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t)
}

// Dark maroon/red (low = bad) -> near-white (mid) -> deep blue-grey
// (high = good), applied the SAME way to every cell in a column
// regardless of which team's row it's in -- a value-based heatmap, not
// a per-team accent. Two-segment interpolation through three fixed stops.
const HEAT_LOW: [number, number, number] = [122, 31, 43] // dark maroon/red
const HEAT_MID: [number, number, number] = [245, 245, 244] // near-white
const HEAT_HIGH: [number, number, number] = [59, 91, 122] // deep blue-grey

export function heatColorDiverging(pctile: number): string {
  const clamped = Math.max(0, Math.min(100, isNaN(pctile) ? 50 : pctile))
  const t = clamped / 100
  let r: number, g: number, b: number
  if (t < 0.5) {
    const local = t / 0.5
    r = lerp(HEAT_LOW[0], HEAT_MID[0], local)
    g = lerp(HEAT_LOW[1], HEAT_MID[1], local)
    b = lerp(HEAT_LOW[2], HEAT_MID[2], local)
  } else {
    const local = (t - 0.5) / 0.5
    r = lerp(HEAT_MID[0], HEAT_HIGH[0], local)
    g = lerp(HEAT_MID[1], HEAT_HIGH[1], local)
    b = lerp(HEAT_MID[2], HEAT_HIGH[2], local)
  }
  return `rgb(${r}, ${g}, ${b})`
}

// Text needs to flip to white once the heat background gets dark enough
// (near the low or high end) -- otherwise dark text on a dark maroon/blue
// cell is unreadable.
export function heatTextColor(pctile: number): string {
  const clamped = Math.max(0, Math.min(100, isNaN(pctile) ? 50 : pctile))
  return clamped <= 20 || clamped >= 80 ? '#ffffff' : '#111318'
}
