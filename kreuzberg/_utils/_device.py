"""Device detection and management utilities for GPU acceleration."""
# ruff: noqa: BLE001  # ~keep

from __future__ import annotations

import warnings
from dataclasses import dataclass
from itertools import chain
from typing import Literal

from kreuzberg.exceptions import ValidationError

DeviceType = Literal["cpu", "cuda", "mps", "auto"]


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Information about a compute device."""

    device_type: Literal["cpu", "cuda", "mps"]
    """The type of device."""
    device_id: int | None = None
    """Device ID for multi-GPU systems. None for CPU or single GPU."""
    memory_total: float | None = None
    """Total memory in GB. None if unknown."""
    memory_available: float | None = None
    """Available memory in GB. None if unknown."""
    name: str | None = None
    """Human-readable device name."""


def detect_available_devices() -> list[DeviceInfo]:
    """Detect all available compute devices.

    Returns:
        List of available devices, with the most preferred device first.
    """
    # Build device lists efficiently using generators
    cpu_device = DeviceInfo(device_type="cpu", name="CPU")

    cuda_devices = _get_cuda_devices() if _is_cuda_available() else []

    mps_device = _get_mps_device() if _is_mps_available() else None
    mps_devices = [mps_device] if mps_device else []

    # Return GPU devices first, then CPU using itertools.chain
    gpu_devices = list(chain(cuda_devices, mps_devices))
    return [*gpu_devices, cpu_device]


def get_optimal_device() -> DeviceInfo:
    """Get the optimal device for OCR processing.

    Returns:
        The best available device, preferring GPU over CPU.
    """
    devices = detect_available_devices()
    return devices[0] if devices else DeviceInfo(device_type="cpu", name="CPU")


def validate_device_request(
    requested: DeviceType,
    backend: str,
    *,
    memory_limit: float | None = None,
    fallback_to_cpu: bool = True,
) -> DeviceInfo:
    """Validate and resolve a device request.

    Args:
        requested: The requested device type.
        backend: Name of the OCR backend requesting the device.
        memory_limit: Optional memory limit in GB.
        fallback_to_cpu: Whether to fallback to CPU if requested device unavailable.

    Returns:
        A validated DeviceInfo object.

    Raises:
        ValidationError: If the requested device is not available and fallback is disabled.
    """
    available_devices = detect_available_devices()

    if requested == "auto":
        device = get_optimal_device()
        if memory_limit is not None:
            _validate_memory_limit(device, memory_limit)
        return device

    matching_devices = [d for d in available_devices if d.device_type == requested]

    if not matching_devices:
        if fallback_to_cpu and requested != "cpu":
            warnings.warn(
                f"Requested device '{requested}' not available for {backend}. Falling back to CPU.",
                UserWarning,
                stacklevel=2,
            )
            cpu_device = next((d for d in available_devices if d.device_type == "cpu"), None)
            if cpu_device:
                return cpu_device

        raise ValidationError(
            f"Requested device '{requested}' is not available for {backend}",
            context={
                "requested_device": requested,
                "backend": backend,
                "available_devices": [d.device_type for d in available_devices],
            },
        )

    device = matching_devices[0]

    if memory_limit is not None:
        _validate_memory_limit(device, memory_limit)

    return device


def get_device_memory_info(device: DeviceInfo) -> tuple[float | None, float | None]:
    """Get memory information for a device.

    Args:
        device: The device to query.

    Returns:
        Tuple of (total_memory_gb, available_memory_gb). None values if unknown.
    """
    if device.device_type == "cpu":
        return None, None

    if device.device_type == "cuda":
        return _get_cuda_memory_info(device.device_id or 0)

    if device.device_type == "mps":
        return _get_mps_memory_info()

    return None, None


def _is_cuda_available() -> bool:
    """Check if CUDA is available."""
    try:
        import torch  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _is_mps_available() -> bool:
    """Check if MPS (Apple Silicon) is available."""
    try:
        import torch  # type: ignore[import-not-found,unused-ignore]  # noqa: PLC0415

        return bool(torch.backends.mps.is_available())
    except ImportError:
        return False


def _get_cuda_devices() -> list[DeviceInfo]:
    """Get information about available CUDA devices."""
    devices: list[DeviceInfo] = []

    try:
        import torch  # noqa: PLC0415

        if not torch.cuda.is_available():
            return devices

        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            total_memory = props.total_memory / (1024**3)

            torch.cuda.set_device(i)
            available_memory = torch.cuda.get_device_properties(i).total_memory / (1024**3)
            try:
                allocated = torch.cuda.memory_allocated(i) / (1024**3)
                available_memory = total_memory - allocated
            except Exception:
                available_memory = total_memory

            devices.append(
                DeviceInfo(
                    device_type="cuda",
                    device_id=i,
                    memory_total=total_memory,
                    memory_available=available_memory,
                    name=props.name,
                )
            )

    except ImportError:
        pass

    return devices


def _get_mps_device() -> DeviceInfo | None:
    """Get information about the MPS device."""
    try:
        import torch  # noqa: PLC0415

        if not torch.backends.mps.is_available():
            return None

        return DeviceInfo(
            device_type="mps",
            name="Apple Silicon GPU (MPS)",
        )

    except ImportError:
        return None


def _get_cuda_memory_info(device_id: int) -> tuple[float | None, float | None]:
    """Get CUDA memory information for a specific device."""
    try:
        import torch  # noqa: PLC0415

        if not torch.cuda.is_available():
            return None, None

        props = torch.cuda.get_device_properties(device_id)
        total_memory = props.total_memory / (1024**3)

        try:
            allocated = torch.cuda.memory_allocated(device_id) / (1024**3)
            available_memory = total_memory - allocated
        except Exception:
            available_memory = total_memory

        return total_memory, available_memory

    except ImportError:
        return None, None


def _get_mps_memory_info() -> tuple[float | None, float | None]:
    """Get MPS memory information."""
    return None, None


def _validate_memory_limit(device: DeviceInfo, memory_limit: float) -> None:
    """Validate that a device has enough memory for the requested limit.

    Args:
        device: The device to validate.
        memory_limit: Required memory in GB.

    Raises:
        ValidationError: If the device doesn't have enough memory.
    """
    if device.device_type == "cpu":
        # CPU memory validation is complex and OS-dependent, skip for now  # ~keep
        return

    total_memory, available_memory = get_device_memory_info(device)

    if total_memory is not None and memory_limit > total_memory:
        raise ValidationError(
            f"Requested memory limit ({memory_limit:.1f}GB) exceeds device capacity ({total_memory:.1f}GB)",
            context={
                "device": device.device_type,
                "device_name": device.name,
                "requested_memory": memory_limit,
                "total_memory": total_memory,
                "available_memory": available_memory,
            },
        )

    if available_memory is not None and memory_limit > available_memory:
        warnings.warn(
            f"Requested memory limit ({memory_limit:.1f}GB) exceeds available memory "
            f"({available_memory:.1f}GB) on {device.name or device.device_type}",
            UserWarning,
            stacklevel=3,
        )


def is_backend_gpu_compatible(backend: str) -> bool:
    """Check if an OCR backend supports GPU acceleration.

    Args:
        backend: Name of the OCR backend.

    Returns:
        True if the backend supports GPU acceleration.
    """
    # EasyOCR and PaddleOCR support GPU, Tesseract does not  # ~keep
    return backend.lower() in ("easyocr", "paddleocr")


def get_recommended_batch_size(device: DeviceInfo, input_size_mb: float = 10.0) -> int:
    """Get recommended batch size for OCR processing.

    Args:
        device: The device to optimize for.
        input_size_mb: Estimated input size per item in MB.

    Returns:
        Recommended batch size.
    """
    if device.device_type == "cpu":
        # Conservative batch size for CPU  # ~keep
        return 1

    _, available_memory = get_device_memory_info(device)

    if available_memory is None:
        return 4

    # Use approximately 50% of available memory for batching  # ~keep
    usable_memory_gb = available_memory * 0.5
    usable_memory_mb = usable_memory_gb * 1024

    # Estimate batch size (conservative)  # ~keep
    estimated_batch_size = max(1, int(usable_memory_mb / (input_size_mb * 4)))

    # Cap at reasonable limits  # ~keep
    return min(estimated_batch_size, 32)


def cleanup_device_memory(device: DeviceInfo) -> None:
    """Clean up device memory.

    Args:
        device: The device to clean up.
    """
    if device.device_type == "cuda":
        try:
            import torch  # noqa: PLC0415

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    elif device.device_type == "mps":
        try:
            import torch  # noqa: PLC0415

            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except (ImportError, AttributeError):
            pass
