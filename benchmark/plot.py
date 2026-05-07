"""
Génère conc.png et fanout.png depuis les CSV de benchmark.

Usage:
    python benchmark/plot.py
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

OUT_DIR = Path(__file__).parent.parent / "out"


def parse_ms(val: str | float) -> float:
    """Convertit '123ms' ou 123 en float."""
    if isinstance(val, (int, float)):
        return float(val)
    return float(str(val).replace("ms", "").strip())


def barplot(df: pd.DataFrame, param_col: str, title: str, out_path: Path) -> None:
    df = df.copy()
    df["avg_ms"] = df["AVG_TIME"].apply(parse_ms)

    # Agrégation par PARAM : moyenne + écart-type sur les 3 runs
    grouped = df.groupby(param_col)["avg_ms"].agg(["mean", "std"]).reset_index()
    grouped.columns = [param_col, "mean", "std"]
    grouped = grouped.sort_values(param_col)
    grouped["std"] = grouped["std"].fillna(0)

    params = grouped[param_col].tolist()
    means  = grouped["mean"].tolist()
    stds   = grouped["std"].tolist()

    x = np.arange(len(params))
    width = 0.5

    fig, ax = plt.subplots(figsize=(10, 6))

    bars = ax.bar(x, means, width=width, color="#4C72B0",
                  edgecolor="white", linewidth=0.8, label="Moyenne (3 runs)")

    # Moustaches (error bars) = ±1 écart-type
    ax.errorbar(x, means, yerr=stds,
                fmt="none", color="black",
                capsize=6, capthick=1.5, elinewidth=1.5,
                label="±1 écart-type")

    # Annoter chaque barre avec la valeur moyenne
    for bar, m, s in zip(bars, means, stds):
        ax.text(bar.get_x() + bar.get_width() / 2,
                bar.get_height() + s + max(means) * 0.01,
                f"{m:.0f}ms", ha="center", va="bottom", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels([str(p) for p in params], fontsize=11)
    ax.set_xlabel(param_col, fontsize=12)
    ax.set_ylabel("Temps moyen de réponse (ms)", fontsize=12)
    ax.set_title(title, fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.yaxis.set_minor_locator(ticker.AutoMinorLocator())
    ax.grid(axis="y", which="major", linestyle="--", alpha=0.5)
    ax.grid(axis="y", which="minor", linestyle=":", alpha=0.3)
    ax.set_axisbelow(True)
    ax.set_ylim(bottom=0)

    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"[PLOT] Sauvegardé: {out_path}")


def main() -> None:
    conc_csv = OUT_DIR / "conc.csv"
    fanout_csv = OUT_DIR / "fanout.csv"

    if conc_csv.exists():
        df = pd.read_csv(conc_csv)
        barplot(
            df,
            param_col="PARAM",
            title="Scalabilité en charge – Timeline avg response time vs utilisateurs simultanés",
            out_path=OUT_DIR / "conc.png",
        )
    else:
        print(f"[WARN] {conc_csv} introuvable – ignoré")

    if fanout_csv.exists():
        df = pd.read_csv(fanout_csv)
        barplot(
            df,
            param_col="PARAM",
            title="Scalabilité en données – Timeline avg response time vs nombre de followees",
            out_path=OUT_DIR / "fanout.png",
        )
    else:
        print(f"[WARN] {fanout_csv} introuvable – ignoré")


if __name__ == "__main__":
    main()
