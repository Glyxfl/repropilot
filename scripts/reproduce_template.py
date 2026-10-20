"""Frozen statistical replay. Run inside the extracted delivery directory.

Install requirements from the ReproPilot repository first. Writes replay/ only.
"""
import json
from pathlib import Path
import pandas as pd
from pydeseq2.dds import DeseqDataSet
from pydeseq2.ds import DeseqStats
from pydeseq2.default_inference import DefaultInference

root = Path(__file__).resolve().parent
plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
counts = pd.read_csv(root / "counts.csv", index_col=0)
metadata = pd.read_csv(root / "metadata.csv", index_col=0)
counts = counts.loc[:, (counts >= plan["min_count"]).sum() >= plan["min_samples"]]
formula = "~ library_type + condition" if plan["adjust_library_type"] else "~ condition"
inference = DefaultInference(n_cpus=1)
dds = DeseqDataSet(counts=counts.astype(int), metadata=metadata, design=formula,
                  refit_cooks=True, inference=inference, quiet=True)
dds.deseq2()
stats = DeseqStats(dds, contrast=["condition", plan["treatment"], plan["reference"]],
                  alpha=plan["alpha"], inference=inference, quiet=True)
stats.summary()
target = root / "replay"
target.mkdir(exist_ok=True)
stats.results_df.sort_values("padj").to_csv(target / "differential_expression.csv", index_label="gene")
print("Replayed analysis:", target / "differential_expression.csv")
