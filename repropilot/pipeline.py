"""Allowlisted scientific tools. Every reported statistic is derived from artifacts."""
import hashlib
import html
import importlib.metadata
import json
import platform
import time
import zipfile
from pathlib import Path

from . import store
from .config import ROOT, RUNTIME
from .datasets import CATALOG, digest, load
from .models import Plan


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def inspect_design(counts, metadata, plan):
    import numpy as np
    import pandas as pd
    if counts.empty or metadata.empty or not counts.index.equals(metadata.index):
        raise ValueError("计数矩阵与样本注释未对齐；停止分析。")
    if not counts.index.is_unique or not counts.columns.is_unique:
        raise ValueError("样本或基因标识重复；停止分析。")
    values = counts.to_numpy()
    if not np.isfinite(values).all() or (values < 0).any() or not np.equal(values, np.floor(values)).all():
        raise ValueError("PyDESeq2 需要非负整数原始计数；不接受 TPM、FPKM 或缺失值。")
    needed = ["condition"] + (["library_type"] if plan.adjust_library_type else [])
    if metadata[needed].isna().any().any():
        raise ValueError("设计变量包含缺失值。")
    groups = metadata["condition"].value_counts().to_dict()
    if set(groups) != {plan.treatment, plan.reference} or min(groups.values()) < 2:
        raise ValueError("必须存在两个选定分组，且每组至少两个独立样本。")
    columns = [np.ones(len(metadata))]
    if plan.adjust_library_type:
        columns.extend(pd.get_dummies(metadata["library_type"], drop_first=True, dtype=float).to_numpy().T)
    columns.append((metadata["condition"] == plan.treatment).to_numpy(dtype=float))
    matrix = np.column_stack(columns)
    rank = int(np.linalg.matrix_rank(matrix))
    if rank < matrix.shape[1]:
        raise ValueError("设计矩阵不满秩：condition 与协变量存在完全混杂，不能分别估计其效应。")
    if len(metadata) <= rank:
        raise ValueError("设计矩阵没有剩余自由度，不能估计离散度。")
    return {"groups": {k: int(v) for k, v in groups.items()}, "rank": rank,
            "columns": int(matrix.shape[1]), "residual_df": len(metadata) - rank, "formula": plan.formula}


def fit(counts, metadata, plan, output):
    import numpy as np
    import pandas as pd
    from pydeseq2.dds import DeseqDataSet
    from pydeseq2.ds import DeseqStats
    from pydeseq2.default_inference import DefaultInference
    keep = (counts >= plan.min_count).sum(axis=0) >= plan.min_samples
    filtered = counts.loc[:, keep]
    if filtered.shape[1] < 20:
        raise ValueError("表达过滤后不足 20 个基因，不适合继续拟合本流程。")
    inference = DefaultInference(n_cpus=1)
    dds = DeseqDataSet(counts=filtered.astype(int), metadata=metadata,
        design=plan.formula, refit_cooks=True, inference=inference, quiet=True)
    dds.deseq2()
    stats = DeseqStats(dds, contrast=["condition", plan.treatment, plan.reference],
        alpha=plan.alpha, inference=inference, quiet=True)
    stats.summary()
    result = stats.results_df.sort_values("padj")
    result.index.name = "gene"
    result.to_csv(output / "differential_expression.csv")
    norm = pd.DataFrame(dds.layers["normed_counts"], index=filtered.index, columns=filtered.columns)
    norm.to_csv(output / "normalized_counts.csv")
    significant = (result.padj < plan.alpha) & (result.log2FoldChange.abs() >= plan.lfc_threshold)
    significant = significant.fillna(False)
    tested = result.padj.notna()
    if not tested.any():
        raise ValueError("没有得到可用的调整后 P 值；不能生成成功报告。")
    up = int((significant & (result.log2FoldChange > 0)).sum())
    down = int((significant & (result.log2FoldChange < 0)).sum())
    top = result.loc[significant, ["log2FoldChange", "padj", "baseMean"]].head(10)
    # pandas.to_json's decimal precision can round small nonzero p-values to 0.
    # Preserve Python float precision in the report/API representation.
    top_records = [{"gene": str(gene), **{column: float(value) for column, value in row.items()}}
                   for gene, row in top.iterrows()]
    return {"input_genes": counts.shape[1], "retained_genes": filtered.shape[1],
            "tested_genes": int(tested.sum()), "na_padj": int((~tested).sum()), "samples": len(metadata),
            "up": up, "down": down, "significant": up + down, "top_genes": top_records,
            "normalization": "PyDESeq2 median-of-ratios", "test": "Wald", "multiple_testing": "Benjamini-Hochberg",
            "independent_filter": True, "cooks_filter": True, "lfc_shrinkage": False}


def render_figures(output, metadata, plan):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.labelcolor": "#53616e", "axes.edgecolor": "#dce1e4",
                         "text.color": "#243b48", "figure.facecolor": "white", "axes.titleweight": "bold"})
    result = pd.read_csv(output / "differential_expression.csv", index_col=0)
    norm = pd.read_csv(output / "normalized_counts.csv", index_col=0)
    up = (result.padj < plan.alpha) & (result.log2FoldChange >= plan.lfc_threshold)
    down = (result.padj < plan.alpha) & (result.log2FoldChange <= -plan.lfc_threshold)
    colors = np.where(up, "#d17d5d", np.where(down, "#597da6", "#cfd6d9"))
    valid = result.padj.notna() & np.isfinite(result.log2FoldChange)
    fig, ax = plt.subplots(figsize=(7.5, 5.2), layout="constrained")
    ax.scatter(result.loc[valid, "log2FoldChange"], -np.log10(result.loc[valid, "padj"].clip(lower=1e-300)),
               c=colors[valid], s=11, alpha=0.68, linewidth=0, rasterized=True)
    ax.axhline(-np.log10(plan.alpha), color="#a6b2b8", lw=0.8, ls="--")
    for x in (-plan.lfc_threshold, plan.lfc_threshold):
        ax.axvline(x, color="#a6b2b8", lw=0.8, ls="--")
    ax.set(xlabel=f"log2 fold change ({plan.treatment} / {plan.reference})", ylabel="-log10 adjusted P",
           title="Differential expression landscape")
    ax.text(0.98, 0.96, f"Up {int(up.sum()):,}   /   Down {int(down.sum()):,}", transform=ax.transAxes, ha="right")
    for ext in ("png", "svg"):
        fig.savefig(output / f"volcano.{ext}", dpi=160)
    plt.close(fig)
    # Exploratory PCA: log2(norm+1), top 500 variable genes; not a variance-stabilizing transform.
    logged = np.log2(norm + 1)
    top_genes = logged.var().nlargest(min(500, logged.shape[1])).index
    x = logged[top_genes].to_numpy()
    x -= x.mean(axis=0)
    u, singular, _ = np.linalg.svd(x, full_matrices=False)
    scores = u[:, :2] * singular[:2]
    variance = singular ** 2 / (singular ** 2).sum()
    fig, ax = plt.subplots(figsize=(7.5, 5.2), layout="constrained")
    palette = {plan.treatment: "#d17d5d", plan.reference: "#597da6"}
    for condition in (plan.reference, plan.treatment):
        ix = (metadata.condition == condition).to_numpy()
        ax.scatter(scores[ix, 0], scores[ix, 1], s=95, c=palette[condition], label=condition,
                   edgecolors="white", linewidths=1.5)
    for i, sample in enumerate(metadata.index):
        ax.annotate(sample, (scores[i, 0], scores[i, 1]), xytext=(7, 7), textcoords="offset points", fontsize=8)
    ax.set(xlabel=f"PC1 ({variance[0]:.1%})", ylabel=f"PC2 ({variance[1]:.1%})",
           title="Sample relationships · exploratory PCA")
    ax.legend(frameon=False)
    for ext in ("png", "svg"):
        fig.savefig(output / f"pca.{ext}", dpi=160)
    plt.close(fig)
    return {"pca_pc1": float(variance[0]), "pca_pc2": float(variance[1]),
            "pca_transform": "log2(normalized counts + 1), top 500 variable genes; no VST"}


def report(output, run, plan, summary, provenance):
    escaped = html.escape
    source = CATALOG[plan.dataset_id]
    content = f"""# ReproPilot analysis delivery

Run: {run['id']} | Dataset: {source['name']} | Kind: {source['kind']}

## Research question
{run['prompt']}

## Frozen method
- Design: `{plan.formula}`
- Contrast: {plan.treatment} / {plan.reference}; positive log2FC = higher in {plan.treatment}.
- Pre-filter: counts >= {plan.min_count} in at least {plan.min_samples} samples.
- PyDESeq2 negative-binomial model, Wald test, Benjamini-Hochberg correction.
- Significant: padj < {plan.alpha} and |unshrunk log2FC| >= {plan.lfc_threshold}.
- Cook's filtering and independent filtering enabled. NA padj excluded from discoveries.

## Computed results
{summary['samples']} samples; {summary['input_genes']} input genes; {summary['retained_genes']} retained;
{summary['tested_genes']} non-NA padj; {summary['na_padj']} NA padj.
{summary['up']} upregulated and {summary['down']} downregulated genes under these thresholds.

## Interpretation boundaries
These are statistical associations in the supplied experiment, not validated mechanisms.
Exploratory PCA uses log2(normalized counts + 1) on the 500 most variable retained genes, not VST.
No enrichment, pathway, causal or clinical conclusions were computed.
Sequencing library type is a known protocol covariate, not a claim that all batch effects are controlled.
Changing the formula or thresholds requires a new approved plan. No samples were automatically dropped.

## Provenance and reproduction
Source: {source['source']} ({source['accession']}). Input hashes and package versions are in manifest.json.
Run `python reproduce.py` from this delivery directory after installing the project requirements.
Reproduction writes a separate replay/differential_expression.csv; exact floating-point equality can vary across BLAS/platform versions.
"""
    (output / "report.md").write_text(content, encoding="utf-8")
    table = "".join(f"<tr><td>{escaped(str(g['gene']))}</td><td>{g['log2FoldChange']:.3f}</td><td>{g['padj']:.3g}</td></tr>" for g in summary["top_genes"])
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>ReproPilot · {run['id']}</title>
<style>body{{font:16px/1.7 system-ui;max-width:1000px;margin:50px auto;padding:24px;color:#263e49;background:#f7f8f6}}h1{{font-size:42px}}.stats{{display:flex;gap:20px}}.stats div{{background:white;padding:20px;flex:1;border:1px solid #dce3df;border-radius:12px}}img{{width:48%;background:white}}pre{{white-space:pre-wrap;background:white;padding:24px;border-radius:12px}}td,th{{padding:8px 20px;text-align:left;border-bottom:1px solid #ddd}}a{{color:#42776c}}</style>
<p>RESEARCH EXECUTION / VERIFIED DELIVERY</p><h1>{escaped(source['name'])} · analysis delivery</h1><p>{escaped(run['prompt'])}</p>
<div class="stats"><div>Samples<h2>{summary['samples']}</h2></div><div>Retained genes<h2>{summary['retained_genes']:,}</h2></div><div>Significant<h2>{summary['significant']:,}</h2></div></div>
<p>{escaped(plan.formula)} · {escaped(plan.treatment)} / {escaped(plan.reference)} · FDR &lt; {plan.alpha}</p>
<img src="volcano.png" alt="Volcano plot"><img src="pca.png" alt="PCA"><h2>Top significant genes</h2>
<table><tr><th>Gene</th><th>log2FC</th><th>padj</th></tr>{table}</table><h2>Method & limitations</h2><pre>{escaped(content)}</pre></html>"""
    (output / "report.html").write_text(page, encoding="utf-8")
    (output / "reproduce.py").write_text((ROOT / "scripts" / "reproduce_template.py").read_text(encoding="utf-8"), encoding="utf-8")


def tool_fingerprint():
    versions = {name: importlib.metadata.version(name) for name in ["pydeseq2", "numpy", "pandas", "scipy", "anndata", "formulaic"]}
    return hashlib.sha256((digest(Path(__file__)) + json.dumps(versions, sort_keys=True)).encode()).hexdigest()


def checkpoint_valid(output, plan_hash):
    marker = output / "fit_checkpoint.json"
    try:
        checkpoint = json.loads(marker.read_text(encoding="utf-8"))
        return (checkpoint["plan_hash"] == plan_hash and checkpoint["tool_fingerprint"] == tool_fingerprint()
                and all(digest(output / name) == expected for name, expected in checkpoint["files"].items()))
    except (OSError, KeyError, json.JSONDecodeError):
        return False


def run_pipeline(run_id):
    started = time.monotonic()
    run = store.get(run_id)
    plan = Plan.model_validate(run["plan"])
    output = RUNTIME / run_id
    output.mkdir(parents=True, exist_ok=True)
    stage = "inspect_dataset"
    try:
        store.event(run_id, stage, "running", "读取固定版本数据，核验输入哈希与样本对齐。")
        counts, metadata, provenance = load(plan.dataset_id)
        counts.to_csv(output / "counts.csv")
        metadata.to_csv(output / "metadata.csv")
        write_json(output / "plan.json", run["plan"])
        store.event(run_id, stage, "complete", f"已载入 {len(metadata)} 个样本、{counts.shape[1]:,} 个基因。", {"source": provenance})
        stage = "validate_design"
        store.event(run_id, stage, "running", "检查原始整数计数、重复数、设计矩阵秩及剩余自由度。")
        design = inspect_design(counts, metadata, plan)
        store.event(run_id, stage, "complete", f"设计矩阵满秩 {design['rank']}/{design['columns']}，剩余自由度 {design['residual_df']}。", design)
        stage = "fit_deseq2"
        if checkpoint_valid(output, run["plan_hash"]):
            summary = json.loads((output / "fit_summary.json").read_text(encoding="utf-8"))
            store.event(run_id, stage, "cached", "检查点哈希验证通过，复用统计结果，无需重新拟合。")
        else:
            store.event(run_id, stage, "running", f"拟合负二项模型 {plan.formula}，执行 Wald 检验与 FDR 校正。")
            summary = fit(counts, metadata, plan, output)
            write_json(output / "fit_summary.json", summary)
            checkpoint_files = ["counts.csv", "metadata.csv", "differential_expression.csv", "normalized_counts.csv", "fit_summary.json"]
            write_json(output / "fit_checkpoint.json", {"plan_hash": run["plan_hash"], "tool_fingerprint": tool_fingerprint(),
                       "files": {name: digest(output / name) for name in checkpoint_files}})
            store.event(run_id, stage, "complete", f"统计完成：{summary['retained_genes']:,} 个保留基因；{summary['significant']:,} 个达到选定阈值。", summary)
        stage = "render_figures"
        store.event(run_id, stage, "running", "生成火山图与探索性 PCA，保存 PNG / SVG。")
        # Explicit, one-shot fault injection. Never alters a biological result or analysis method.
        fault_marker = output / "fault_injected.txt"
        if run["recovery_demo"] and not fault_marker.exists():
            fault_marker.write_text("User-selected visualization failure drill.\n", encoding="utf-8")
            raise RuntimeError("故障演练：首次图表阶段主动中断。统计检查点已保存，请点击「从检查点重试」。")
        summary.update(render_figures(output, metadata, plan))
        store.event(run_id, stage, "complete", "已生成火山图、PCA 及可编辑 SVG。")
        summary.update({"design": design, "elapsed_seconds": round(time.monotonic() - started, 2), "dataset_id": plan.dataset_id})
        write_json(output / "summary.json", summary)
        stage = "write_report"
        store.event(run_id, stage, "running", "依据计算产物生成方法说明、统计摘要和复现脚本。")
        report(output, run, plan, summary, provenance)
        versions = {name: importlib.metadata.version(name) for name in ["pydeseq2", "numpy", "pandas", "scipy", "matplotlib", "anndata", "formulaic"]}
        (output / "environment.txt").write_text("python==" + platform.python_version() + "\n" + "\n".join(f"{k}=={v}" for k, v in versions.items()), encoding="utf-8")
        store.event(run_id, stage, "complete", "报告仅引用计算指标，记录输入来源、方法假设和解释边界。")
        stage = "verify_delivery"
        store.event(run_id, stage, "running", "核验 CSV、图像与报告后，生成 SHA-256 交付清单。")
        required = ["counts.csv", "metadata.csv", "plan.json", "differential_expression.csv", "normalized_counts.csv",
                    "volcano.png", "pca.png", "volcano.svg", "pca.svg", "report.md", "report.html", "reproduce.py", "environment.txt", "summary.json"]
        for name in required:
            if not (output / name).exists() or (output / name).stat().st_size == 0:
                raise ValueError(f"缺少有效交付物：{name}")
        from PIL import Image
        for name in ["volcano.png", "pca.png"]:
            with Image.open(output / name) as image:
                image.verify()
        manifest = {"run_id": run_id, "plan_hash": run["plan_hash"], "created_at": store.now(),
            "provenance": provenance, "packages": versions, "python": platform.python_version(),
            "files": {name: {"sha256": digest(output / name), "bytes": (output / name).stat().st_size} for name in required}}
        write_json(output / "manifest.json", manifest)
        store.event(run_id, stage, "complete", f"{len(required)} 项产物校验通过，交付包已就绪。", {"verified_files": len(required)})
        write_json(output / "trace.json", store.get(run_id)["events"])
        with zipfile.ZipFile(output / "delivery.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in required + ["manifest.json", "trace.json"]:
                bundle.write(output / name, name)
        store.transition(run_id, "running", "succeeded", summary=summary, error=None)
    except Exception as exc:
        store.event(run_id, stage, "failed", str(exc)[:1000])
        store.transition(run_id, "running", "failed", error=str(exc)[:1000])
        raise
