# Terminal95 profiles

Install the bundled `assets/fonts/PxPlus_IBM_VGA_8x16.ttf` first. Select **PxPlus IBM VGA 8x16** at **16 pt**. For crisp pixel edges, keep the physical rendered em height at a multiple of 16 pixels; DPI and display scaling affect the result.

These files contain appearance settings only. They do not set a shell, launch Codex, run commands, or change permission settings. Merge or include the relevant file in your existing configuration; keep your other preferences.

| Terminal | File | Apply |
| --- | --- | --- |
| Ghostty | [terminal95.ghostty](terminal95.ghostty) | Add `config-file = /absolute/path/to/terminal95.ghostty` to the main config, then reload. |
| Kitty | [terminal95.kitty.conf](terminal95.kitty.conf) | Add `include /absolute/path/to/terminal95.kitty.conf` to `kitty.conf`, then reload. |
| Alacritty | [terminal95.alacritty.toml](terminal95.alacritty.toml) | Add the absolute path to the `[general]` `import` array in `alacritty.toml`. Values in the main file override imported values. |
| WezTerm | [terminal95.wezterm.lua](terminal95.wezterm.lua) | This file returns a Lua config table. Use it as `.wezterm.lua` for a new config, or merge its font/color keys into your existing table. |
| Windows Terminal | [terminal95.windows-terminal.json](terminal95.windows-terminal.json) | Merge the scheme into the `schemes` array and its `profiles.defaults` keys into your `settings.json`. This is a settings snippet, not an automatically discovered fragment extension. |
| iTerm2 | [Terminal95.itermcolors](Terminal95.itermcolors) | Import through Settings → Profiles → Colors → Color Presets → Import. Set the font separately under Text. |

[palette.json](palette.json) records the common colors and font for adapting the style to another terminal. Applications can request their own colors or backgrounds, so a theme does not force every program to render identically.

For a temporary color change in a compatible terminal and POSIX shell, run:

```sh
./scripts/theme.sh
# Later: restore the terminal/profile defaults.
./scripts/theme.sh --reset
```

The script requires [xterm-compatible dynamic color sequences](https://invisible-island.net/xterm/ctlseqs/ctlseqs.html) (OSC 4/10/11/12), refuses redirected output, and leaves the font unchanged. Its reset asks the emulator to restore configured default colors, rather than remembering earlier temporary overrides. Native profiles are the more reliable choice, especially inside tmux or other terminal multiplexers. There is no universal escape sequence for choosing the same font across all terminal emulators.

Format references: [Ghostty](https://ghostty.org/docs/config/reference), [Kitty](https://sw.kovidgoyal.net/kitty/conf/), [Alacritty](https://alacritty.org/config-alacritty.html), [WezTerm colors](https://wezterm.org/config/appearance.html) and [fonts](https://wezterm.org/config/fonts.html), [Windows Terminal color schemes](https://learn.microsoft.com/en-us/windows/terminal/customize-settings/color-schemes) and [profile appearance](https://learn.microsoft.com/en-us/windows/terminal/customize-settings/profile-appearance), [iTerm2 color presets](https://iterm2.com/documentation-preferences-profiles-colors.html).
