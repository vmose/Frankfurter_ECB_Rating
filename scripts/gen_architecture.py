"""
One-off script to render architecture.png. Not part of the pipeline —
run manually if the architecture changes and the diagram needs updating:

    python scripts_gen_architecture.py
"""

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

INK = "#0F1210"
INK_RAISED = "#171B18"
PAPER = "#EDE8DD"
PAPER_DIM = "#A8A398"
HAIRLINE = "#3A403B"
GAIN = "#3F7D5C"
GOLD = "#B8944F"

mono = FontProperties(family="monospace")

fig, ax = plt.subplots(figsize=(9, 8.6), dpi=200)
fig.patch.set_facecolor(INK)
ax.set_facecolor(INK)
ax.set_xlim(0, 10)
ax.set_ylim(0, 11.6)
ax.axis("off")


def box(x, y, w, h, label, sublabel=None, color=PAPER, accent=HAIRLINE, fontsize=13):
    b = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.3,
        edgecolor=accent,
        facecolor=INK_RAISED,
    )
    ax.add_patch(b)
    ax.text(
        x + w / 2,
        y + h / 2 + (0.14 if sublabel else 0),
        label,
        ha="center",
        va="center",
        color=color,
        fontsize=fontsize,
        fontproperties=mono,
        fontweight="bold",
    )
    if sublabel:
        ax.text(
            x + w / 2,
            y + h / 2 - 0.22,
            sublabel,
            ha="center",
            va="center",
            color=PAPER_DIM,
            fontsize=9,
            fontproperties=mono,
        )
    return (x + w / 2, y, x + w / 2, y + h)


def arrow(x1, y1, x2, y2, color=GOLD):
    a = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle="-|>",
        mutation_scale=14,
        linewidth=1.4,
        color=color,
        shrinkA=2,
        shrinkB=2,
    )
    ax.add_patch(a)


CX = 5.0
W = 4.6
H = 0.9

# Title
ax.text(
    CX,
    11.15,
    "PUBLIC DATA OBSERVATORY",
    ha="center",
    color=PAPER,
    fontsize=17,
    fontproperties=mono,
    fontweight="bold",
)
ax.text(
    CX,
    10.72,
    "currency pipeline — extract → warehouse → transform → quality → dashboard",
    ha="center",
    color=PAPER_DIM,
    fontsize=9.5,
    fontproperties=mono,
)

STEP = 1.28
y = 9.35
_, by0, _, by1 = box(CX - W / 2, y, W, H, "PUBLIC API", "Frankfurter (ECB reference rates)")
y -= STEP
arrow(CX, by0, CX, y + H)
_, by0, _, by1 = box(
    CX - W / 2, y, W, H, "GITHUB ACTIONS", "ingestion.yml — daily cron", accent=GOLD
)
y -= STEP
arrow(CX, by0, CX, y + H)
_, by0, _, by1 = box(CX - W / 2, y, W, H, "RAW PARQUET", "dt=YYYY-MM-DD partitions")
y -= STEP
arrow(CX, by0, CX, y + H)
_, by0, _, by1 = box(CX - W / 2, y, W, H, "BIGQUERY", "raw.currency_rates_frankfurter", color=GOLD)
y -= STEP
arrow(CX, by0, CX, y + H)
_, by0, _, by1 = box(CX - W / 2, y, W, H, "DBT", "staging → intermediate → marts")
y -= 1.22

# Split into Data Quality / Analytics
branch_y = y + H
left_x = CX - 2.5
right_x = CX + 2.5
mid_y = y - 0.3

arrow(CX, branch_y, left_x, mid_y + H)
arrow(CX, branch_y, right_x, mid_y + H)

bw = 3.5
box(
    left_x - bw / 2,
    mid_y,
    bw,
    H,
    "DATA QUALITY",
    "freshness · volume · recon · drift",
    color=GAIN,
    accent=GAIN,
    fontsize=11.5,
)
box(right_x - bw / 2, mid_y, bw, H, "ANALYTICS", "mart_currency_latest / _history", fontsize=11.5)

y2 = mid_y - STEP
arrow(left_x, mid_y, CX, y2 + H)
arrow(right_x, mid_y, CX, y2 + H)

_, by0, _, by1 = box(CX - W / 2, y2, W, H, "D3 DASHBOARD", "static site · GitHub Pages", color=GOLD)

# Footer
ax.text(
    CX,
    y2 - 0.45,
    "extract.py · load.py  →  dbt build  →  quality/*.py  →  export_marts.py  →  Pages",
    ha="center",
    color=PAPER_DIM,
    fontsize=8.5,
    fontproperties=mono,
)

plt.tight_layout()
plt.savefig("architecture.png", facecolor=INK, bbox_inches="tight", pad_inches=0.3)
print("wrote architecture.png")
