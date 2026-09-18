# /// script
# requires-python = ">=3.13"
# dependencies = ["pillow==11.3.0"]
# ///
"""Opt-in upstream-backed image contract; fetches source, never calls inference.

Run with mise exec -- uv run --script tests/validation/docling_image_reader.py.
Executes selected *unmodified* AST methods from the pinned Docling revision,
with service/model plumbing replaced by small stand-ins. This avoids installing
Torch or downloading models. Pillow decoding/encoding and all upstream sizing,
VLM preparation and HTTP payload construction remain real. Not a live-service test.
"""

import ast
import base64
import contextlib
import io
import logging
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace as NS

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "charts"))
from test_docling import DoclingTests
from test_openrouter_catalog import render


REVISION = "014e8e357b24aa9d5113317fa454df8a70de9aeb"  # Docling v2.127.0
SOURCES = {}


def source(path):
    if path not in SOURCES:
        url = f"https://raw.githubusercontent.com/docling-project/docling/{REVISION}/{path}"
        with urllib.request.urlopen(url, timeout=30) as response:
            SOURCES[path] = ast.parse(response.read(), filename=path)
    return SOURCES[path]


def upstream(path, owner, names, scope):
    """Execute exact methods, excluding imports, annotations and unrelated code."""
    tree = source(path)
    if owner:
        tree = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, "exec"), scope)
    return {name: scope[name] for name in names}


class CapturedRequest(BaseException):
    """Stop exactly at the HTTP boundary, outside upstream exception handling."""


def main():
    settings = DoclingTests.settings(render("docling", {"docling": {"inference": {"mode": "private-vlm"}}}))
    options_tree = next(node for node in source("docling/datamodel/pipeline_options.py").body
                        if isinstance(node, ast.ClassDef) and node.name == "VlmConvertOptions")
    max_size = next(node for node in options_tree.body
                    if isinstance(node, ast.AnnAssign) and node.target.id == "max_size")
    assert ast.unparse(max_size.annotation) == "int | None"
    assert any(keyword.arg == "default" and isinstance(keyword.value, ast.Constant)
               and keyword.value.value is None for keyword in max_size.value.keywords)

    scope = {"Image": Image, "BytesIO": io.BytesIO, "math": math,
             "Size": lambda **kw: NS(**kw, as_tuple=lambda: (kw["width"], kw["height"])),
             "_POINTS_PER_INCH": 72.0, "_DEFAULT_DPI": (72.0, 72.0),
             "_log": logging.getLogger("upstream-contract"), "time": time,
             "base64": base64, "ThreadPoolExecutor": ThreadPoolExecutor,
             "TimeRecorder": lambda *args: contextlib.nullcontext(),
             "VlmEngineInput": NS}
    image_path = "docling/backend/image_backend.py"
    upstream(image_path, None, ["_validate_dpi", "_get_frame_dpi"], scope)
    page_backend = type("ImagePageBackend", (), upstream(
        image_path, "_ImagePageBackend", ["__init__", "get_size", "get_page_image"], scope))
    scope["_ImagePageBackend"] = page_backend

    # Only abstract document bookkeeping is stubbed; upstream opens and reads PNG.
    class AbstractBackend:
        def __init__(self, in_doc, path_or_stream, options):
            self.input_format = "image"
            self.path_or_stream = path_or_stream

    scope.update(AbstractDocumentBackend=AbstractBackend, PdfBackendOptions=NS,
                 InputFormat=NS(IMAGE="image"))
    reader = type("ImageDocumentBackend", (), upstream(
        image_path, "ImageDocumentBackend", ["__init__", "load_page"], scope))
    page_type = type("Page", (), upstream("docling/datamodel/base_models.py", "Page", ["get_image"], scope))
    scope["np"] = NS(ndarray=type("UnusedNumpyArray", (), {}))
    upstream("docling/models/inference_engines/vlm/_utils.py", None,
             ["normalize_image_to_pil", "preprocess_image_batch"], scope)
    upstream("docling/utils/api_image_request.py", None, ["api_image_request"], scope)
    scope["extract_generation_stoppers"] = lambda _: []
    api_type = type("ApiVlmEngine", (), upstream(
        "docling/models/inference_engines/vlm/api_openai_compatible_engine.py",
        "ApiVlmEngine", ["predict_batch"], scope))
    stage_type = type("VlmConvertModel", (), upstream(
        "docling/models/stages/vlm_convert/vlm_convert_model.py", "VlmConvertModel",
        ["__call__", "_build_engine_inputs", "_resolve_runtime_engine_type"], scope))

    captured = []

    class Session:
        def post(self, url, **kwargs):
            captured.append(kwargs["json"])
            raise CapturedRequest()

    scope["_make_retry_session"] = lambda: contextlib.nullcontext(Session())
    # Include an image beyond common max-size caps; use nonuniform RGB pixels.
    for size in ((321, 197), (2051, 129)):
        original = Image.frombytes("RGB", size, bytes((i * 37 + i // 11) % 256 for i in range(size[0] * size[1] * 3)))
        for preset_name, dpi in (("images", None), ("default", None), ("images", (144, 144))):
            preset = settings["custom_vlm_presets"][preset_name]
            data = io.BytesIO()
            original.save(data, "PNG", **({"dpi": dpi} if dpi else {}))
            data.seek(0)
            backend = reader(None, data).load_page(0)
            page = page_type()
            page._backend, page._image_cache, page.size = backend, {}, backend.get_size()
            # An independently cached preview must not change scale-1 input.
            page.get_image(scale=2)
            model_spec = NS(**preset["model_spec"], get_runtime_input_extra_config=lambda _: {})
            engine = api_type()
            engine._initialized = True
            engine.options = NS(**preset["engine_options"])
            engine.user_params, engine.model_api_params = engine.options.params, {}
            stage = stage_type()
            stage.enabled, stage.engine = True, engine
            stage.options = NS(scale=preset["scale"], max_size=preset.get("max_size"),
                               model_spec=model_spec, engine_options=engine.options)
            try:
                list(stage(None, [page]))
            except CapturedRequest:
                pass
            else:
                raise AssertionError("upstream did not reach the intercepted HTTP boundary")
            url = captured.pop()["messages"][0]["content"][0]["image_url"]["url"]
            actual = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
            if preset_name == "images" and dpi is None:
                assert actual.size == original.size
                assert actual.tobytes() == original.tobytes()
            elif preset_name == "default":
                assert actual.size == (size[0] * 2, size[1] * 2)
            else:
                assert actual.size != original.size, "DPI metadata must be removed by normalization"
    print(f"PASS: pinned Docling {REVISION} reader -> Page -> VLM stage -> API PNG payload")
    print("RGB pixels unchanged for images preset; scale-2 and DPI controls detect resizing. No live inference.")


if __name__ == "__main__":
    main()
