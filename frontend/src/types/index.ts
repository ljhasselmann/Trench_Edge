export interface MatchupScore {
  week: string
  year: string
  matchup_label: string
  direction: string
  team_ol: string
  team_dl: string
  team_ol_color: string
  team_dl_color: string
  mass_score: string
  push_score: string
  experience_score: string
  recruiting_score: string
  composite_score: string
  composite_verdict: string
  ol_avg_weight: string
  dl_avg_weight: string
  weight_diff_lbs: string
  ol_offense_scheme: string
  dl_defense_scheme: string
}

export interface OlRankRow {
  rank: string
  of: string
  team: string
  composite_0_100: string
  avg_ol_weight: string
  mass_pctile: string
  returning_ol_snap_pct: string
  experience_pctile: string
  avg_ol_rating: string
  recruiting_pctile: string
  performance_score: string
  performance_pctile: string
  left_success_rate: string
  middle_success_rate: string
  right_success_rate: string
}

export interface LinemanStat {
  team: string
  name: string
  jersey: string
  position_tag: string
  weight_lbs: string
  class_year: string
  snaps_multi_year: string
  recruit_rating: string
  recruit_stars: string
  confidence: string
}
