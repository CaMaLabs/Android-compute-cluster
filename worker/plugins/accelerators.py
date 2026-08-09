from __future__ import annotations

from pathlib import Path
from typing import Any

from swarm_plugin import advertise, task


try:
    import numpy as np
except ImportError:  # optional dependency
    np = None


# ---- CUDA / CuPy ---------------------------------------------------------
try:
    import cupy as cp
except ImportError:  # optional dependency
    cp = None

_CUDA_READY = False
if cp is not None:
    try:
        _CUDA_READY = cp.cuda.runtime.getDeviceCount() > 0
    except Exception:
        _CUDA_READY = False

if _CUDA_READY:
    advertise("cuda")
    advertise("cuda:cupy")

    @task("cuda_vector_add")
    def cuda_vector_add(payload: dict[str, Any]) -> dict[str, Any]:
        a = cp.asarray(payload["a"], dtype=cp.float32)
        b = cp.asarray(payload["b"], dtype=cp.float32)
        if a.shape != b.shape:
            raise ValueError("a and b must have identical shapes")
        out = cp.asnumpy(a + b)
        return {"shape": list(out.shape), "values": out.reshape(-1).tolist()}

    @task("cuda_matmul_npy")
    def cuda_matmul_npy(payload: dict[str, Any]) -> dict[str, Any]:
        if np is None:
            raise RuntimeError("numpy is required for cuda_matmul_npy")
        paths = payload.get("_artifact_paths", {})
        a_alias = str(payload.get("a_alias", "a"))
        b_alias = str(payload.get("b_alias", "b"))
        if a_alias not in paths or b_alias not in paths:
            raise ValueError("cuda_matmul_npy requires artifact aliases for both matrices")
        a = cp.asarray(np.load(paths[a_alias], allow_pickle=False))
        b = cp.asarray(np.load(paths[b_alias], allow_pickle=False))
        if a.ndim != 2 or b.ndim != 2 or a.shape[1] != b.shape[0]:
            raise ValueError("matrix dimensions are incompatible")
        result = cp.asnumpy(a @ b)
        output_name = Path(str(payload.get("output_name", "matmul.npy"))).name or "matmul.npy"
        output = Path(payload["_work_dir"]) / output_name
        np.save(output, result, allow_pickle=False)
        return {
            "shape": list(result.shape),
            "dtype": str(result.dtype),
            "_artifact_outputs": [
                {"path": output_name, "name": output_name, "content_type": "application/x-npy"}
            ],
        }


# ---- ONNX Runtime --------------------------------------------------------
try:
    import onnxruntime as ort
except ImportError:  # optional dependency
    ort = None

if ort is not None and np is not None:
    _ORT_PROVIDERS = set(ort.get_available_providers())
    advertise("onnx")
    advertise("onnxruntime")
    if "CUDAExecutionProvider" in _ORT_PROVIDERS:
        advertise("onnx:cuda")

    def _onnx_input(payload: dict[str, Any], input_name: str, expected_type: str):
        dtype_map = {
            "tensor(float)": np.float32,
            "tensor(double)": np.float64,
            "tensor(int64)": np.int64,
            "tensor(int32)": np.int32,
            "tensor(uint8)": np.uint8,
            "tensor(int8)": np.int8,
            "tensor(bool)": np.bool_,
        }
        dtype = dtype_map.get(expected_type)
        if dtype is None:
            raise ValueError(f"unsupported ONNX input type: {expected_type}")

        artifact_alias = payload.get("input_artifact_alias")
        if artifact_alias:
            paths = payload.get("_artifact_paths", {})
            if artifact_alias not in paths:
                raise ValueError(f"artifact alias not found: {artifact_alias}")
            value = np.load(paths[artifact_alias], allow_pickle=False)
            return np.asarray(value, dtype=dtype)

        inputs = payload.get("inputs")
        if isinstance(inputs, dict) and input_name in inputs:
            return np.asarray(inputs[input_name], dtype=dtype)
        if "values" in payload:
            return np.asarray(payload["values"], dtype=dtype)
        raise ValueError("provide values, inputs{name:...}, or input_artifact_alias")

    @task("onnx_infer")
    def onnx_infer(payload: dict[str, Any]) -> dict[str, Any]:
        paths = payload.get("_artifact_paths", {})
        model_alias = str(payload.get("model_alias", "model"))
        if model_alias not in paths:
            raise ValueError(f"ONNX model artifact alias not found: {model_alias}")

        requested = str(payload.get("provider", "auto")).lower()
        if requested == "cuda":
            if "CUDAExecutionProvider" not in _ORT_PROVIDERS:
                raise RuntimeError("CUDAExecutionProvider is not available on this worker")
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif requested == "cpu":
            providers = ["CPUExecutionProvider"]
        else:
            providers = (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if "CUDAExecutionProvider" in _ORT_PROVIDERS
                else ["CPUExecutionProvider"]
            )

        session = ort.InferenceSession(paths[model_alias], providers=providers)
        session_inputs = session.get_inputs()
        feed: dict[str, Any] = {}
        payload_inputs = payload.get("inputs")
        for spec in session_inputs:
            if isinstance(payload_inputs, dict) and spec.name in payload_inputs:
                dtype_map = {
                    "tensor(float)": np.float32,
                    "tensor(double)": np.float64,
                    "tensor(int64)": np.int64,
                    "tensor(int32)": np.int32,
                    "tensor(uint8)": np.uint8,
                    "tensor(int8)": np.int8,
                    "tensor(bool)": np.bool_,
                }
                dtype = dtype_map.get(spec.type)
                if dtype is None:
                    raise ValueError(f"unsupported ONNX input type: {spec.type}")
                feed[spec.name] = np.asarray(payload_inputs[spec.name], dtype=dtype)
            elif len(session_inputs) == 1:
                feed[spec.name] = _onnx_input(payload, spec.name, spec.type)
            else:
                raise ValueError(f"missing ONNX input: {spec.name}")

        outputs = session.run(None, feed)
        output_specs = session.get_outputs()
        metadata = [
            {"name": spec.name, "shape": list(value.shape), "dtype": str(value.dtype)}
            for spec, value in zip(output_specs, outputs)
        ]

        if bool(payload.get("output_artifact", False)):
            name = Path(str(payload.get("output_name", "onnx_outputs.npz"))).name or "onnx_outputs.npz"
            path = Path(payload["_work_dir"]) / name
            np.savez(path, **{spec.name: value for spec, value in zip(output_specs, outputs)})
            return {
                "provider": session.get_providers()[0],
                "outputs": metadata,
                "_artifact_outputs": [
                    {"path": name, "name": name, "content_type": "application/octet-stream"}
                ],
            }

        total_elements = sum(int(value.size) for value in outputs)
        if total_elements > int(payload.get("max_inline_elements", 100_000)):
            raise ValueError("ONNX output is too large for inline JSON; set output_artifact=true")
        return {
            "provider": session.get_providers()[0],
            "outputs": [
                {**meta, "values": value.reshape(-1).tolist()}
                for meta, value in zip(metadata, outputs)
            ],
        }
