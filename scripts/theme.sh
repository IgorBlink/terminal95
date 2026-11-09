#!/bin/sh
# Terminal95 color palette for terminals supporting xterm-compatible OSC colors.
# This changes the current terminal's colors, not its font or configuration file.
set -eu

usage() {
    cat <<'EOF'
Usage: theme.sh [--apply | --reset]

Apply Terminal95 colors to this terminal, or ask it to restore its default colors.
Requires a terminal supporting OSC 4, 10, 11 and 12 (and their reset variants).
Select PxPlus IBM VGA 8x16 at 16 pt in your terminal for the matching pixel font.
EOF
}

if [ "$#" -gt 1 ]; then
    usage >&2
    exit 2
fi

case "${1---apply}" in
    --help|-h)
        usage
        exit 0
        ;;
    --apply)
        action=apply
        ;;
    --reset)
        action=reset
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

if [ ! -t 1 ] || [ "${TERM-}" = dumb ]; then
    echo 'Terminal95: run this directly in an OSC-color-capable terminal.' >&2
    exit 1
fi

if [ "$action" = reset ]; then
    # Restore the emulator/profile defaults, not an arbitrary prior OSC override.
    printf '\033]104\007\033]110\007\033]111\007\033]112\007'
    exit 0
fi

printf '\033]10;#000000\007\033]11;#cccccc\007\033]12;#000000\007'

index=0
for color in \
    '#000000' '#800000' '#008000' '#808000' \
    '#000080' '#800080' '#008080' '#c0c0c0' \
    '#808080' '#ff0000' '#00ff00' '#ffff00' \
    '#0000ff' '#ff00ff' '#00ffff' '#ffffff'
do
    printf '\033]4;%s;%s\007' "$index" "$color"
    index=$((index + 1))
done
