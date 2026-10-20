"""Two bounded inputs: provenance-pinned public counts and seeded synthetic counts."""
import hashlib
import json

from .config import DATA

CATALOG = {
    "pasilla": {
        "id": "pasilla", "name": "Pasilla knock-down", "label": "公开 RNA-seq · 果蝇",
        "samples": 7, "genes": 14599, "organism": "Drosophila melanogaster",
        "description": "Pasilla 敲低实验：3 treated / 4 untreated；包含 single-read 与 paired-end 两类测序。",
        "source": "https://bioconductor.org/packages/pasilla/", "accession": "GSE18508",
        "kind": "public", "levels": ["treated", "untreated"],
    },
    "synthetic": {
        "id": "synthetic", "name": "Seeded recovery lab", "label": "合成数据 · 故障演练",
        "samples": 8, "genes": 1200, "organism": "Synthetic / not biological",
        "description": "固定种子 42 的负二项计数，预设上下调信号；用于测试执行与恢复，不代表生物学结论。",
        "source": "local:seed=42", "accession": "SYNTHETIC-42",
        "kind": "synthetic", "levels": ["treated", "untreated"],
    },
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(dataset_id):
    import numpy as np
    import pandas as pd
    if dataset_id == "pasilla":
        counts_file = DATA / "pasilla_gene_counts.tsv"
        annotation_file = DATA / "pasilla_sample_annotation.csv"
        provenance = json.loads((DATA / "provenance.json").read_text(encoding="utf-8"))
        for path in (counts_file, annotation_file):
            if digest(path) != provenance["sha256"][path.name]:
                raise ValueError(f"公开输入校验和不匹配：{path.name}")
        counts = pd.read_csv(counts_file, sep="\t", index_col=0).T
        metadata = pd.read_csv(annotation_file, index_col=0)
        # The source uses 'treated1fb' for annotations and 'treated1' for counts.
        metadata.index = metadata.index.str.replace(r"fb$", "", regex=True)
        metadata = metadata.rename(columns={"type": "library_type"})
        metadata = metadata.loc[counts.index, ["condition", "library_type"]]
    elif dataset_id == "synthetic":
        rng = np.random.default_rng(42)
        gene_count = 1200
        mu = rng.lognormal(3.8, 0.85, gene_count)
        means = np.tile(mu, (8, 1))
        means[4:, :80] *= 4
        means[4:, 80:160] *= 0.25
        means[[1, 3, 5, 7], :] *= 1.2
        values = rng.negative_binomial(12, 12 / (12 + means))
        samples = [f"sample_{i+1}" for i in range(8)]
        counts = pd.DataFrame(values, index=samples, columns=[f"SIM_{i+1:04}" for i in range(gene_count)])
        metadata = pd.DataFrame({"condition": ["untreated"] * 4 + ["treated"] * 4,
                                 "library_type": ["single-read", "paired-end"] * 4}, index=samples)
        provenance = {"kind": "synthetic", "seed": 42, "signal_genes": 160, "generator": "negative-binomial"}
    else:
        raise ValueError("Unknown dataset")
    counts.index.name = "sample"
    metadata.index.name = "sample"
    return counts, metadata, provenance
