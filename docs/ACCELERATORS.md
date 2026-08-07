# Accelerator backends

Accelerators are local worker capabilities. The controller never supplies CUDA kernels, Vulkan shaders, executables, Python source, or command lines. It sends only a registered task kind plus JSON/artifact inputs.

## Python PC worker: CUDA with CuPy

Install the normal worker first, then the CuPy package matching the machine's CUDA major version.

CUDA 12.x:

```bash
pip install -r worker/requirements.txt -r worker/requirements-cuda12.txt
python worker/worker.py
```

CUDA 13.x:

```bash
pip install -r worker/requirements.txt -r worker/requirements-cuda13.txt
python worker/worker.py
```

When CuPy can see at least one CUDA device, the worker advertises:

```text
cuda
cuda:cupy
task:cuda_vector_add
task:cuda_matmul_npy
```

### CUDA vector add

```json
{
  "kind": "cuda_vector_add",
  "units": [{"a": [1,2,3], "b": [10,20,30]}],
  "requirements": {"capabilities": ["cuda"]}
}
```

### CUDA matrix multiply using artifacts

Upload two NumPy `.npy` matrices and reference them as `a` and `b`:

```json
{
  "kind": "cuda_matmul_npy",
  "units": [{
    "artifact_inputs": [
      {"artifact_id":"<A_SHA256>", "alias":"a", "name":"a.npy"},
      {"artifact_id":"<B_SHA256>", "alias":"b", "name":"b.npy"}
    ],
    "a_alias":"a",
    "b_alias":"b",
    "output_name":"product.npy"
  }],
  "requirements": {"capabilities": ["cuda"]}
}
```

The multiplication runs in CuPy on the GPU and the `.npy` result is uploaded to the swarm artifact store.

## Python PC worker: ONNX Runtime

CPU:

```bash
pip install -r worker/requirements.txt -r worker/requirements-onnx.txt
python worker/worker.py
```

GPU:

```bash
pip install -r worker/requirements.txt -r worker/requirements-onnx-gpu.txt
python worker/worker.py
```

The worker advertises `onnx` and `onnxruntime`. If ONNX Runtime exposes `CUDAExecutionProvider`, it also advertises `onnx:cuda`.

The ONNX model is an artifact rather than remotely executable worker code. Example single-input inference:

```json
{
  "kind":"onnx_infer",
  "units":[{
    "artifact_inputs":[
      {"artifact_id":"<MODEL_SHA256>", "alias":"model", "name":"model.onnx"}
    ],
    "model_alias":"model",
    "provider":"auto",
    "values":[[1.0,2.0,3.0,4.0]]
  }],
  "requirements":{"capabilities":["onnx"]}
}
```

Set `provider` to `cpu` or `cuda` to require that execution path. For large inference outputs, set `output_artifact:true`; the worker writes an `.npz` result and uploads it instead of returning a large JSON array.

Only load models you trust. Model files are treated as data by the swarm, but they are still parsed by the installed inference runtime.

## Native Android: LiteRT

The Android APK bundles LiteRT and advertises:

```text
litert
tflite
task:litert_infer
```

`litert_infer` currently provides a deliberately narrow portable baseline: one FLOAT32 input tensor and one FLOAT32 output tensor. The model arrives through the existing artifact mechanism.

```json
{
  "kind":"litert_infer",
  "units":[{
    "artifact_inputs":[
      {"artifact_id":"<MODEL_SHA256>", "alias":"model", "name":"model.tflite"}
    ],
    "model_alias":"model",
    "values":[0.1,0.2,0.3,0.4]
  }],
  "requirements":{"capabilities":["litert"]}
}
```

An optional `shape` array can resize input tensor 0 before inference for models that support dynamic shapes.

## Native Android: Vulkan Compute

The APK includes an NDK Vulkan backend and a fixed locally bundled compute shader. At startup the worker probes for a Vulkan physical device with a compute-capable queue. Only a successful probe enables:

```text
vulkan
task:vulkan_vector_add
```

Example:

```json
{
  "kind":"vulkan_vector_add",
  "units":[{"a":[1,2,3,4], "b":[10,20,30,40]}],
  "requirements":{"capabilities":["vulkan"]}
}
```

The vectors are copied into host-visible Vulkan storage buffers, the bundled SPIR-V compute pipeline is dispatched on the device GPU, and the result is synchronized and copied back to the worker result.

The controller cannot replace the shader. Adding another Vulkan kernel requires shipping a new trusted APK build and registering a new local task name.

## Scheduling mixed accelerators

Capability requirements keep jobs off incompatible hardware. Examples:

```json
{"requirements":{"capabilities":["cuda"]}}
```

```json
{"requirements":{"capabilities":["onnx:cuda"]}}
```

```json
{"requirements":{"capabilities":["vulkan"]}}
```

Task compatibility is always enforced too: a worker must advertise `task:<kind>` before it can lease that work unit.
