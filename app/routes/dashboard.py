from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client


class RunFunctionRequest(BaseModel):
    source: str
    args: Dict[str, Any] = {}
    mock_globals: Dict[str, Any] = {}
    compare_source: Optional[str] = None
    chain_sources: list = []
    module_context: Optional[Dict[str, Any]] = None  # imports + literal globals from the source file


class AnalyzeFunctionRequest(BaseModel):
    source: str


class SuggestMocksRequest(BaseModel):
    source: str
    callee_sources: list = []
    free_names: list = []   # [{name, callable}]

from app.db import get_session
from app.repositories.lineage_repo import LineageRepository, normalize_repo_name
from app.repositories.test_case_repo import TestCaseRepository
from app.repositories.user_repo import UserRepository
from app.workflow import TASK_QUEUE, CodeCrawlerWorkflow

router = APIRouter()
templates = Jinja2Templates(directory="templates")


async def _get_user(request: Request, session: AsyncSession) -> Dict[str, Any]:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not logged in")
    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(int(user_id))
    if not user or not user.is_active:
        raise HTTPException(status_code=403, detail="User inactive or not found")
    return user


def _temporal(request: Request) -> Client:
    client = request.app.state.temporal_client
    assert client is not None, "Temporal client not initialised"
    return client


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    lineage_repo = LineageRepository(session)
    repo_branches = await lineage_repo.list_repo_branches(user.tenant_id)
    repo_map: dict = defaultdict(list)
    for x in repo_branches:
        repo_map[x["repo"]].append(x["branch"])

    repos_grouped = [
        {
            "repo": repo,
            "repo_q": quote(repo, safe=""),
            "branches": [{"branch": b, "branch_q": quote(b, safe="")} for b in branches],
        }
        for repo, branches in repo_map.items()
    ]
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"tenant_id": user.tenant_id, "repos": repos_grouped},
    )


@router.post("/crawl")
async def crawl(
    request: Request,
    github_repo_url: str = Form(...),
    branch: str = Form("main"),
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    client = _temporal(request)

    handle = await client.start_workflow(
        CodeCrawlerWorkflow.run,
        args=[github_repo_url, branch, "python", None, user.tenant_id],
        id=f"code-crawler-{user.tenant_id}-{branch}-{github_repo_url.rsplit('/', 1)[-1]}",
        task_queue=TASK_QUEUE,
    )
    _ = handle

    return RedirectResponse(
        url=f"/lineage-ui?repo={quote(github_repo_url, safe='')}&branch={quote(branch, safe='')}",
        status_code=303,
    )


@router.get("/lineage-data")
async def get_lineage_data(
    request: Request,
    repo: str,
    branch: str = "main",
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.fetch_lineage_data(user.tenant_id, safe_repo, branch)


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
    data = await lineage_repo.fetch_node_with_neighbors(
        user.tenant_id, safe_repo, branch, asset_id
    )
    if not data:
        raise HTTPException(status_code=404, detail="Node not found")
    return data


@router.get("/lineage-ui", response_class=HTMLResponse)
async def lineage_ui(
    request: Request,
    repo: str,
    branch: str = "main",
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    return templates.TemplateResponse(
        request, "lineage.html", {"repo": repo, "branch": branch}
    )


@router.get("/asset", response_class=HTMLResponse)
async def asset_view(
    request: Request,
    repo: str,
    branch: str,
    asset_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    return templates.TemplateResponse(
        request, "asset.html", {"repo": repo, "branch": branch, "asset_id": asset_id}
    )


@router.get("/changes", response_class=HTMLResponse)
async def changes_view(
    request: Request,
    repo: str,
    branch: str,
    session: AsyncSession = Depends(get_session),
):
    await _get_user(request, session)
    return templates.TemplateResponse(
        request, "changes.html", {"repo": repo, "branch": branch}
    )


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
    # Full mock object: attribute access, item access, callable, awaitable, context manager.
    # Missing keys/indices return an empty _AttrDict so code like fields[0] doesn't crash.
    # If the dict has a "return_value" key, calling it returns that value.
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
    # Recursively convert dicts to _AttrDict so attribute access works on mock values
    if isinstance(v, dict):
        return _AttrDict({k: _wrap_value(vv) for k, vv in v.items()})
    if isinstance(v, list):
        return [_wrap_value(i) for i in v]
    return v

# Auto-import any importable free names found in the source
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

# Inject module-level literal globals (items={}, TIMEOUT=30, etc.) — lower priority than user mocks
for _k, _v in (_module_context.get("globals") or {}).items():
    if _k not in globals():
        globals()[_k] = _wrap_value(_v)

# Try to exec module-level import statements; failed imports stay as auto-mocked _AttrDict
for _imp in (_module_context.get("imports") or []):
    try:
        exec(compile(_imp, "<module_import>", "exec"), globals())
    except Exception:
        pass

# Inject user-provided mock globals (highest priority — overrides everything above)
for _k, _v in _mock_globals.items():
    globals()[_k] = _wrap_value(_v)

# Fix: names used in 'except X' or 'raise X(...)' must be BaseException subclasses.
# Collect from both ExceptHandler nodes and Raise nodes, then ensure they are real exceptions.
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

# Fix: names used as function calls that are either (a) mocked with a non-callable
# JSON value or (b) completely missing from globals (e.g. FastAPI dep factories used
# as default values like Cookie(), Depends(...)).  Both cases get a callable _AttrDict
# so the 'def' statement succeeds when Python evaluates default-value expressions.
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

# Fix exception classes in chain sources too
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

# Exec chain sources first so callee functions are real implementations
for _cs in _chain_sources:
    _fix_exc_classes(_cs)
    try:
        exec(compile(_cs, "<chain>", "exec"), globals())
    except Exception:
        pass

exec(compile(_source, "<function>", "exec"), globals())

# Build call trace: wrap each chain function so calls are recorded
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
    # DI markers like Cookie(default=None), Query(default=None), Depends(...) are mocked
    # as _AttrDict() callables. When the user doesn't supply a value, the param's default
    # becomes _AttrDict() — truthy and not None — breaking 'if x is None' checks.
    # Substitute None so the function sees what it would see in a real request with no value.
    for _pname, _pparam in _sig_params.items():
        if _pname not in _call_args and _pparam.default is not _inspect.Parameter.empty:
            if isinstance(_pparam.default, _AttrDict):
                _call_args[_pname] = None
    if _param_names and _param_names[0] in ("self", "cls"):
        # Build a synthetic class: chain sources + target, all as real method impls.
        # Any other self.attr access falls through to a chainable _ChainMock.
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
    # Rebuild call args properly: unpack *args and **kwargs params instead of
    # passing them as keyword arguments (which would pollute the function's **kwargs).
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


def _exec_function(source: str, args: Dict[str, Any], mock_globals: Dict[str, Any] | None = None, chain_sources: list | None = None, module_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    m = re.search(r"^\s*(?:async\s+)?def\s+(\w+)", source, re.MULTILINE)
    if not m:
        return {"ok": False, "error": "No function definition found in source", "stdout": ""}
    funcname = m.group(1)
    payload = json.dumps({"source": source, "args": args, "funcname": funcname, "mock_globals": mock_globals or {}, "chain_sources": chain_sources or [], "module_context": module_context or {}})
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _RUNNER_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=5,
        )
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

    fn_def = next(
        (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
        None,
    )
    if not fn_def:
        return {"error": "No function found", "needs_mock": [], "auto_imported": []}

    builtin_names = set(dir(builtins)) | set(dir(__import__("typing")))

    def _collect_assign_targets(t: ast.expr, out: set) -> None:
        """Recursively collect names from assignment targets (handles tuple unpacking)."""
        if isinstance(t, ast.Name):
            out.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for elt in t.elts:
                _collect_assign_targets(elt, out)

    # Collect names defined locally inside the function
    local_names: set = set()
    # Names that appear in 'except X as e' — X is an exception class (auto-fixed), e is local
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
            # 'except Foo as e' — e is local, Foo is auto-fixed by the runner
            if node.name:
                local_names.add(node.name)
            if node.type:
                if isinstance(node.type, ast.Name):
                    exc_class_names.add(node.type.id)
                elif isinstance(node.type, ast.Tuple):
                    for elt in node.type.elts:
                        if isinstance(elt, ast.Name):
                            exc_class_names.add(elt.id)

    # Also collect module-level names (imports, defs outside the function)
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

    # Find all Name loads that are genuinely free (not local, not builtin, not exc classes)
    free_names: set = set()
    for node in ast.walk(fn_def):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            name = node.id
            if name not in builtin_names and name not in local_names and name not in exc_class_names:
                free_names.add(name)

    # Detect which free names are called directly as functions (not just attribute access)
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


def _extract_json(text: str) -> dict:
    """Extract the first {...} JSON object from an LLM response, tolerating preamble/fences."""
    # Strip markdown code fences
    if "```" in text:
        text = re.sub(r"```[^\n]*\n?", "", text)
    # Find first { and last } to grab the JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        text = text[start : end + 1]
    return json.loads(text)


@router.post("/suggest-mocks")
async def suggest_mocks(
    request: Request,
    body: SuggestMocksRequest,
    session: AsyncSession = Depends(get_session),
):
    import os
    import httpx

    await _get_user(request, session)

    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not api_key:
        return {"error": "LITELLM_API_KEY not set", "params": {}, "mocks": {}}

    # Build callee context
    callee_section = ""
    if body.callee_sources:
        callee_section = "\n\nCallee functions (will run as real implementations):\n" + \
            "\n---\n".join(f"```python\n{s}\n```" for s in body.callee_sources[:5])

    # Build free-names context
    callables = [n["name"] for n in body.free_names if isinstance(n, dict) and n.get("callable")]
    values    = [n["name"] for n in body.free_names if isinstance(n, dict) and not n.get("callable")]
    mocks_desc = ""
    if callables:
        mocks_desc += f"\nCallable mocks (provide the JSON return value): {', '.join(callables)}"
    if values:
        mocks_desc += f"\nValue mocks (provide the JSON value): {', '.join(values)}"

    prompt = f"""You are helping test a Python function by suggesting minimal mock values.

Function to test:
```python
{body.source}
```
{callee_section}

Suggest values for:
1. Each function parameter (skip self/cls)
2. Each external global that needs mocking:{mocks_desc}

Rules:
- For regex patterns use ".*"
- For SQL snippets use ""
- For callables returning tuples use a JSON array with the right number of elements (check the unpacking in the function)
- Use simple realistic values inferred from names and type hints
- Return ONLY a JSON object, no explanation, no markdown fences

Return format:
{{"params": {{"param_name": value}}, "mocks": {{"global_name": value}}}}"""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://llmproxy.atlan.dev/chat/completions",
                headers={
                    "x-litellm-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        return _extract_json(text)
    except Exception as exc:
        return {"error": str(exc), "params": {}, "mocks": {}}


class AnalyzeAssetRequest(BaseModel):
    asset_id: str
    repo: str
    branch: str


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

    node_data = await lineage_repo.fetch_node_with_neighbors(
        user.tenant_id, safe_repo, body.branch, body.asset_id
    )
    if not node_data:
        raise HTTPException(status_code=404, detail="Node not found")

    source = node_data["node"].get("source") or ""

    # BFS over callees (API "upstream" = functions this one calls)
    callee_sources: list = []
    visited: set = {body.asset_id}
    # seed queue with direct callees
    queue = [(nd["id"], nd.get("source")) for nd in node_data.get("upstream", [])]

    while queue and len(visited) < 50:
        callee_id, callee_src = queue.pop(0)
        if callee_id in visited:
            continue
        visited.add(callee_id)
        if callee_src:
            callee_sources.append(callee_src)
        sub = await lineage_repo.fetch_node_with_neighbors(
            user.tenant_id, safe_repo, body.branch, callee_id
        )
        if sub:
            for sc in sub.get("upstream", []):
                if sc["id"] not in visited:
                    queue.append((sc["id"], sc.get("source")))

    # Analyse source for undefined free names
    analysis = _analyze_function(source) if source else {"needs_mock": [], "auto_imported": []}

    # Build set of names already provided by the callee chain (so we don't ask for mocks)
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

    # Also exclude names provided by module_context (imports + literal globals)
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

    return {
        "needs_mock": needs_mock,
        "callee_sources": callee_sources,
        "callee_count": len(callee_sources),
        "module_context": module_context,
    }


class SuggestFixRequest(BaseModel):
    source: str
    error: str
    callee_sources: list = []


@router.post("/suggest-fix")
async def suggest_fix(
    request: Request,
    body: SuggestFixRequest,
    session: AsyncSession = Depends(get_session),
):
    import os
    import httpx

    await _get_user(request, session)

    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not api_key:
        return {"error": "LITELLM_API_KEY not set", "params": {}, "mocks": {}}

    # Re-analyze source to find what names need mocking
    analysis = _analyze_function(body.source)
    free_names = analysis.get("needs_mock", [])
    callables = [n["name"] for n in free_names if n.get("callable")]
    values    = [n["name"] for n in free_names if not n.get("callable")]
    mocks_desc = ""
    if callables:
        mocks_desc += f"\nCallable mocks needed: {', '.join(callables)}"
    if values:
        mocks_desc += f"\nValue mocks needed: {', '.join(values)}"

    callee_section = ""
    if body.callee_sources:
        callee_section = "\n\nCallee functions included in chain:\n" + \
            "\n---\n".join(f"```python\n{s}\n```" for s in body.callee_sources[:5])

    prompt = f"""You are debugging a Python function that produced an error. Analyse the error and suggest corrected parameter values and mock globals that would fix it.

Function:
```python
{body.source}
```
{callee_section}

Error:
```
{body.error}
```

External names needing mock values:{mocks_desc if mocks_desc else " (none detected)"}

Rules:
- Read the traceback carefully to understand what value caused the error
- Suggest corrected values that avoid the error
- For callables returning tuples, use a JSON array with the right number of elements
- For regex patterns use ".*", for SQL use ""
- Return ONLY a JSON object, no explanation, no markdown fences

Return format:
{{"params": {{"param_name": value}}, "mocks": {{"global_name": value}}}}"""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://llmproxy.atlan.dev/chat/completions",
                headers={
                    "x-litellm-api-key": api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        return _extract_json(text)
    except Exception as exc:
        return {"error": str(exc), "params": {}, "mocks": {}}


# ── Real-repo runner ──────────────────────────────────────────────────────────

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
    # func_name is a qualname like "pkg.module.ClassName.method"; strip the module prefix
    _local_func = _func_name
    if _local_func.startswith(_module_name + "."):
        _local_func = _local_func[len(_module_name) + 1:]
    # If an edited version exists, exec it into the real module's namespace so it has
    # all real imports but uses the edited logic. Re-derive _local_func from the edit.
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
    # Unwrap descriptors: cached_property → .func, property → .fget, etc.
    if not callable(_fn):
        for _attr in ('func', 'fget', '__func__', '__wrapped__'):
            _candidate = getattr(_fn, _attr, None)
            if callable(_candidate):
                _fn = _candidate
                break
    _sig = inspect.signature(_fn)
    _param_names = list(_sig.parameters.keys())

    # Auto-handle self/cls for instance/class methods
    _positional_prefix = []
    if _param_names and _param_names[0] in ('self', 'cls'):
        _first = _param_names[0]
        if _first == 'cls':
            # classmethod: pass the class itself
            _cls_part = _local_func.rsplit('.', 1)[0] if '.' in _local_func else None
            if _cls_part:
                try:
                    _cls_obj = _mod
                    for _p in _cls_part.split('.'):
                        _cls_obj = getattr(_cls_obj, _p)
                    _positional_prefix = [_cls_obj]
                except Exception:
                    pass
        else:
            # instance method: create bare instance, populate from user-provided 'self' dict
            _cls_part = _local_func.rsplit('.', 1)[0] if '.' in _local_func else None
            _self_instance = None
            if _cls_part:
                try:
                    _cls_obj = _mod
                    for _p in _cls_part.split('.'):
                        _cls_obj = getattr(_cls_obj, _p)
                    _self_instance = _cls_obj.__new__(_cls_obj)
                    _self_attrs = _args.get('self', {})
                    if isinstance(_self_attrs, dict):
                        for _k, _v in _self_attrs.items():
                            try:
                                setattr(_self_instance, _k, _v)
                            except Exception:
                                pass
                except Exception:
                    pass
            if _self_instance is None:
                # fallback mock self
                class _MockSelf:
                    def __getattr__(self, n): return _MockSelf()
                    def __call__(self, *a, **kw): return _MockSelf()
                    async def __aenter__(self): return self
                    async def __aexit__(self, *a): pass
                    def __await__(self):
                        async def _r(): return _MockSelf()
                        return _r().__await__()
                _self_instance = _MockSelf()
            _positional_prefix = [_self_instance]

    _call_args = {}
    for _pname, _pparam in _sig.parameters.items():
        if _pname in ('self', 'cls'):
            continue
        if _pname not in _args:
            continue
        _val = _args[_pname]
        _ann = _pparam.annotation
        # Coerce dict → annotated type (e.g. Pydantic models, dataclasses)
        if (
            _ann is not inspect.Parameter.empty
            and isinstance(_ann, type)
            and isinstance(_val, dict)
            and not isinstance(_val, _ann)
        ):
            if hasattr(_ann, 'model_validate'):      # Pydantic v2
                _val = _ann.model_validate(_val)
            elif hasattr(_ann, 'parse_obj'):          # Pydantic v1
                _val = _ann.parse_obj(_val)
            else:
                _val = _ann(**_val)
        _call_args[_pname] = _val
    if asyncio.iscoroutinefunction(_fn):
        _result = asyncio.run(_fn(*_positional_prefix, **_call_args))
    else:
        _result = _fn(*_positional_prefix, **_call_args)
    sys.stdout = _real
    try:
        _r = json.dumps(_result, default=str)
    except Exception:
        _r = json.dumps(repr(_result))
    print(json.dumps({"ok": True, "result": json.loads(_r), "stdout": _buf.getvalue()}))
except Exception as _e:
    sys.stdout = _real
    print(json.dumps({"ok": False, "error": str(_e), "stdout": _buf.getvalue(),
                      "traceback": traceback.format_exc()}))
"""

_DOCKER_IMAGE = "python:3.11-slim"

# Bounded thread pool — web server threads never exhausted by repo runs
_REPO_EXECUTOR = ThreadPoolExecutor(max_workers=50, thread_name_prefix="repo-runner")



def _build_venv_setup_script(clone_dir: str) -> str:
    """Return a bash one-liner that creates and populates /venv inside the container."""
    cmds = [
        "python -m venv /venv",
        "/venv/bin/pip install --upgrade pip -q",
    ]
    pkg_files = ["pyproject.toml", "setup.py", "setup.cfg"]
    if any(os.path.isfile(os.path.join(clone_dir, f)) for f in pkg_files):
        cmds.append("/venv/bin/pip install -e . -q")
        pyproject_path = os.path.join(clone_dir, "pyproject.toml")
        if os.path.isfile(pyproject_path):
            try:
                import tomllib
                with open(pyproject_path, "rb") as _f:
                    _pdata = tomllib.load(_f)
                extras = list((_pdata.get("project", {}).get("optional-dependencies", {}) or {}).keys())
                if extras:
                    extras_str = ",".join(extras)
                    cmds.append(f"/venv/bin/pip install -e '.[{extras_str}]' -q")
                dep_groups = list((_pdata.get("dependency-groups", {}) or {}).keys())
                for grp in dep_groups:
                    cmds.append(f"/venv/bin/pip install --group {grp} -q")
            except Exception:
                pass
    for req_name in sorted(os.listdir(clone_dir)):
        if req_name.startswith("requirements") and req_name.endswith(".txt"):
            cmds.append(f"/venv/bin/pip install -r {req_name} -q --exists-action i")
    return " && ".join(cmds)


def _run_in_repo(
    clone_dir: str,
    venv_dir: str,
    file_path: str,
    func_name: str,
    args: Dict[str, Any],
    edited_source: Optional[str] = None,
) -> Dict[str, Any]:
    rel = file_path.replace("\\", "/").lstrip("/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    module_name = rel.replace("/", ".")

    C_VENV = "/venv"
    C_REPO = "/repo"
    C_PYTHON = "/venv/bin/python"

    # ── Venv setup (once per repo) ───────────────────────────────────────────
    setup_log = ""
    venv_marker = os.path.join(venv_dir, "pyvenv.cfg")

    if not os.path.isfile(venv_marker):
        os.makedirs(venv_dir, exist_ok=True)
        script = _build_venv_setup_script(clone_dir)
        p = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{venv_dir}:{C_VENV}",
                "-v", f"{clone_dir}:{C_REPO}",
                "-w", C_REPO,
                _DOCKER_IMAGE,
                "bash", "-c", script,
            ],
            capture_output=True, text=True, timeout=600,
        )
        setup_log = (p.stdout + p.stderr).strip()
        if p.returncode != 0:
            return {"ok": False, "error": f"Docker venv setup failed: {p.stderr.strip()}", "stdout": "", "setup_log": setup_log}
        if not os.path.isfile(venv_marker):
            return {"ok": False, "error": f"venv setup done but pyvenv.cfg missing at {venv_dir}", "stdout": "", "setup_log": setup_log}

    # ── PYTHONPATH ────────────────────────────────────────────────────────────
    rel_dir = os.path.dirname(rel)
    host_file_dir = os.path.dirname(os.path.join(clone_dir, file_path.lstrip("/")))
    pp_parts = [C_REPO]
    if (
        rel_dir
        and os.path.isdir(host_file_dir)
        and not os.path.isfile(os.path.join(host_file_dir, "__init__.py"))
    ):
        pp_parts.append(f"{C_REPO}/{rel_dir}")
    pythonpath = ":".join(pp_parts)

    # ── Execute ───────────────────────────────────────────────────────────────
    payload = json.dumps({"args": args, "module": module_name, "func": func_name, "edited_source": edited_source})
    try:
        cmd = [
            "docker", "run", "--rm", "-i",
            "--network", "none",
            "--memory", "256m",
            "--cpus", "0.5",
            "-v", f"{venv_dir}:{C_VENV}:ro",
            "-v", f"{clone_dir}:{C_REPO}:ro",
            "-w", C_REPO,
            "-e", f"PYTHONPATH={pythonpath}",
            _DOCKER_IMAGE,
            C_PYTHON, "-c", _REPO_RUNNER_SCRIPT,
        ]
        proc = subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=30)
        out = proc.stdout.strip()
        if out:
            result = json.loads(out)
            if setup_log:
                result["setup_log"] = setup_log
            return result
        err = proc.stderr.strip()
        return {"ok": False, "error": err or "No output from subprocess", "stdout": "", "setup_log": setup_log}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Execution timed out (30s)", "stdout": ""}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stdout": ""}


@router.get("/branch-compare", response_class=HTMLResponse)
async def branch_compare_page(
    request: Request,
    repo: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    branches = await lineage_repo.list_branches_for_repo(user.tenant_id, safe_repo)
    return templates.TemplateResponse(
        request,
        "branch_compare.html",
        {"repo": safe_repo, "branches": branches, "tenant_id": user.tenant_id},
    )


@router.get("/api/branch-functions")
async def api_branch_functions(
    request: Request,
    repo: str,
    branch: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    return await lineage_repo.list_functions_for_branch(user.tenant_id, safe_repo, branch)


@router.get("/api/function-source")
async def api_function_source(
    request: Request,
    repo: str,
    branch: str,
    name: str,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(repo)
    lineage_repo = LineageRepository(session)
    node = await lineage_repo.fetch_node_by_name(user.tenant_id, safe_repo, branch, name)
    if not node:
        raise HTTPException(status_code=404, detail="Function not found")
    return node


class SaveTestCaseRequest(BaseModel):
    repo: str
    function_name: str
    label: str
    args: Dict[str, Any] = {}
    expected: Optional[Any] = None


class BulkSaveTestCasesRequest(BaseModel):
    repo: str
    function_name: str
    cases: list  # [{label?, args, expected?}]


class GenerateTestCasesRequest(BaseModel):
    source: str


class AssertTestCaseRequest(BaseModel):
    expected: Any


class RunTestCasesRequest(BaseModel):
    repo: str
    branch: str
    function_name: str
    edited_source: Optional[str] = None


class RunInRepoRequest(BaseModel):
    asset_id: str
    repo: str
    branch: str
    args: Dict[str, Any] = {}
    edited_source: Optional[str] = None


@router.post("/run-in-repo")
async def run_in_repo_endpoint(
    request: Request,
    body: RunInRepoRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(body.repo)
    lineage_repo = LineageRepository(session)

    node_data = await lineage_repo.fetch_node_with_neighbors(
        user.tenant_id, safe_repo, body.branch, body.asset_id
    )
    if not node_data:
        raise HTTPException(status_code=404, detail="Node not found")

    node = node_data["node"]
    file_path = node.get("file_path") or node.get("file") or ""
    func_name = node.get("name") or ""

    clone_dir = os.path.abspath(os.path.join("output", "repos", f"{safe_repo}-{body.branch}"))
    if not os.path.isdir(clone_dir):
        return {
            "ok": False,
            "error": "Repository not cloned yet. Run the crawler first.",
            "stdout": "",
        }

    venv_dir = os.path.abspath(os.path.join("output", "venvs", f"{safe_repo}-{body.branch}"))

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        _REPO_EXECUTOR,
        lambda: _run_in_repo(clone_dir, venv_dir, file_path, func_name, body.args, body.edited_source),
    )
    return result


# ── Test cases ────────────────────────────────────────────────────────────────

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
    return [
        {
            "id": tc.id,
            "label": tc.label,
            "args": tc.args,
            "expected": tc.expected,
            "created_at": tc.created_at.isoformat(),
        }
        for tc in cases
    ]


@router.post("/test-cases")
async def create_test_case(
    request: Request,
    body: SaveTestCaseRequest,
    session: AsyncSession = Depends(get_session),
):
    user = await _get_user(request, session)
    safe_repo = normalize_repo_name(body.repo)
    tc_repo = TestCaseRepository(session)
    tc = await tc_repo.create(
        tenant_id=user.tenant_id,
        repo=safe_repo,
        function_name=body.function_name,
        label=body.label,
        args=body.args,
        expected=body.expected,
    )
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
        args = case.get("args") or {}
        label = case.get("label") or f"Test {i + 1}"
        expected = case.get("expected", None) if "expected" in case else None
        tc = await tc_repo.create(
            tenant_id=user.tenant_id,
            repo=safe_repo,
            function_name=body.function_name,
            label=str(label),
            args=args,
            expected=expected,
        )
        created.append({"id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected})
    return created


@router.post("/generate-test-cases")
async def generate_test_cases(
    request: Request,
    body: GenerateTestCasesRequest,
    session: AsyncSession = Depends(get_session),
):
    import os
    import httpx

    await _get_user(request, session)
    api_key = os.environ.get("LITELLM_API_KEY", "")
    if not api_key:
        return {"error": "LITELLM_API_KEY not set", "cases": []}

    prompt = f"""You are a Python testing expert. Given this function, generate diverse test cases.

Function:
{body.source}

Generate 6-10 test cases covering: typical inputs, edge cases (empty, zero, None), boundary values, and error-prone scenarios.
Each test case must be runnable — only use values the function can actually receive.
Do NOT include 'self' or 'cls' in args.

Return ONLY a JSON array, no explanation, no markdown fences.
Format: [{{"label": "descriptive name", "args": {{"param1": value, "param2": value}}}}]"""

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://llmproxy.atlan.dev/chat/completions",
                headers={"x-litellm-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 2048,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
        # Extract the JSON array — find outermost [ ... ]
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end < start:
            return {"error": "No JSON array found in response", "cases": []}
        cases = json.loads(text[start:end + 1])
        if not isinstance(cases, list):
            return {"error": "Unexpected response format", "cases": []}
        return {"cases": cases}
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

    node = await lineage_repo.fetch_node_by_name(
        user.tenant_id, safe_repo, body.branch, body.function_name
    )
    not_found_resp = [
        {
            "id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected,
            "ok": False, "error": "Function not found in this branch",
            "result": None, "stdout": None, "passed": False if tc.expected is not None else None,
        }
        for tc in cases
    ]
    if not node:
        return not_found_resp

    file_path = node.get("file_path") or node.get("file") or ""
    func_name = node.get("name") or ""

    clone_dir = os.path.abspath(os.path.join("output", "repos", f"{safe_repo}-{body.branch}"))
    if not os.path.isdir(clone_dir):
        return [
            {
                "id": tc.id, "label": tc.label, "args": tc.args, "expected": tc.expected,
                "ok": False, "error": "Repository not cloned. Run the crawler first.",
                "result": None, "stdout": None, "passed": False if tc.expected is not None else None,
            }
            for tc in cases
        ]

    venv_dir = os.path.abspath(os.path.join("output", "venvs", f"{safe_repo}-{body.branch}"))
    loop = asyncio.get_running_loop()

    async def _run_one(tc):
        run_result = await loop.run_in_executor(
            _REPO_EXECUTOR,
            lambda tc=tc: _run_in_repo(clone_dir, venv_dir, file_path, func_name, tc.args, body.edited_source),
        )
        passed = None
        if tc.expected is not None:
            if run_result.get("ok"):
                passed = run_result.get("result") == tc.expected
            else:
                passed = False
        return {
            "id": tc.id,
            "label": tc.label,
            "args": tc.args,
            "expected": tc.expected,
            "ok": run_result.get("ok", False),
            "result": run_result.get("result"),
            "error": run_result.get("error"),
            "stdout": run_result.get("stdout"),
            "passed": passed,
        }

    results = await asyncio.gather(*[_run_one(tc) for tc in cases])
    return list(results)
