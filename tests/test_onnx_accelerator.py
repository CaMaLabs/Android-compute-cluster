import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))

np = pytest.importorskip("numpy")
onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from onnx import TensorProto, helper
from swarm_plugin import CAPABILITIES, TASKS


def test_onnx_infer_executes_generated_model(tmp_path):
    importlib.import_module("plugins.accelerators")
    assert "onnx" in CAPABILITIES
    assert "onnx_infer" in TASKS

    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, 4])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, 4])
    bias = helper.make_tensor("bias", TensorProto.FLOAT, [1, 4], [1.0, 1.0, 1.0, 1.0])
    node = helper.make_node("Add", ["x", "bias"], ["y"])
    graph = helper.make_graph([node], "swarm-test", [x], [y], [bias])
    model = helper.make_model(graph, producer_name="compute-swarm-test", opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 9
    model_path = tmp_path / "model.onnx"
    onnx.save(model, model_path)

    result = TASKS["onnx_infer"](
        {
            "_artifact_paths": {"model": str(model_path)},
            "_work_dir": str(tmp_path),
            "model_alias": "model",
            "provider": "cpu",
            "values": [[1.0, 2.0, 3.0, 4.0]],
        }
    )

    assert result["provider"] == "CPUExecutionProvider"
    assert result["outputs"][0]["shape"] == [1, 4]
    assert np.allclose(result["outputs"][0]["values"], [2.0, 3.0, 4.0, 5.0])
