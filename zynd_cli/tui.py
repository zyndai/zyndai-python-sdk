"""Interactive TUI components for the zynd CLI."""

import sys
import tty
import termios
from rich.console import Console
from rich.text import Text

console = Console()

ACCENT = "#8B5CF6"
DIM = "dim"
HIGHLIGHT_BG = "on #1a1a2e"


def _read_key() -> str:
    """Read a single keypress, handling arrow keys."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            if seq == "[A":
                return "up"
            elif seq == "[B":
                return "down"
            return "esc"
        elif ch in ("\r", "\n"):
            return "enter"
        elif ch == "\x03":
            return "ctrl-c"
        elif ch == "q":
            return "q"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select(
    title: str,
    options: list[dict],
    label_key: str = "label",
    desc_key: str = "description",
) -> int:
    """
    Arrow-key selector. Returns the index of the chosen option.

    Each option is a dict with at least `label_key` and `desc_key` fields.
    """
    selected = 0
    total = len(options)

    # Find max label width for alignment
    max_label = max(len(opt[label_key]) for opt in options)

    def render():
        # Move cursor up to overwrite previous render (except first time)
        console.print()
        console.print(f"  [bold]{title}[/bold]")
        console.print()

        for i, opt in enumerate(options):
            label = opt[label_key]
            desc = opt.get(desc_key, "")
            padded = label.ljust(max_label)

            if i == selected:
                line = Text()
                line.append("  ❯ ", style=f"bold {ACCENT}")
                line.append(padded, style=f"bold {ACCENT}")
                if desc:
                    line.append(f"  {desc}", style=f"{ACCENT}")
                console.print(line)
            else:
                line = Text()
                line.append("    ", style=DIM)
                line.append(padded, style=DIM)
                if desc:
                    line.append(f"  {desc}", style="dim")
                console.print(line)

        console.print()
        console.print(f"  [dim]↑/↓ to move, Enter to select[/dim]", end="")

    # Initial render
    render()

    while True:
        key = _read_key()

        if key == "up":
            selected = (selected - 1) % total
        elif key == "down":
            selected = (selected + 1) % total
        elif key == "enter":
            # Clear the selector and print final choice
            # Move up to clear rendered lines: title(1) + blank(1) + options(total) + blank(1) + hint(1) + initial blank(1)
            lines_to_clear = total + 5
            sys.stdout.write(f"\x1b[{lines_to_clear}A")  # move up
            sys.stdout.write("\x1b[J")  # clear to end of screen
            sys.stdout.flush()
            return selected
        elif key in ("ctrl-c", "q", "esc"):
            console.print("\n")
            sys.exit(0)

        # Re-render: move cursor up to overwrite
        lines_to_clear = total + 5
        sys.stdout.write(f"\x1b[{lines_to_clear}A")
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()
        render()


def prompt(label: str, default: str = "") -> str:
    """Styled input prompt."""
    suffix = f" [dim]({default})[/dim]" if default else ""
    console.print(f"  [bold {ACCENT}]❯[/bold {ACCENT}] {label}{suffix}", end="")
    try:
        value = input(": ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        sys.exit(0)
    return value or default
