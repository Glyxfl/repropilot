import json
import math
import subprocess
import sys
import unittest
import zipfile

from .test_core import _sandbox  # establishes isolated runtime before importing pipeline
from repropilot import store
from repropilot.config import RUNTIME
from repropilot.models import Plan
from repropilot.pipeline import checkpoint_valid, digest, run_pipeline


class ExecutionIntegrationTests(unittest.TestCase):
    def test_failure_recovery_delivery_and_statistical_replay(self):
        import numpy as np
        import pandas as pd
        store.init()
        run = store.create("合成数据差异分析，开启故障恢复演练", Plan(dataset_id="synthetic").model_dump(), "offline", True)
        run_id = run["id"]
        store.transition(run_id, "awaiting_approval", "running")
        with self.assertRaisesRegex(RuntimeError, "故障演练"):
            run_pipeline(run_id)
        self.assertEqual(store.get(run_id)["status"], "failed")
        output = RUNTIME / run_id
        self.assertTrue(checkpoint_valid(output, run["plan_hash"]))
        old_hash = digest(output / "differential_expression.csv")
        store.transition(run_id, "failed", "running")
        run_pipeline(run_id)
        result = store.get(run_id)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(old_hash, digest(output / "differential_expression.csv"))
        self.assertTrue(any(e["status"] == "cached" for e in result["events"]))
        self.assertGreater(result["summary"]["significant"], 80)
        # Known synthetic signal: the majority of the top 50 discoveries are planted genes.
        frame = pd.read_csv(output / "differential_expression.csv", index_col=0)
        for gene in result["summary"]["top_genes"]:
            reference_p = float(frame.loc[gene["gene"], "padj"])
            self.assertTrue(math.isclose(gene["padj"], reference_p, rel_tol=1e-12, abs_tol=0))
        top = frame.dropna(subset=["padj"]).head(50).index
        self.assertGreater(sum(int(g.split("_")[1]) <= 160 for g in top), 40)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        for name, meta in manifest["files"].items():
            self.assertEqual(digest(output / name), meta["sha256"])
        with zipfile.ZipFile(output / "delivery.zip") as bundle:
            self.assertIsNone(bundle.testzip())
            self.assertIn("reproduce.py", bundle.namelist())
        subprocess.run([sys.executable, str(output / "reproduce.py")], check=True, timeout=120, capture_output=True)
        replay = pd.read_csv(output / "replay" / "differential_expression.csv", index_col=0).loc[frame.index]
        np.testing.assert_allclose(frame[["log2FoldChange", "padj"]], replay[["log2FoldChange", "padj"]], rtol=1e-8, equal_nan=True)
        (output / "normalized_counts.csv").write_text("tampered", encoding="utf-8")
        self.assertFalse(checkpoint_valid(output, run["plan_hash"]))


if __name__ == "__main__": unittest.main()
