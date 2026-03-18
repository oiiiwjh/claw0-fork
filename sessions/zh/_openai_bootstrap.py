"""Bootstrap helpers for running the original English sessions via OpenAI."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from dotenv import load_dotenv

from _openai_anthropic_shim import Anthropic, install_module_shim


def _prepare_environment(project_root: Path) -> None:
    load_dotenv(project_root / ".env", override=False)
    load_dotenv(project_root / ".env.openai", override=True)

    openai_api_key = os.getenv("OPENAI_API_KEY", "")
    openai_model_id = os.getenv("OPENAI_MODEL_ID", "gpt-4.1")
    openai_base_url = os.getenv("OPENAI_BASE_URL", "")

    if openai_api_key:
        os.environ["ANTHROPIC_API_KEY"] = openai_api_key
    os.environ["MODEL_ID"] = openai_model_id
    if openai_base_url:
        os.environ["ANTHROPIC_BASE_URL"] = openai_base_url


def _load_original_module(wrapper_path: Path, original_filename: str) -> ModuleType:
    original_path = wrapper_path.with_name(original_filename)
    module_name = f"_openai_wrapped_{original_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, original_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for {original_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _patch_module_runtime(module: ModuleType) -> None:
    model_id = os.getenv("OPENAI_MODEL_ID", "gpt-4.1")
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("ANTHROPIC_BASE_URL") or None

    if hasattr(module, "MODEL_ID"):
        module.MODEL_ID = model_id
    if hasattr(module, "client"):
        module.client = Anthropic(api_key=api_key, base_url=base_url)


def run_original_session(wrapper_file: str, original_filename: str) -> None:
    wrapper_path = Path(wrapper_file).resolve()
    project_root = wrapper_path.parent.parent.parent
    _prepare_environment(project_root)

    shim_module = install_module_shim()
    previous_anthropic = sys.modules.get("anthropic")
    sys.modules["anthropic"] = shim_module

    try:
        module = _load_original_module(wrapper_path, original_filename)
        _patch_module_runtime(module)
        if hasattr(module, "main"):
            module.main()
        else:
            raise RuntimeError(f"{original_filename} does not expose main()")
    finally:
        if previous_anthropic is None:
            sys.modules.pop("anthropic", None)
        else:
            sys.modules["anthropic"] = previous_anthropic
