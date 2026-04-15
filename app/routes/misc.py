"""Non-/api/ prefixed JSON endpoints used directly by the React frontend."""
from __future__ import annotations

import asyncio
import json
import math as _math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.repositories.lineage_repo import LineageRepository, normalize_repo_name
from app.repositories.test_case_repo import TestCaseRepository
from app.repositories.user_repo import UserRepository

router = APIRouter()

_REPO_EXECUTOR = ThreadPoolExecutor(max_workers=4)


async def _get_user(request: Request, session: AsyncSession):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


# ── Pydantic models ────────────────────────────────────────────────────────────

class RunFunctionRequest(BaseModel):
    source: str
    args: Dict[str, Any] = {}
    mock_globals: Dict[str, Any] = {}
    compare_source: Optional[str] = None
    chain_sources: list = []
    module_context: Optional[Dict[str, Any]] = None


class AnalyzeFunctionRequest(BaseModel):
    source: str


class SuggestMocksRequest(BaseModel):
    source: str
    callee_sources: list = []
    free_names: list = []


class AnalyzeAssetRequest(BaseModel):
    asset_id: str
    repo: str
    branch: str


class SuggestFixRequest(BaseModel):
    source: str
    error: str
    callee_sources: list = []


class RunInRepoRequest(BaseModel):
    asset_id: str
    repo: str
    branch: str
    args: Dict[str, Any] = {}
    edited_source: Optional[str] = None


class SaveTestCaseRequest(BaseModel):
    repo: str
    function_name: str
    label: str
    args: Dict[str, Any] = {}
    expected: Optional[Any] = None


class BulkSaveTestCasesRequest(BaseModel):
    repo: str
    function_name: str
    cases: list


class GenerateTestCasesRequest(BaseModel):
    source: str


class AssertTestCaseRequest(BaseModel):
    expected: Any


class RunTestCasesRequest(BaseModel):
    repo: str
    branch: str
    function_name: str
    edited_source: Optional[str] = None


# ── Runner script (inline) ─────────────────────────────────────────────────────

_RUNNER_SCRIPT = """
import sys, json, io, traceback, asyncio, textwrap, importlib, ast, builtins, re

_payload       = json.loads(sys.stdin.read())
_source        = textwrap.dedent(_payload["source"])
_args          = _payload["args"]
_funcname      = _payload["funcname"]
_mock_globals   = _payload.get("mock_globals", {})
_chain_sources  = [textwrap.dedent(s) for s in _payload.get("chain_sources", [])]
_module_context = _payload.get("module_context") or {}

from typing import *

class _AttrDict(dict):
    def __getattr__(self, key):
        if key in self:
            v = self[key]
            if v is None: return _AttrDict()
            if isinstance(v, dict) and not isinstance(v, _AttrDict): return _AttrDict(v)
            return v
        return _AttrDict()
    def __getitem__(self, key):
        try:
            v = dict.__getitem__(self, key)
            if v is None: return _AttrDict()
            if isinstance(v, dict) and not isinstance(v, _AttrDict): return _AttrDict(v)
            return v
        except (KeyError, IndexError, TypeError):
            return _AttrDict()
    def __setattr__(self, key, value): self[key] = value
    def __delattr__(self, key): del self[key]
    def __call__(self, *a, **kw):
        if 'return_value' in self:
            v = self['return_value']
            if v is None: return _AttrDict()
            if isinstance(v, dict) and not isinstance(v, _AttrDict): return _AttrDict(v)
            return v
        return _AttrDict()
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __await__(self):
        async def _r(): return _AttrDict()
        return _r().__await__()
    def __len__(self): return dict.__len__(self)
    def __repr__(self): return '<Mock>'
    def __bool__(self): return True
    def __str__(self): return ''

def _wrap_value(v):
    if isinstance(v, dict):
        return _AttrDict({k: _wrap_value(vv) for k, vv in v.items()})
    if isinstance(v, list):
        return [_wrap_value(i) for i in v]
    return v

try:
    _tree = ast.parse(_source)
    _builtin_names = set(dir(builtins)) | set(dir(__import__("typing")))
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.Name) and _node.id not in _builtin_names and _node.id not in globals():
            try:
                globals()[_node.id] = importlib.import_module(_node.id)
            except ImportError:
                pass
except Exception:
    pass

for _k, _v in (_module_context.get("globals") or {}).items():
    if _k not in globals():
        globals()[_k] = _wrap_value(_v)

for _imp in (_module_context.get("imports") or []):
    try:
        exec(compile(_imp, "<module_import>", "exec"), globals())
    except Exception:
        pass

for _k, _v in _mock_globals.items():
    globals()[_k] = _wrap_value(_v)

try:
    _exc_tree = ast.parse(_source)
    _raise_exc_names = []
    for _exc_node in ast.walk(_exc_tree):
        if isinstance(_exc_node, ast.ExceptHandler) and _exc_node.type:
            if isinstance(_exc_node.type, ast.Name):
                _raise_exc_names.append(_exc_node.type.id)
            elif isinstance(_exc_node.type, ast.Tuple):
                for _elt in _exc_node.type.elts:
                    if isinstance(_elt, ast.Name):
                        _raise_exc_names.append(_elt.id)
        elif isinstance(_exc_node, ast.Raise) and _exc_node.exc is not None:
            _re = _exc_node.exc
            _rname = None
            if isinstance(_re, ast.Name):
                _rname = _re.id
            elif isinstance(_re, ast.Call) and isinstance(_re.func, ast.Name):
                _rname = _re.func.id
            if _rname:
                _raise_exc_names.append(_rname)
    for _en in _raise_exc_names:
        _cur = globals().get(_en)
        _is_exc = isinstance(_cur, type) and issubclass(_cur, BaseException)
        if not _is_exc:
            globals()[_en] = type(_en, (Exception,), {})
except Exception:
    pass

def _autowrap_callables(src):
    try:
        for _call_node in ast.walk(ast.parse(src)):
            if isinstance(_call_node, ast.Call) and isinstance(_call_node.func, ast.Name):
                _cn = _call_node.func.id
                if _cn not in globals() and not hasattr(builtins, _cn):
                    globals()[_cn] = _AttrDict()
                elif _cn in globals() and not callable(globals()[_cn]):
                    _rv = globals()[_cn]
                    globals()[_cn] = (lambda _v: lambda *_a, **_kw: _v)(_rv)
    except Exception:
        pass

_autowrap_callables(_source)
for _cs in _chain_sources:
    _autowrap_callables(_cs)

def _fix_exc_classes(src):
    try:
        for _exc_node in ast.walk(ast.parse(src)):
            if isinstance(_exc_node, ast.ExceptHandler) and _exc_node.type:
                _exc_names = []
                if isinstance(_exc_node.type, ast.Name):
                    _exc_names.append(_exc_node.type.id)
                elif isinstance(_exc_node.type, ast.Tuple):
                    for _elt in _exc_node.type.elts:
                        if isinstance(_elt, ast.Name):
                            _exc_names.append(_elt.id)
                for _en in _exc_names:
                    _cur = globals().get(_en)
                    if not (isinstance(_cur, type) and issubclass(_cur, BaseException)):
                        globals()[_en] = type(_en, (Exception,), {})
    except Exception:
        pass

for _cs in _chain_sources:
    _fix_exc_classes(_cs)
    try:
        exec(compile(_cs, "<chain>", "exec"), globals())
    except Exception:
        pass

exec(compile(_source, "<function>", "exec"), globals())

_trace_log = []

def _make_tracer(_tn, _tf):
    if asyncio.iscoroutinefunction(_tf):
        async def _traced_async(*a, **kw):
            try:
                _tr = await _tf(*a, **kw)
                _trace_log.append({"fn": _tn, "ok": True, "result": repr(_tr)[:300]})
                return _tr
            except Exception as _te:
                _trace_log.append({"fn": _tn, "ok": False, "error": str(_te)})
                raise
        return _traced_async
    else:
        def _traced(*a, **kw):
            try:
                _tr = _tf(*a, **kw)
                _trace_log.append({"fn": _tn, "ok": True, "result": repr(_tr)[:300]})
                return _tr
            except Exception as _te:
                _trace_log.append({"fn": _tn, "ok": False, "error": str(_te)})
                raise
        return _traced

_chain_fn_names = set()
for _cs in _chain_sources:
    for _cm in re.finditer(r'(?:async )?def (\\w+)', _cs):
        _chain_fn_names.add(_cm.group(1))
for _tn in _chain_fn_names:
    if _tn in globals() and callable(globals()[_tn]) and not isinstance(globals()[_tn], type):
        globals()[_tn] = _make_tracer(_tn, globals()[_tn])

_buf  = io.StringIO()
_real = sys.stdout
sys.stdout = _buf
try:
    _fn = globals()[_funcname]
    import inspect as _inspect
    _sig_params  = _inspect.signature(_fn).parameters
    _param_names = list(_sig_params.keys())
    _call_args   = {k: _wrap_value(v) for k, v in _args.items()}
    for _pname, _pparam in _sig_params.items():
        if _pname not in _call_args and _pparam.default is not _inspect.Parameter.empty:
            if isinstance(_pparam.default, _AttrDict):
                _call_args[_pname] = None
    if _param_names and _param_names[0] in ("self", "cls"):
        _all_sources = _chain_sources + [_source]
        _method_names = set()
        for _s in _all_sources:
            for _m in re.finditer(r'(?:async )?def (\\w+)', _s):
                _method_names.add(_m.group(1))
        _method_dict = {}
        for _mn in _method_names:
            if _mn in globals() and callable(globals()[_mn]):
                _method_dict[_mn] = globals()[_mn]
        class _ChainMock:
            def __getattr__(self, n): return _ChainMock()
            def __call__(self, *a, **kw): return _ChainMock()
            def __await__(self):
                async def _r(): return _ChainMock()
                return _r().__await__()
            def __iter__(self): return iter([])
            def __repr__(self): return '<Mock>'
            def __bool__(self): return False
            def __str__(self): return ''
        def _fallback_getattr(self, n): return _ChainMock()
        _method_dict['__getattr__'] = _fallback_getattr
        _ChainClass = type('_ChainClass', (), _method_dict)
        _positional_list = [_ChainClass()]
    else:
        _positional_list = []
    _keyword_args = {}
    for _pname, _pparam in _sig_params.items():
        if _pname in ("self", "cls") and _positional_list:
            continue
        if _pname not in _call_args:
            continue
        if _pparam.kind == _inspect.Parameter.VAR_POSITIONAL:
            _v = _call_args[_pname]
            if isinstance(_v, list):
                _positional_list.extend(_v)
        elif _pparam.kind == _inspect.Parameter.VAR_KEYWORD:
            _v = _call_args[_pname]
            if isinstance(_v, dict):
                _keyword_args.update(_v)
        else:
            _keyword_args[_pname] = _call_args[_pname]
    if asyncio.iscoroutinefunction(_fn):
        _result = asyncio.run(_fn(*_positional_list, **_keyword_args))
    else:
        _result = _fn(*_positional_list, **_keyword_args)
    sys.stdout = _real
    try:
        _r = json.dumps(_result, default=str)
    except Exception:
        _r = json.dumps(repr(_result))
    print(json.dumps({"ok": True, "result": json.loads(_r), "stdout": _buf.getvalue(), "trace": _trace_log}))
except Exception as _e:
    sys.stdout = _real
    _err = str(_e)
    if "cannot unpack non-iterable" in _err or "not enough values to unpack" in _err or "too many values to unpack" in _err:
        _err += " — provide the return value as a JSON array in the mock globals field (e.g. ['val1', 'val2'])"
    nm = re.search(r"name '(\\w+)' is not defined", _err)
    print(json.dumps({"ok": False, "error": _err, "stdout": _buf.getvalue(), "trace": _trace_log,
                      **({"undefined_name": nm.group(1)} if nm else {})}))
"""


def _exec_function(source, args, mock_globals=None, chain_sources=None, module_context=None):
    m = re.search(r"^\s*(?:async\s+)?def\s+(\w+)", source, re.MULTILINE)
    if not m:
        return {"ok": False, "error": "No function definition found in source", "stdout": ""}
    funcname = m.group(1)
    payload = json.dumps({"source": source, "args": args, "funcname": funcname,
                          "mock_globals": mock_globals or {}, "chain_sources": chain_sources or [],
                          "module_context": module_context or {}})
    try:
        proc = subprocess.run([sys.executable, "-c", _RUNNER_SCRIPT], input=payload,
                              capture_output=True, text=True, timeout=5)
        out = proc.stdout.strip()
        if out:
            result = json.loads(out)
            if not result.get("ok"):
                nm = re.search(r"NameError: name '(\w+)' is not defined", result.get("error", ""))
                if nm:
                    result["undefined_name"] = nm.group(1)
            return result
        err = proc.stderr.strip()
        nm = re.search(r"NameError: name '(\w+)' is not defined", err)
        return {"ok": False, "error": err or "No output from subprocess", "stdout": "",
                **({"undefined_name": nm.group(1)} if nm else {})}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Execution timed out (5s limit)", "stdout": ""}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stdout": ""}


def _analyze_function(source: str) -> dict:
    import ast
    import builtins
    import importlib
    import textwrap

    source = textwrap.dedent(source)
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {"error": str(e), "needs_mock": [], "auto_imported": []}

    fn_def = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), None)
    if not fn_def:
        return {"error": "No function found", "needs_mock": [], "auto_imported": []}

    builtin_names = set(dir(builtins)) | set(dir(__import__("typing")))

    def _collect_assign_targets(t, out):
        if isinstance(t, ast.Name):
            out.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for elt in t.elts:
                _collect_assign_targets(elt, out)

    local_names: set = set()
    exc_class_names: set = set()
    for node in ast.walk(fn_def):
        if isinstance(node, ast.arg):
            local_names.add(node.arg)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                _collect_assign_targets(t, local_names)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                local_names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                local_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not fn_def:
            local_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            local_names.add(node.name)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _collect_assign_targets(node.target, local_names)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                local_names.add(node.name)
            if node.type:
                if isinstance(node.type, ast.Name):
                    exc_class_names.add(node.type.id)
                elif isinstance(node.type, ast.Tuple):
                    for elt in node.type.elts:
                        if isinstance(elt, ast.Name):
                            exc_class_names.add(elt.id)

    for node in ast.iter_child_nodes(tree):
        if node is fn_def:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local_names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local_names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            local_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                _collect_assign_targets(t, local_names)

    free_names: set = set()
    for node in ast.walk(fn_def):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if name not in builtin_names and name not in local_names and name not in exc_class_names:
                free_names.add(name)

    called_names: set = set()
    for node in ast.walk(fn_def):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in free_names:
                called_names.add(node.func.id)

    needs_mock: list = []
    auto_imported: list = []
    for name in sorted(free_names):
        try:
            importlib.import_module(name)
            auto_imported.append(name)
        except ImportError:
            needs_mock.append({"name": name, "callable": name in called_names})

    return {"needs_mock": needs_mock, "auto_imported": auto_imported}


def _extract_json(text: str) -> dict:
    if "```" in text:
        text = re.sub(r"```[^\n]*\n?", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        text = text[start: end + 1]
    return json.loads(text)


def _run_in_repo(clone_dir, venv_dir, file_path, func_name, args, edited_source=None):
    import importlib.util
    _REPO_RUNNER_SCRIPT = r"""
import sys, json, io, traceback, asyncio, importlib, inspect

_payload = json.loads(sys.stdin.read())
_args = _payload["args"]
_module_name = _payload["module"]
_func_name = _payload["func"]
_edited_source = _payload.get("edited_source")

_buf = io.StringIO()
_real = sys.stdout
sys.stdout = _buf
try:
    _mod = importlib.import_module(_module_name)
    _local_func = _func_name
    if _local_func.startswith(_module_name + "."):
        _local_func = _local_func[len(_module_name) + 1:]
    if _edited_source:
        import textwrap as _tw, ast as _ast_e
        _dedented = _tw.dedent(_edited_source)
        exec(compile(_dedented, "<edited>", "exec"), vars(_mod))
        for _en in _ast_e.walk(_ast_e.parse(_dedented)):
            if isinstance(_en, (_ast_e.FunctionDef, _ast_e.AsyncFunctionDef)):
                _local_func = _en.name
                break
    _obj = _mod
    for _part in _local_func.split("."):
        _obj = getattr(_obj, _part)
    _fn = _obj
    if not callable(_fn):
        for _attr in ('func', 'fget', '__func__', '__wrapped__'):
            _candidate = getattr(_fn, _attr, None)
            if callable(_candidate):
                _fn = _candidate
                break
    _sig = inspect.signature(_fn)
    _filtered = {k: v for k, v in _args.items() if k in _sig.parameters}
    if asyncio.iscoroutinefunction(_fn):
        _result = asyncio.run(_fn(**_filtered))
    else:
        _result = _fn(**_filtered)
    sys.stdout = _real
    try:
        _r = json.dumps(_result, default=str)
    except Exception:
        _r = json.dumps(repr(_result))
    print(json.dumps({"ok": True, "result": json.loads(_r), "stdout": _buf.getvalue()}))
except Exception as _e:
    sys.stdout = _real
    print(json.dumps({"ok": False, "error": str(_e), "stdout": _buf.getvalue()}))
"""
    parts = file_path.replace("\\", "/").split("/")
    module = ".".join(p for p in parts if p).replace(".py", "")

    python_bin = os.path.join(venv_dir, "bin", "python") if os.path.isdir(venv_dir) else sys.executable

    # Install deps if venv doesn't exist yet
    if not os.path.isdir(venv_dir):
        req_file = os.path.join(clone_dir, "requirements.txt")
        subprocess.run([sys.executable, "-m", "venv", venv_dir], capture_output=True)
        if os.path.isfile(req_file):
            subprocess.run([python_bin, "-m", "pip", "install", "-r", req_file, "-q"], capture_output=True)

    payload = json.dumps({"args": args, "module": module, "func": func_name, "edited_source": edited_source})
    try:
        proc = subprocess.run(
            [python_bin, "-c", _REPO_RUNNER_SCRIPT],
            input=payload, capture_output=True, text=True, timeout=15,
            cwd=clone_dir,
            env={**os.environ, "PYTHONPATH": clone_dir},
        )
        out = proc.stdout.strip()
        if out:
            return json.loads(out)
        return {"ok": False, "error": proc.stderr.strip() or "No output", "stdout": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Execution timed out (15s limit)", "stdout": ""}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stdout": ""}


def _deep_equal(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a.keys()) == set(b.keys()) and all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_deep_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        fa, fb = float(a), float(b)
        if _math.isnan(fa) and _math.isnan(fb):
            return True
        return _math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-12)
    return a == b


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/lineage-stats")
async def get_lineage_stats(
    request: Request,
    repo: str,
    branch: str = "main",
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_lineage_stats(user.tenant_id, safe_repo, branch)


@router.get("/lineage-data")
async def get_lineage_data(
    request: Request,
    repo: str,
    branch: str = "main",
    offset: int = 0,
    limit: int = 100,
    search: str = "",
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_lineage_data(
        user.tenant_id, safe_repo, branch,
        offset=offset, limit=limit, search=search,
    )


@router.get("/lineage-node")
async def get_lineage_node(
    request: Request,
    repo: str,
    branch: str,
    asset_id: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    data = await lineage_repo.fetch_node_with_neighbors(user.tenant_id, safe_repo, branch, asset_id)
    if not data:
        raise HTTPException(status_code=404, detail="Node not found")
    return data


@router.post("/run-function")
async def run_function(
    request: Request,
    body: RunFunctionRequest,
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    result = _exec_function(body.source, body.args, body.mock_globals, body.chain_sources, body.module_context)
    if body.compare_source is not None:
        original = _exec_function(body.compare_source, body.args, body.mock_globals, body.chain_sources, body.module_context)
        return {"mode": "compare", "original": original, "edited": result}
    return {"mode": "single", **result}


@router.post("/analyze-function")
async def analyze_function(
    request: Request,
    body: AnalyzeFunctionRequest,
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    return _analyze_function(body.source)


@router.post("/suggest-mocks")
async def suggest_mocks(
    request: Request,
    body: SuggestMocksRequest,
    session: AsyncSession = Depends(get_session),
):
    import httpx
    await _get_user(request, session)
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not api_key:
        return {"error": "LITELLM_API_KEY not set", "params": {}, "mocks": {}}

    callee_section = ""
    if body.callee_sources:
        callee_section = "\n\nCallee functions:\n" + "\n---\n".join(f"```python\n{s}\n```" for s in body.callee_sources[:5])

    callables = [n["name"] for n in body.free_names if isinstance(n, dict) and n.get("callable")]
    values = [n["name"] for n in body.free_names if isinstance(n, dict) and not n.get("callable")]
    mocks_desc = ""
    if callables:
        mocks_desc += f"\nCallable mocks: {', '.join(callables)}"
    if values:
        mocks_desc += f"\nValue mocks: {', '.join(values)}"

    prompt = f"""Suggest minimal mock values for this Python function.\n\nFunction:\n```python\n{body.source}\n```{callee_section}\n\nExternal names:{mocks_desc}\n\nReturn ONLY JSON: {{"params": {{}}, "mocks": {{}}}}"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://llmproxy.atlan.dev/chat/completions",
                headers={"x-litellm-api-key": api_key, "Content-Type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1024,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        return _extract_json(text)
    except Exception as exc:
        return {"error": str(exc), "params": {}, "mocks": {}}


@router.post("/analyze-asset")
async def analyze_asset(
    request: Request,
    body: AnalyzeAssetRequest,
    session: AsyncSession = Depends(get_session),
):
    import ast as _ast
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(body.repo)
    lineage_repo = LineageRepository(session)

    node_data = await lineage_repo.fetch_node_with_neighbors(user.tenant_id, safe_repo, body.branch, body.asset_id)
    if not node_data:
        raise HTTPException(status_code=404, detail="Node not found")

    source = node_data["node"].get("source") or ""
    callee_sources: list = []
    visited: set = {body.asset_id}
    queue = [(nd["id"], nd.get("source")) for nd in node_data.get("upstream", [])]

    while queue and len(visited) < 50:
        callee_id, callee_src = queue.pop(0)
        if callee_id in visited:
            continue
        visited.add(callee_id)
        if callee_src:
            callee_sources.append(callee_src)
        sub = await lineage_repo.fetch_node_with_neighbors(user.tenant_id, safe_repo, body.branch, callee_id)
        if sub:
            for sc in sub.get("upstream", []):
                if sc["id"] not in visited:
                    queue.append((sc["id"], sc.get("source")))

    analysis = _analyze_function(source) if source else {"needs_mock": [], "auto_imported": []}
    chain_defined: set = set()
    for cs in callee_sources:
        try:
            tree = _ast.parse(cs)
            for node in _ast.iter_child_nodes(tree):
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    chain_defined.add(node.name)
                elif isinstance(node, _ast.ClassDef):
                    chain_defined.add(node.name)
                elif isinstance(node, _ast.Import):
                    for alias in node.names:
                        chain_defined.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(node, _ast.ImportFrom):
                    for alias in node.names:
                        chain_defined.add(alias.asname or alias.name)
        except Exception:
            pass

    module_context = node_data["node"].get("module_context") or {}
    context_defined: set = set(module_context.get("globals", {}).keys())
    for imp_stmt in module_context.get("imports", []):
        try:
            imp_tree = _ast.parse(imp_stmt)
            for imp_node in _ast.iter_child_nodes(imp_tree):
                if isinstance(imp_node, _ast.Import):
                    for alias in imp_node.names:
                        context_defined.add(alias.asname or alias.name.split(".")[0])
                elif isinstance(imp_node, _ast.ImportFrom):
                    for alias in imp_node.names:
                        context_defined.add(alias.asname or alias.name)
        except Exception:
            pass

    needs_mock = [
        m for m in analysis.get("needs_mock", [])
        if (m["name"] if isinstance(m, dict) else m) not in chain_defined
        and (m["name"] if isinstance(m, dict) else m) not in context_defined
    ]
    return {"needs_mock": needs_mock, "callee_sources": callee_sources,
            "callee_count": len(callee_sources), "module_context": module_context}


@router.post("/suggest-fix")
async def suggest_fix(
    request: Request,
    body: SuggestFixRequest,
    session: AsyncSession = Depends(get_session),
):
    import httpx
    await _get_user(request, session)
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not api_key:
        return {"error": "LITELLM_API_KEY not set", "params": {}, "mocks": {}}

    analysis = _analyze_function(body.source)
    free_names = analysis.get("needs_mock", [])
    callables = [n["name"] for n in free_names if n.get("callable")]
    values = [n["name"] for n in free_names if not n.get("callable")]
    mocks_desc = ""
    if callables:
        mocks_desc += f"\nCallable mocks needed: {', '.join(callables)}"
    if values:
        mocks_desc += f"\nValue mocks needed: {', '.join(values)}"

    callee_section = ""
    if body.callee_sources:
        callee_section = "\n\nCallee functions:\n" + "\n---\n".join(f"```python\n{s}\n```" for s in body.callee_sources[:5])

    prompt = f"""Debug this Python function error and suggest corrected values.\n\nFunction:\n```python\n{body.source}\n```{callee_section}\n\nError:\n```\n{body.error}\n```\n\nMocks needed:{mocks_desc if mocks_desc else ' (none)'}\n\nReturn ONLY JSON: {{"params": {{}}, "mocks": {{}}}}"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://llmproxy.atlan.dev/chat/completions",
                headers={"x-litellm-api-key": api_key, "Content-Type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1024,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        return _extract_json(text)
    except Exception as exc:
        return {"error": str(exc), "params": {}, "mocks": {}}


@router.post("/run-in-repo")
async def run_in_repo_endpoint(
    request: Request,
    body: RunInRepoRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(body.repo)
    lineage_repo = LineageRepository(session)

    node_data = await lineage_repo.fetch_node_with_neighbors(user.tenant_id, safe_repo, body.branch, body.asset_id)
    if not node_data:
        raise HTTPException(status_code=404, detail="Node not found")

    node = node_data["node"]
    file_path = node.get("file_path") or node.get("file") or ""
    func_name = node.get("name") or ""

    clone_dir = os.path.abspath(os.path.join("output", "repos", f"{safe_repo}-{body.branch}"))
    if not os.path.isdir(clone_dir):
        return {"ok": False, "error": "Repository not cloned yet. Run the crawler first.", "stdout": ""}

    venv_dir = os.path.abspath(os.path.join("output", "venvs", f"{safe_repo}-{body.branch}"))
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _REPO_EXECUTOR,
        lambda: _run_in_repo(clone_dir, venv_dir, file_path, func_name, body.args, body.edited_source),
    )
    return result


# ── Test cases ─────────────────────────────────────────────────────────────────

@router.get("/test-cases")
async def list_test_cases(
    request: Request,
    repo: str,
    function_name: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    tc_repo = TestCaseRepository(session)
    cases = await tc_repo.list(user.tenant_id, safe_repo, function_name)
    return [{"id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected,
              "created_at": tc.created_at.isoformat()} for tc in cases]


@router.post("/test-cases")
async def create_test_case(
    request: Request,
    body: SaveTestCaseRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(body.repo)
    tc_repo = TestCaseRepository(session)
    tc = await tc_repo.create(tenant_id=user.tenant_id, repo=safe_repo,
                               function_name=body.function_name, label=body.label,
                               args=body.args, expected=body.expected)
    return {"id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected}


@router.post("/test-cases/bulk")
async def bulk_create_test_cases(
    request: Request,
    body: BulkSaveTestCasesRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(body.repo)
    tc_repo = TestCaseRepository(session)
    created = []
    for i, case in enumerate(body.cases):
        if not isinstance(case, dict):
            continue
        tc = await tc_repo.create(tenant_id=user.tenant_id, repo=safe_repo,
                                   function_name=body.function_name,
                                   label=str(case.get("label") or f"Test {i + 1}"),
                                   args=case.get("args") or {}, expected=case.get("expected", None))
        created.append({"id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected})
    return created


@router.post("/generate-test-cases")
async def generate_test_cases(
    request: Request,
    body: GenerateTestCasesRequest,
    session: AsyncSession = Depends(get_session),
):
    import httpx
    await _get_user(request, session)
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not api_key:
        return {"error": "LITELLM_API_KEY not set", "cases": []}

    prompt = f"""Generate 6-10 diverse test cases for this Python function. Return ONLY a JSON array.\nFormat: [{{"label": "name", "args": {{"param": value}}}}]\n\nFunction:\n{body.source}"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://llmproxy.atlan.dev/chat/completions",
                headers={"x-litellm-api-key": api_key, "Content-Type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 2048,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        if text.startswith("```"):
            text = "\n".join(l for l in text.splitlines() if not l.strip().startswith("```")).strip()
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return {"error": "No JSON array found", "cases": []}
        raw = text[start:end + 1]
        raw = re.sub(r'\bNone\b', 'null', raw)
        raw = re.sub(r'\bTrue\b', 'true', raw)
        raw = re.sub(r'\bFalse\b', 'false', raw)
        raw = re.sub(r',\s*([\]}])', r'\1', raw)
        cases = json.loads(raw)
        return {"cases": cases if isinstance(cases, list) else []}
    except Exception as exc:
        return {"error": str(exc), "cases": []}


@router.patch("/test-cases/{tc_id}/expected")
async def assert_test_case(
    request: Request,
    tc_id: int,
    body: AssertTestCaseRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    tc_repo = TestCaseRepository(session)
    updated = await tc_repo.update_expected(user.tenant_id, tc_id, body.expected)
    if not updated:
        raise HTTPException(status_code=404, detail="Test case not found")
    return {"ok": True}


@router.delete("/test-cases/{tc_id}")
async def delete_test_case(
    request: Request,
    tc_id: int,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    tc_repo = TestCaseRepository(session)
    deleted = await tc_repo.delete(user.tenant_id, tc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Test case not found")
    return {"ok": True}


@router.post("/run-test-cases")
async def run_test_cases_endpoint(
    request: Request,
    body: RunTestCasesRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(body.repo)
    tc_repo = TestCaseRepository(session)
    lineage_repo = LineageRepository(session)

    cases = await tc_repo.list(user.tenant_id, safe_repo, body.function_name)
    if not cases:
        return []

    node = await lineage_repo.fetch_node_by_name(user.tenant_id, safe_repo, body.branch, body.function_name)
    if not node:
        return [{"id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected,
                 "ok": False, "error": "Function not found", "result": None, "stdout": None,
                 "passed": False if tc.expected is not None else None} for tc in cases]

    file_path = node.get("file_path") or node.get("file") or ""
    func_name = node.get("name") or ""
    clone_dir = os.path.abspath(os.path.join("output", "repos", f"{safe_repo}-{body.branch}"))
    if not os.path.isdir(clone_dir):
        return [{"id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected,
                 "ok": False, "error": "Repository not cloned. Run the crawler first.",
                 "result": None, "stdout": None,
                 "passed": False if tc.expected is not None else None} for tc in cases]

    venv_dir = os.path.abspath(os.path.join("output", "venvs", f"{safe_repo}-{body.branch}"))
    loop = asyncio.get_running_loop()

    async def _run_one(tc):
        run_result = await loop.run_in_executor(
            _REPO_EXECUTOR,
            lambda tc=tc: _run_in_repo(clone_dir, venv_dir, file_path, func_name, tc.args, body.edited_source),
        )
        passed = None
        if tc.expected is not None:
            passed = _deep_equal(run_result.get("result"), tc.expected) if run_result.get("ok") else False
        return {"id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected,
                "ok": run_result.get("ok", False), "result": run_result.get("result"),
                "error": run_result.get("error"), "stdout": run_result.get("stdout"), "passed": passed}

    return list(await asyncio.gather(*[_run_one(tc) for tc in cases]))
