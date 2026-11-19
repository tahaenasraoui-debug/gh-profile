from __future__ import annotations

from rich.style import Style
from rich.text import Text

from gh_profile.api import ContributionCalendar

# GitHub dark-mode 5-level contribution palette
PALETTE = [
    "#161b22",  # Level 0 (none)
    "#0e4429",  # Level 1
    "#006d32",  # Level 2
    "#26a641",  # Level 3
    "#39d353",  # Level 4
]

DAY_LABELS = {
    0: "    ",
    1: "Mon ",
    2: "    ",
    3: "Wed ",
    4: "    ",
    5: "Fri ",
    6: "    ",
}


def render_heatmap(calendar: ContributionCalendar | None) -> Text:
    """Render a 52x7 GitHub contribution heatmap as a Rich Text block."""
    if not calendar or not calendar.weeks:
        return Text("Contribution data unavailable.", style="dim italic")

    # Keep the most recent 53 weeks at most (standard GitHub view)
    weeks = calendar.weeks[-53:]
    num_weeks = len(weeks)

    output = Text()

    # Header line: total contributions + legend
    total_str = f"{calendar.total:,} contributions in the last year"
    output.append(total_str, style="bold #c9d1d9")
    output.append("   Less ", style="dim")
    for hex_color in PALETTE:
        output.append("■ ", style=Style(color=hex_color))
    output.append("More\n", style="dim")

    # Build month label row (2 chars per week column)
    month_row = [" "] * (num_weeks * 2 + 4)
    last_month = -1
    for w_idx, week in enumerate(weeks):
        first_day = week[0].date
        if first_day.month != last_month:
            # Avoid overlapping adjacent month labels at the very start
            if w_idx == 0 or (w_idx * 2) >= 4:
                abbr = first_day.strftime("%b")
                pos = w_idx * 2
                for i, ch in enumerate(abbr):
                    if pos + i < len(month_row):
                        month_row[pos + i] = ch
            last_month = first_day.month

    output.append("    " + "".join(month_row).rstrip() + "\n", style="dim #8b949e")

    # Build 7 rows (Sun=0 .. Sat=6)
    for row_idx in range(7):
        output.append(DAY_LABELS[row_idx], style="dim #8b949e")
        for week in weeks:
            # Map Python weekday (Mon=0..Sun=6) -> GitHub row (Sun=0..Sat=6)
            day_match = next((d for d in week if (d.date.weekday() + 1) % 7 == row_idx), None)
            if day_match is None:
                output.append("  ")
            else:
                color = PALETTE[max(0, min(4, day_match.level))]
                output.append("■ ", style=Style(color=color))
        if row_idx < 6:
            output.append("\n")

    return output
