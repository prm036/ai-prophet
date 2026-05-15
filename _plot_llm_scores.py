import matplotlib.pyplot as plt
import numpy as np

categories = ["Source Finding", "Evidence Extraction", "Reasoning Synthesis", "Alignment", "Uncertainty Analysis"]
models = ["GPT-5", "Gemini 2.5 Flash", "Grok 4"]
scores = np.array([
    [3.69, 3.66, 4.14, 3.97, 3.94],
    [3.57, 3.66, 3.19, 3.67, 3.74],
    [3.40, 3.51, 3.33, 3.48, 3.66],
])

x = np.arange(len(categories))
width = 0.26
colors = ["#2E5EAA", "#E9724C", "#7DAF4B"]

fig, ax = plt.subplots(figsize=(11, 6))
for i, (model, color) in enumerate(zip(models, colors)):
    offset = (i - 1) * width
    bars = ax.bar(x + offset, scores[i], width, label=model, color=color, edgecolor="black", linewidth=0.6)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.03,
                f"{b.get_height():.2f}", ha="center", va="bottom", fontsize=8)

for idx in [0, 1]:
    ax.axvspan(idx - 0.5, idx + 0.5, color="#FFE58A", alpha=0.25, zorder=0)

ax.set_xticks(x)
ax.set_xticklabels(categories, fontsize=11)
ax.set_ylabel("Score", fontsize=11)
ax.set_ylim(0, 5)
ax.legend(loc="upper right", frameon=True)
ax.grid(axis="y", linestyle="--", alpha=0.4)
ax.set_axisbelow(True)

plt.tight_layout()
plt.savefig("/Users/anrigu/Projects/ai-prophet/llm_scores_top3.png", dpi=200)
print("saved llm_scores_top3.png")
