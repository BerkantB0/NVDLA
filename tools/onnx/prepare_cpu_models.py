from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnx
import onnxruntime as ort
from onnx import numpy_helper
from onnxruntime.quantization import (
    CalibrationDataReader,
    CalibrationMethod,
    QuantFormat,
    QuantType,
    quantize_static,
)
from onnxruntime.quantization.shape_inference import quant_pre_process


class PinnedCalibrationReader(CalibrationDataReader):
    def __init__(self, name: str, values: list[np.ndarray]) -> None:
        self.name = name
        self.values = values
        self.rewind()

    def get_next(self) -> dict[str, np.ndarray] | None:
        if self.index >= len(self.values):
            return None
        value = self.values[self.index]
        self.index += 1
        return {self.name: value}

    def rewind(self) -> None:
        self.index = 0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise ValueError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")


def run_converter(prototxt: Path, weights: Path, destination: Path) -> None:
    raw = destination.with_suffix(".raw.onnx")
    subprocess.run(
        [
            "python",
            "-m",
            "caffe2onnx.convert",
            "--prototxt",
            str(prototxt),
            "--caffemodel",
            str(weights),
            "--onnx",
            str(raw),
        ],
        check=True,
    )
    model = onnx.load(raw)
    initializer_names = {item.name for item in model.graph.initializer}
    retained = [item for item in model.graph.input if item.name not in initializer_names]
    removed = len(model.graph.input) - len(retained)
    del model.graph.input[:]
    model.graph.input.extend(retained)
    model.graph.name = destination.parent.parent.name
    onnx.checker.check_model(model, full_check=True)
    onnx.save(model, destination)
    raw.unlink()
    if removed <= 0:
        raise ValueError(f"{destination}: converter produced no legacy initializer inputs")


def tensor_shape(value_info: Any) -> list[int | str | None]:
    shape: list[int | str | None] = []
    for dim in value_info.type.tensor_type.shape.dim:
        if dim.HasField("dim_value"):
            shape.append(int(dim.dim_value))
        elif dim.HasField("dim_param"):
            shape.append(dim.dim_param)
        else:
            shape.append(None)
    return shape


def save_test_data(directory: Path, value: np.ndarray, output: np.ndarray) -> dict[str, Any]:
    data = directory / "test_data_set_0"
    data.mkdir(parents=True, exist_ok=True)
    input_path = data / "input_0.pb"
    output_path = data / "output_0.pb"
    input_path.write_bytes(
        numpy_helper.from_array(value).SerializeToString()
    )
    output_path.write_bytes(
        numpy_helper.from_array(output).SerializeToString()
    )
    return {
        "path": data.name,
        "input": {
            "path": input_path.name,
            "sha256": sha256_file(input_path),
            "size_bytes": input_path.stat().st_size,
        },
        "output": {
            "path": output_path.name,
            "sha256": sha256_file(output_path),
            "size_bytes": output_path.stat().st_size,
        },
    }


def comparison(reference: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    reference64 = reference.reshape(-1).astype(np.float64)
    candidate64 = candidate.reshape(-1).astype(np.float64)
    absolute = np.abs(reference64 - candidate64)
    denominator = np.linalg.norm(reference64) * np.linalg.norm(candidate64)
    cosine = float(np.dot(reference64, candidate64) / denominator) if denominator else 1.0
    count = min(5, reference64.size)
    reference_top = np.argsort(reference64)[-count:][::-1].astype(int).tolist()
    candidate_top = np.argsort(candidate64)[-count:][::-1].astype(int).tolist()
    top_overlap = len(set(reference_top).intersection(candidate_top))
    return {
        "elements": int(reference64.size),
        "max_abs_error": float(absolute.max(initial=0.0)),
        "mean_abs_error": float(absolute.mean()),
        "rmse": float(np.sqrt(np.mean(np.square(reference64 - candidate64)))),
        "cosine_similarity": cosine,
        "reference_top5": reference_top,
        "candidate_top5": candidate_top,
        "top1_identical": reference_top[:1] == candidate_top[:1],
        "top5_identical": reference_top == candidate_top,
        "top5_overlap": top_overlap,
    }


def inspect_model(path: Path) -> dict[str, Any]:
    model = onnx.load(path)
    onnx.checker.check_model(model, full_check=True)
    counts = Counter(node.op_type for node in model.graph.node)
    return {
        "path": path.name,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
        "ir_version": int(model.ir_version),
        "opsets": [
            {"domain": item.domain, "version": int(item.version)}
            for item in model.opset_import
        ],
        "node_count": len(model.graph.node),
        "operator_counts": dict(sorted(counts.items())),
        "initializer_count": len(model.graph.initializer),
        "inputs": [
            {"name": item.name, "shape": tensor_shape(item)}
            for item in model.graph.input
        ],
        "outputs": [
            {"name": item.name, "shape": tensor_shape(item)}
            for item in model.graph.output
        ],
    }


def canonicalize_quantized_model(path: Path) -> None:
    model = onnx.load(path)
    value_info = sorted(model.graph.value_info, key=lambda item: item.name)
    del model.graph.value_info[:]
    model.graph.value_info.extend(value_info)
    path.write_bytes(model.SerializeToString(deterministic=True))


def validate_quantization_scales(path: Path) -> dict[str, Any]:
    model = onnx.load(path)
    initializers = {item.name: item for item in model.graph.initializer}
    scale_names = {
        node.input[1]
        for node in model.graph.node
        if node.op_type in {"QuantizeLinear", "DequantizeLinear"}
        and len(node.input) > 1
    }
    missing = sorted(name for name in scale_names if name not in initializers)
    if missing:
        raise ValueError(f"{path}: missing Q/DQ scale initializers: {missing}")
    values = [
        numpy_helper.to_array(initializers[name]).astype(np.float64).reshape(-1)
        for name in sorted(scale_names)
    ]
    invalid = [
        name
        for name, value in zip(sorted(scale_names), values)
        if not np.all(np.isfinite(value)) or np.any(value <= 0.0)
    ]
    if invalid:
        raise ValueError(f"{path}: invalid Q/DQ scales: {invalid}")
    flattened = np.concatenate(values) if values else np.array([], dtype=np.float64)
    return {
        "initializer_count": len(scale_names),
        "element_count": int(flattened.size),
        "all_finite_positive": True,
        "minimum": float(flattened.min()) if flattened.size else None,
        "maximum": float(flattened.max()) if flattened.size else None,
    }


def session_output(model: Path, input_name: str, value: np.ndarray) -> np.ndarray:
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    if [item.name for item in session.get_inputs()] != [input_name]:
        raise ValueError(f"unexpected inputs for {model}: {[item.name for item in session.get_inputs()]}")
    return np.asarray(session.run(None, {input_name: value})[0])


def build_ort_variant(
    model: Path, input_name: str, value: np.ndarray, expected: np.ndarray
) -> dict[str, Any]:
    destination = model.with_suffix(".ort")
    options = ort.SessionOptions()
    options.optimized_model_filepath = str(destination)
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.add_session_config_entry("session.save_model_format", "ORT")
    options.add_session_config_entry("session.qdqisint8allowed", "1")
    session = ort.InferenceSession(
        str(model),
        sess_options=options,
        providers=["CPUExecutionProvider"],
        disabled_optimizers=["NchwcTransformer"],
    )
    actual = np.asarray(session.run(None, {input_name: value})[0])
    if actual.shape != expected.shape or not np.allclose(actual, expected, rtol=1e-5, atol=1e-5):
        raise ValueError(f"{destination}: optimized model output mismatch")
    return {
        "path": destination.name,
        "sha256": sha256_file(destination),
        "size_bytes": destination.stat().st_size,
        "format": "ORT",
        "source_onnx_sha256": sha256_file(model),
        "optimization_style": "Fixed",
        "optimization_level": "all",
        "target_platform": "arm",
        "validation": {"rtol": 1e-5, "atol": 1e-5},
    }


def build_model(
    *,
    name: str,
    prototxt: Path,
    weights: Path,
    image: Path,
    value: np.ndarray,
    calibration_values: list[np.ndarray],
    out_root: Path,
    expected_fp32_hash: str | None,
    expected_int8_hash: str | None,
) -> dict[str, Any]:
    model_root = out_root / name
    fp32_dir = model_root / "fp32"
    int8_dir = model_root / "int8"
    fp32_dir.mkdir(parents=True, exist_ok=True)
    int8_dir.mkdir(parents=True, exist_ok=True)
    fp32_model = fp32_dir / "model.onnx"
    int8_model = int8_dir / "model.onnx"
    preprocessed_model = int8_dir / "model.preprocessed.onnx"

    run_converter(prototxt, weights, fp32_model)
    if expected_fp32_hash:
        verify_file(fp32_model, expected_fp32_hash)

    model = onnx.load(fp32_model)
    input_name = model.graph.input[0].name
    caffe = cv2.dnn.readNetFromCaffe(str(prototxt), str(weights))
    caffe.setInput(value)
    caffe_output = np.asarray(caffe.forward())
    fp32_output = session_output(fp32_model, input_name, value)
    fp32_comparison = comparison(caffe_output, fp32_output)
    if (
        not fp32_comparison["top5_identical"]
        or fp32_comparison["max_abs_error"] > 1e-4
        or fp32_comparison["cosine_similarity"] < 0.999999
    ):
        raise ValueError(f"{name}: converted FP32 model did not match Caffe")
    fp32_test_data = save_test_data(fp32_dir, value, fp32_output)
    fp32_ort = build_ort_variant(fp32_model, input_name, value, fp32_output)

    quant_pre_process(
        input_model=str(fp32_model),
        output_model_path=str(preprocessed_model),
        skip_symbolic_shape=True,
        skip_optimization=False,
        skip_onnx_shape=False,
    )
    quantize_static(
        str(preprocessed_model),
        str(int8_model),
        PinnedCalibrationReader(input_name, calibration_values),
        quant_format=QuantFormat.QDQ,
        activation_type=QuantType.QInt8,
        weight_type=QuantType.QInt8,
        per_channel=True,
        op_types_to_quantize=["Conv", "Gemm", "MatMul", "Softmax"],
        calibrate_method=CalibrationMethod.MinMax,
        extra_options={
            "ActivationSymmetric": True,
            "WeightSymmetric": True,
            "MinimumRealRange": 1e-7,
        },
    )
    preprocessed_model.unlink()
    canonicalize_quantized_model(int8_model)
    if expected_int8_hash:
        verify_file(int8_model, expected_int8_hash)
    quantization_scales = validate_quantization_scales(int8_model)
    int8_output = session_output(int8_model, input_name, value)
    int8_comparison = comparison(fp32_output, int8_output)
    (int8_dir / "comparison.json").write_text(
        json.dumps(int8_comparison, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if (
        not int8_comparison["top1_identical"]
        or int8_comparison["top5_overlap"] < 4
        or int8_comparison["cosine_similarity"] < 0.98
    ):
        raise ValueError(f"{name}: INT8 performance model failed the pinned-input check")
    int8_test_data = save_test_data(int8_dir, value, int8_output)

    return {
        "schema_version": 1,
        "name": name,
        "source": {
            "prototxt": {"path": prototxt.name, "sha256": sha256_file(prototxt)},
            "weights": {"path": weights.name, "sha256": sha256_file(weights)},
            "image": {"path": image.name, "sha256": sha256_file(image)},
        },
        "input": {
            "name": input_name,
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "sha256": hashlib.sha256(value.tobytes()).hexdigest(),
        },
        "models": {
            "fp32": {
                **inspect_model(fp32_model),
                "ort": fp32_ort,
                "caffe_comparison": fp32_comparison,
                "test_data": fp32_test_data,
            },
            "int8": {
                **inspect_model(int8_model),
                "fp32_comparison": int8_comparison,
                "test_data": int8_test_data,
                "quantization": {
                    "format": "QDQ",
                    "activation_type": "QInt8",
                    "weight_type": "QInt8",
                    "per_channel": True,
                    "operators": ["Conv", "Gemm", "MatMul", "Softmax"],
                    "calibration_method": "MinMax",
                    "minimum_real_range": 1e-7,
                    "preprocessing": "ORT graph optimization and ONNX shape inference",
                    "canonicalization": "graph.value_info sorted by tensor name; deterministic protobuf serialization",
                    "scale_validation": quantization_scales,
                    "calibration_samples": len(calibration_values),
                    "calibration_scope": (
                        "deterministic augmentations of the pinned workload image; "
                        "performance-only with no dataset accuracy claim"
                    ),
                },
            },
        },
        "acceptance": {
            "fp32": "Caffe top-5 identical, max absolute error <= 1e-4, cosine >= 0.999999",
            "int8": "pinned-input top-1 identical, top-5 overlap >= 4, and cosine >= 0.98",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--lenet-source", required=True, type=Path)
    parser.add_argument("--resnet-source", required=True, type=Path)
    parser.add_argument("--resnet-workload", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    lock = json.loads(args.lock.read_text(encoding="utf-8"))
    lenet_spec = lock["workloads"]["lenet_mnist"]
    resnet_spec = lock["workloads"]["resnet50_imagenet"]
    for spec, root in ((lenet_spec, args.lenet_source), (resnet_spec, args.resnet_source)):
        for item in spec["files"]:
            verify_file(root / item["name"], item["sha256"])

    resnet_image = args.resnet_workload / "cat.center-crop-224.jpg"
    verify_file(resnet_image, resnet_spec["preprocess"]["expected_output_sha256"])
    lenet_image = args.lenet_source / "seven.pgm"

    lenet_pixels = cv2.imread(str(lenet_image), cv2.IMREAD_GRAYSCALE)
    if lenet_pixels is None:
        raise ValueError("OpenCV could not decode the LeNet input")
    lenet_input = lenet_pixels.reshape(1, 1, 28, 28).astype(np.float32)
    resnet_pixels = cv2.imread(str(resnet_image), cv2.IMREAD_COLOR)
    if resnet_pixels is None:
        raise ValueError("OpenCV could not decode the ResNet-50 input")
    resnet_input = cv2.dnn.blobFromImage(
        resnet_pixels,
        scalefactor=1.0,
        size=(224, 224),
        mean=(103.939, 116.779, 123.68),
        swapRB=False,
        crop=False,
    ).astype(np.float32)
    resnet_calibration: list[np.ndarray] = []
    for flipped in (False, True):
        transformed = cv2.flip(resnet_pixels, 1) if flipped else resnet_pixels
        for scale in (0.75, 1.0, 1.25):
            for bias in (-16.0, 0.0, 16.0):
                adjusted = np.clip(
                    transformed.astype(np.float32) * scale + bias,
                    0.0,
                    255.0,
                ).astype(np.uint8)
                resnet_calibration.append(
                    cv2.dnn.blobFromImage(
                        adjusted,
                        scalefactor=1.0,
                        size=(224, 224),
                        mean=(103.939, 116.779, 123.68),
                        swapRB=False,
                        crop=False,
                    ).astype(np.float32)
                )

    args.out.mkdir(parents=True, exist_ok=True)
    expected = lock["cpu_benchmark"].get("expected_fp32_model_sha256", {})
    expected_int8 = lock["cpu_benchmark"].get("expected_int8_model_sha256", {})
    models = [
        build_model(
            name="lenet",
            prototxt=args.lenet_source / "lenet_mnist.prototxt",
            weights=args.lenet_source / "lenet_mnist.caffemodel",
            image=lenet_image,
            value=lenet_input,
            calibration_values=[lenet_input],
            out_root=args.out,
            expected_fp32_hash=expected.get("lenet"),
            expected_int8_hash=expected_int8.get("lenet"),
        ),
        build_model(
            name="resnet50",
            prototxt=args.resnet_source / "ResNet-50-deploy.prototxt",
            weights=args.resnet_source / "ResNet-50-model.caffemodel",
            image=resnet_image,
            value=resnet_input,
            calibration_values=resnet_calibration,
            out_root=args.out,
            expected_fp32_hash=expected.get("resnet50"),
            expected_int8_hash=expected_int8.get("resnet50"),
        ),
    ]
    environment = {
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "onnx": onnx.__version__,
        "onnxruntime": ort.__version__,
        "providers": ort.get_available_providers(),
    }
    manifest = {
        "schema_version": 1,
        "status": "pass",
        "environment": environment,
        "conversion": lock["cpu_benchmark"]["conversion"],
        "runtime": lock["cpu_benchmark"]["runtime"],
        "quantization": lock["cpu_benchmark"]["quantization"],
        "models": models,
    }
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
