import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import rcParams

rcParams["font.family"] = "sans-serif"
rcParams["font.sans-serif"] = [
    "Avenir Next", "Avenir", "Futura", "Optima",
    "Helvetica Neue", "DejaVu Sans",
]
rcParams["mathtext.fontset"] = "custom"
rcParams["axes.unicode_minus"] = False

INK = "#1f1f1f"

PALETTE = {
    "Dispersed":     "#e8dff0",
    "Overconfident": "#fadfd0",
    "Conservative":  "#dfebd9",
    "Aggressive":    "#f6eccb",
    "Selective":     "#b9cde4",
}

fig, ax = plt.subplots(figsize=(6.8, 6.0))

quad_bounds = {
    "Dispersed":     (-1, 0,  1, 1),
    "Overconfident": ( 0, 0,  1, 1),
    "Conservative":  (-1,-1,  1, 1),
    "Aggressive":    ( 0,-1,  1, 1),
}
for name, (x, y, w, h) in quad_bounds.items():
    ax.add_patch(mpatches.Rectangle(
        (x, y), w, h, facecolor=PALETTE[name], edgecolor="none", zorder=0,
    ))

label_pos = {
    "Dispersed":     (-0.5,  0.50),
    "Overconfident": ( 0.5,  0.50),
    "Conservative":  (-0.5, -0.67),
    "Aggressive":    ( 0.5, -0.50),
}
for name, (x, y) in label_pos.items():
    ax.text(x, y, name, fontsize=13, fontweight="semibold",
            color=INK, ha="center", va="center", zorder=4)

band_y, band_h = -0.34, 0.34
ax.add_patch(mpatches.Rectangle(
    (-1, band_y), 1, band_h,
    facecolor=PALETTE["Selective"], edgecolor="none", alpha=0.95, zorder=3,
))
ax.text(-0.5, band_y + band_h/2, "Selective",
        fontsize=12, fontweight="semibold", color=INK,
        ha="center", va="center", zorder=5)

# Axis lines with arrowheads on both ends
arrow_kw = dict(arrowstyle="->", color=INK, lw=1.0,
                shrinkA=0, shrinkB=0, mutation_scale=11)
# x-axis: two segments from origin outward so each end gets an arrowhead
ax.annotate("", xy=( 1.02, 0), xytext=(0, 0), arrowprops=arrow_kw, zorder=2)
ax.annotate("", xy=(-1.02, 0), xytext=(0, 0), arrowprops=arrow_kw, zorder=2)
ax.annotate("", xy=(0,  1.02), xytext=(0, 0), arrowprops=arrow_kw, zorder=2)
ax.annotate("", xy=(0, -1.02), xytext=(0, 0), arrowprops=arrow_kw, zorder=2)

# Axis labels placed INSIDE the plot area, just inside the arrowheads
lbl_kw = dict(fontsize=10.5, style="italic", color=INK)
ax.text(-0.97,  0.03, "Low divergence",  ha="left",  va="bottom", **lbl_kw)
ax.text( 0.97,  0.03, "High divergence", ha="right", va="bottom", **lbl_kw)
ax.text( 0.03,  0.97, "More skew",       ha="left",  va="top",    **lbl_kw)
ax.text( 0.03, -0.97, "Less skew",       ha="left",  va="bottom", **lbl_kw)

ax.set_xlim(-1.08, 1.08)
ax.set_ylim(-1.08, 1.08)
ax.set_aspect("equal")
ax.set_xticks([]); ax.set_yticks([])
for s in ("top", "right", "bottom", "left"):
    ax.spines[s].set_visible(False)

plt.tight_layout(pad=0.2)
plt.savefig("divergence_skew.png", dpi=300, bbox_inches="tight")
plt.savefig("divergence_skew.pdf", bbox_inches="tight")
print("saved divergence_skew.png / divergence_skew.pdf")
