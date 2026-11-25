from __future__ import annotations

from datetime import date, timedelta

from rich.style import Style

from gh_profile.api import ContributionCalendar, ContributionDay
from gh_profile.heatmap import PALETTE, render_heatmap


def _make_weeks(start_sunday: date, num_weeks: int) -> list[list[ContributionDay]]:
    weeks: list[list[ContributionDay]] = []
    current = start_sunday
    for w in range(num_weeks):
        days: list[ContributionDay] = []
        for d in range(7):
            level = (w + d) % 5
            days.append(ContributionDay(date=current, count=level, level=level))
            current += timedelta(days=1)
        weeks.append(days)
    return weeks


def test_render_heatmap_unavailable() -> None:
    assert render_heatmap(None).plain == "Contribution data unavailable."
    assert render_heatmap(ContributionCalendar(total=0, weeks=[])).plain == (
        "Contribution data unavailable."
    )


def test_render_heatmap_structure_and_palette() -> None:
    weeks = _make_weeks(date(2025, 1, 5), num_weeks=6)
    cal = ContributionCalendar(total=1234, weeks=weeks)
    rendered = render_heatmap(cal)
    lines = rendered.plain.splitlines()

    assert "1,234 contributions in the last year" in lines[0]
    assert "Less " in lines[0]
    assert "More" in lines[0]
    assert "Jan" in lines[1]

    grid_lines = lines[2:]
    assert len(grid_lines) == 7
    assert grid_lines[1].startswith("Mon ")
    assert grid_lines[3].startswith("Wed ")
    assert grid_lines[5].startswith("Fri ")

    span_colors = {
        span.style.color.name
        for span in rendered.spans
        if isinstance(span.style, Style) and span.style.color is not None
    }
    for color in PALETTE:
        assert color in span_colors


def test_render_heatmap_truncates_to_53_weeks() -> None:
    weeks = _make_weeks(date(2024, 1, 7), num_weeks=60)
    cal = ContributionCalendar(total=500, weeks=weeks)
    rendered = render_heatmap(cal)
    grid_lines = rendered.plain.splitlines()[2:]
    assert len(grid_lines) == 7
    # 4-char day label + 53 * 2 chars per week cell
    assert len(grid_lines[0]) == 4 + 53 * 2
