-- Terminal95: appearance-only config table. Merge with your existing config.
local wezterm = require 'wezterm'

return {
  font = wezterm.font('PxPlus IBM VGA 8x16', { weight = 'Regular' }),
  font_size = 16.0,
  harfbuzz_features = { 'calt=0', 'liga=0', 'dlig=0' },
  window_background_opacity = 1.0,
  default_cursor_style = 'SteadyBlock',
  colors = {
    background = '#cccccc',
    foreground = '#000000',
    cursor_bg = '#000000',
    cursor_fg = '#cccccc',
    cursor_border = '#000000',
    selection_bg = '#aaaaaa',
    selection_fg = '#000000',
    ansi = { '#000000', '#800000', '#008000', '#808000', '#000080', '#800080', '#008080', '#c0c0c0' },
    brights = { '#808080', '#ff0000', '#00ff00', '#ffff00', '#0000ff', '#ff00ff', '#00ffff', '#ffffff' },
  },
}
