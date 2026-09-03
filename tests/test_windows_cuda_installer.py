from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cupy_requirements_bundle_cuda_components():
    cuda12 = (ROOT / "worker" / "requirements-cuda12.txt").read_text(encoding="utf-8")
    cuda13 = (ROOT / "worker" / "requirements-cuda13.txt").read_text(encoding="utf-8")
    assert "cupy-cuda12x[ctk]" in cuda12
    assert "cupy-cuda13x[ctk]" in cuda13


def test_windows_installer_validates_cupy_gpu_execution():
    installer = (ROOT / "scripts" / "install-windows-auto.ps1").read_text(encoding="utf-8")
    assert "Testing CuPy CUDA execution" in installer
    assert "cp.cuda.runtime.getDeviceCount()" in installer
    assert "CuPy CUDA: validated" in installer
