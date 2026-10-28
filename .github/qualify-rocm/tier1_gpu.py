#!/usr/bin/env python3
"""
Tier 1 -- GPU functional inference on a real AMD GPU (the gfx1151 runners).

Drives llama-server over HTTP rather than llama-cli, because llama-server is the
binary Lemonade actually launches.

The archive is run exactly as a user gets it: unzipped into an empty folder and
started from there, with nothing else on the library search path. These
archives carry their own ROCm, so this tier never installs one. Any ROCm the
runner happens to have must not be used, and T1.6 proves it was not.

Checks (ids match lemonade-sdk/llama.cpp where the meaning is the same):
  T1.1  server boot             -- llama-server starts and serves /health.
  T1.2  GPU really did the work -- GATING. A ROCm device was selected AND a
                                   non-zero amount of model memory was placed on
                                   it. The familiar "offloaded N/N layers to GPU"
                                   line is NOT accepted: llama.cpp prints it from
                                   what -ngl requested, so it appears unchanged
                                   on a machine with no GPU at all.
  T1.3  response well-formed    -- GATING. Non-empty, valid UTF-8, no
                                   replacement characters, sensible length,
                                   proper finish_reason.
  T1.4  greedy determinism      -- NON-gating. See check_determinism().
  T1.5  reasoning pass          -- NON-gating. The model thought, answered, and
                                   stopped cleanly.
  T1.6  ROCm loaded from the archive -- GATING. Every ROCm library inside the
                                   running server was loaded from the archive's
                                   own folder, not from a ROCm installed on the
                                   machine. A missing or broken bundled library
                                   can otherwise be papered over by the runner's
                                   copy, and the archive then fails on a clean
                                   machine.

The machine's kernel, GPU driver and any system ROCm are recorded with the
results, so a later "it broke on my machine" can be compared with the last
configuration that passed.

Usage:
    python3 tier1_gpu.py --meta qualify-meta-ubuntu-gfx1151.json \
        --archive-dir ./artifact --output tier1.json
"""

import argparse
import atexit
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import families  # noqa: E402
import report  # noqa: E402

READY_TIMEOUT = 600
REQUEST_TIMEOUT = 180

# Flags llama-server is launched with, in addition to the model, host, port and
# -ngl 99. `-lv 4` raises the log verbosity, and it is not optional: at the
# default setting llama-server prints about nine lines and none of the device or
# memory information T1.2 relies on.
SERVER_ARGS = ["--ctx-size", "4096", "--jinja", "--metrics", "-lv", "4"]

# Evidence that the GPU genuinely did the work. Both name a ROCm device, so
# neither can appear unless one was found and used:
#   "llama_prepare_model_devices: using device ROCm0 (...) - 32612 MiB free"
#   "load_tensors:        ROCm0 model buffer size =   358.78 MiB"
# The buffer size must be above zero: llama.cpp first sizes everything up in a
# dry run that prints "ROCm0 model buffer size = 0.00 MiB" before any memory is
# actually allocated.
DEVICE_RE = re.compile(r"using device (ROCm\d+)(?: \((.*?)\)(?= \(| -|$))?", re.M)
BUFFER_RE = re.compile(r"(ROCm\d+) model buffer size\s*=\s*([0-9.]+) MiB")

# Deliberately NOT treated as proof, despite how it reads. llama.cpp prints
# "offloaded 29/29 layers to GPU" from the number of layers REQUESTED through
# -ngl, before any GPU is involved. It is recorded for context only.
REQUESTED_OFFLOAD_RE = re.compile(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU")

# Lines that explain WHY the GPU went unused, so the failure message can say
# something more useful than "no evidence found".
CPU_FALLBACK_HINTS = [
    re.compile(r"failed to initialize ROCm", re.I),
    re.compile(r"no ROCm-capable device", re.I),
    re.compile(r"no usable GPU", re.I),
    re.compile(r"offloaded\s+0/\d+\s+layers to GPU"),
]

# Environment variables that would let the server borrow a ROCm installed on the
# machine, or pretend the GPU is a different one. Removed before launch, and
# recorded if the runner had them set.
FOREIGN_ROCM_ENV = (
    "LD_LIBRARY_PATH", "ROCM_PATH", "HIP_PATH", "HIP_DEVICE_LIB_PATH",
    "HSA_OVERRIDE_GFX_VERSION", "ROCBLAS_TENSILE_LIBPATH", "HIPBLASLT_TENSILE_LIBPATH",
)

# Library names that belong to ROCm (or to the HIP backend), for T1.6.
ROCM_LIB_PREFIXES = (
    "libamdhip64", "libhsa-runtime64", "libamd_comgr", "librocblas", "libhipblas",
    "librocsolver", "librocm", "libroctx", "librocprofiler", "libtensilelite",
    "liborigami", "librocroller", "libhiprtc", "libggml-hip",
)
ROCM_DLL_PREFIXES = (
    "amdhip64", "amd_comgr", "rocblas", "hipblas", "libhipblas", "rocsolver",
    "rocm_", "origami", "libtensilelite", "hiprtc", "ggml-hip",
)
HIP_RUNTIME_PREFIX = ("libamdhip64", "amdhip64")


# ---------------------------------------------------------------------------
# Inputs this script fetches for itself
#
# Extraction and downloading live here so the job works on any runner regardless
# of which shell it has. A self-hosted machine is not guaranteed to provide the
# same tools a hosted one does.
# ---------------------------------------------------------------------------

MODEL_URL = ("https://huggingface.co/unsloth/Qwen3-0.6B-GGUF/resolve/main/"
             "Qwen3-0.6B-Q4_0.gguf")


def extract_archive(archive, dest):
    """Unpack a release archive and return the folder holding the binaries."""
    os.makedirs(dest, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)
    for dirpath, _dirnames, filenames in os.walk(dest):
        if {"llama-server", "llama-server.exe"} & set(filenames):
            return dirpath
    raise SystemExit(f"::error::llama-server not found anywhere inside {archive}")


def download_model(url, path, attempts=3):
    """Fetch the test model, reusing an existing copy when there is one."""
    if os.path.exists(path) and os.path.getsize(path) > 0:
        print(f"Model already present: {path} ({os.path.getsize(path):,} bytes)")
        return path
    last = None
    for attempt in range(1, attempts + 1):
        try:
            print(f"Downloading model (attempt {attempt}/{attempts}): {url}")
            with urllib.request.urlopen(url, timeout=600) as resp, \
                    open(path, "wb") as handle:
                shutil.copyfileobj(resp, handle)
            print(f"Downloaded {os.path.getsize(path):,} bytes to {path}")
            return path
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            if os.path.exists(path):
                os.remove(path)
            time.sleep(5 * attempt)
    raise SystemExit(f"::error::could not download the test model: {last}")


# ---------------------------------------------------------------------------
# The machine this ran on
# ---------------------------------------------------------------------------

def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _windows_gpu_drivers():
    """AMD display adapters and their driver versions, from the registry."""
    import winreg  # pylint: disable=import-outside-toplevel

    found = []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return found
    with root:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not sub.isdigit():
                continue
            entry = {}
            try:
                with winreg.OpenKey(root, sub) as key:
                    for value in ("DriverDesc", "ProviderName", "DriverVersion",
                                  "DriverDate", "RadeonSoftwareVersion"):
                        try:
                            entry[value] = str(winreg.QueryValueEx(key, value)[0])
                        except OSError:
                            pass
            except OSError:
                continue
            text = " ".join(entry.values())
            if "Advanced Micro Devices" in text or "AMD" in text or "Radeon" in text:
                found.append(entry)
    return found


def runner_environment():
    env = {
        "platform": platform.platform(),
        "machine_name": platform.node(),
        "python": platform.python_version(),
        "foreign_rocm_env_removed": {k: os.environ[k] for k in FOREIGN_ROCM_ENV
                                     if k in os.environ},
    }
    if os.name == "nt":
        env["gpu_drivers"] = _windows_gpu_drivers()
        system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        env["driver_hip_runtime_in_system32"] = sorted(
            f for f in os.listdir(system32) if f.lower().startswith("amdhip64"))
    else:
        env["kernel"] = platform.release()
        env["amdgpu_module_version"] = _read("/sys/module/amdgpu/version")
        env["kfd_present"] = os.path.exists("/dev/kfd")
        env["system_rocm"] = {
            "opt_rocm_present": os.path.isdir("/opt/rocm"),
            "opt_rocm_version": _read("/opt/rocm/.info/version"),
        }
    return env


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def http_get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except (urllib.error.URLError, OSError):
        return None, b""


def http_post_json(url, payload, timeout=REQUEST_TIMEOUT):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # Decode strictly: a malformed byte sequence here is itself a finding, and
    # T1.3 wants to know about it rather than have it silently replaced.
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def build_env(exe_dir, gfx_target):
    """The environment Lemonade starts llama-server with, minus any system ROCm."""
    env = dict(os.environ)
    for key in FOREIGN_ROCM_ENV:
        env.pop(key, None)
    if os.name == "nt":
        # Windows searches the program's own folder for DLLs before anywhere
        # else, so the bundled ROCm wins without touching PATH. Lemonade sets
        # this for these targets to raise the shared-memory limit.
        if gfx_target in ("gfx1151", "gfx1152"):
            env["OCL_SET_SVM_SIZE"] = "262144"
    else:
        # Lemonade puts the archive folder on LD_LIBRARY_PATH; nothing else is
        # added here, so only the archive can supply ROCm.
        env["LD_LIBRARY_PATH"] = exe_dir
    return env


class Server:
    def __init__(self, exe, model, port, log_path, env, cwd):
        self.exe = exe
        self.model = model
        self.port = port
        self.log_path = log_path
        self.env = env
        self.cwd = cwd
        self.proc = None
        self._log = None

    def start(self):
        cmd = [self.exe, "--model", self.model, "--host", "127.0.0.1",
               "--port", str(self.port), "-ngl", "99", *SERVER_ARGS]
        print("+ " + " ".join(cmd), flush=True)
        self._log = open(self.log_path, "w", encoding="utf-8", errors="replace")
        kwargs = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(cmd, stdout=self._log,
                                     stderr=subprocess.STDOUT, env=self.env,
                                     cwd=self.cwd, **kwargs)

    def wait_ready(self, timeout):
        proc = self.proc
        if proc is None:
            return False, "llama-server was never started"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, f"llama-server exited early (rc={proc.returncode})"
            status, _ = http_get(f"http://127.0.0.1:{self.port}/health")
            if status == 200:
                return True, None
            time.sleep(3)
        return False, f"llama-server not ready within {timeout}s"

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                if os.name == "nt":
                    self.proc.terminate()
                else:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=30)
            except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        if self._log:
            self._log.close()

    def log_text(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""


# ---------------------------------------------------------------------------
# Which libraries the running server actually loaded
# ---------------------------------------------------------------------------

def _linux_loaded_files(pid):
    paths = []
    with open(f"/proc/{pid}/maps", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            parts = line.split(None, 5)
            if len(parts) == 6 and parts[5].startswith("/"):
                path = parts[5].strip()
                if path.endswith(" (deleted)"):
                    path = path[: -len(" (deleted)")]
                if path not in paths:
                    paths.append(path)
    return paths


def _windows_loaded_files(pid):
    import ctypes  # pylint: disable=import-outside-toplevel
    from ctypes import wintypes  # pylint: disable=import-outside-toplevel

    process_query_information, process_vm_read, list_modules_all = 0x0400, 0x0010, 0x03
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    psapi.EnumProcessModulesEx.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.GetModuleFileNameExW.argtypes = [
        wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]

    handle = kernel32.OpenProcess(process_query_information | process_vm_read, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), "OpenProcess failed")
    try:
        count = 1024
        needed = wintypes.DWORD()
        while True:
            modules = (wintypes.HMODULE * count)()
            if not psapi.EnumProcessModulesEx(handle, modules, ctypes.sizeof(modules),
                                              ctypes.byref(needed), list_modules_all):
                raise OSError(ctypes.get_last_error(), "EnumProcessModulesEx failed")
            if needed.value <= ctypes.sizeof(modules):
                break
            count = needed.value // ctypes.sizeof(wintypes.HMODULE) + 16
        buffer = ctypes.create_unicode_buffer(32768)
        paths = []
        for i in range(needed.value // ctypes.sizeof(wintypes.HMODULE)):
            if psapi.GetModuleFileNameExW(handle, modules[i], buffer, len(buffer)):
                paths.append(buffer.value)
        return paths
    finally:
        kernel32.CloseHandle(handle)


def _is_rocm(name):
    low = name.lower()
    if os.name == "nt":
        return low.startswith(ROCM_DLL_PREFIXES) and low.endswith(".dll")
    return name.startswith(ROCM_LIB_PREFIXES) and ".so" in name


def _same_dir(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def check_loaded_from_archive(server, exe_dir):
    """Every ROCm library inside the running server came from the archive.

    Taken after the requests have run, so libraries that are only loaded on
    first use -- the code-object compiler, the maths libraries -- are included.
    """
    proc = server.proc
    if proc is None or proc.poll() is not None:
        return (report.STATUS_FAIL, "llama-server is not running, so its loaded "
                "libraries cannot be inspected", {})
    if os.name == "nt":
        paths = _windows_loaded_files(proc.pid)
    else:
        paths = _linux_loaded_files(proc.pid)

    rocm = [p for p in paths if _is_rocm(os.path.basename(p))]
    outside = [p for p in rocm if not _same_dir(os.path.dirname(p), exe_dir)]
    hip_runtime = [p for p in rocm if os.path.basename(p).lower().startswith(HIP_RUNTIME_PREFIX)]
    details = {
        "archive_dir": exe_dir,
        "modules_loaded": len(paths),
        "rocm_libraries_loaded": sorted(os.path.basename(p) for p in rocm),
        "loaded_from_outside_archive": outside,
    }
    if not hip_runtime:
        return (report.STATUS_FAIL,
                "the HIP runtime is not loaded in the server at all, so it cannot be "
                "using the GPU", details)
    if outside:
        return (report.STATUS_FAIL,
                f"{len(outside)} ROCm library/libraries were loaded from outside the "
                f"archive, e.g. {outside[0]}. The archive is not standing on its own: "
                "on a machine without that ROCm installed it would fail.", details)
    return (report.STATUS_PASS, None, details)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_gpu_offload(server, base):
    """Confirm the GPU actually did the work, not merely that it was asked to.

    This is the check the tier exists for. When ROCm cannot reach the GPU --
    driver too old or too new, user not allowed to use the device -- llama.cpp
    logs it, finds zero GPUs and keeps serving from the CPU. Every other check
    here passes happily in that state.
    """
    log = server.log_text()

    devices = []
    for match in DEVICE_RE.finditer(log):
        entry = {"device": match.group(1), "name": match.group(2)}
        if entry not in devices:
            devices.append(entry)
    buffers = [(m.group(1), float(m.group(2))) for m in BUFFER_RE.finditer(log)]
    allocated = [(dev, mib) for dev, mib in buffers if mib > 0]
    requested = REQUESTED_OFFLOAD_RE.search(log)

    props_status, props_raw = http_get(f"{base}/props")
    props = {}
    if props_status == 200:
        try:
            props = json.loads(props_raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            props = {}

    details = {
        "devices_selected": devices,
        "model_buffers_mib": buffers,
        # Recorded, never decisive -- see REQUESTED_OFFLOAD_RE above.
        "requested_offload_line": requested.group(0) if requested else None,
        # /props is supplementary: its shape changes between upstream releases,
        # so it is recorded but never gates.
        "props_status": props_status,
        "props_n_gpu_layers": (props.get("default_generation_settings") or {})
        .get("n_gpu_layers"),
    }

    if devices and allocated:
        print("GPU confirmed by:")
        for entry in devices:
            print(f"    using device {entry['device']} ({entry['name']})")
        for dev, mib in allocated:
            print(f"    {dev} model buffer size = {mib:.2f} MiB")
        return (report.STATUS_PASS, None, details)

    hints = [p.pattern for p in CPU_FALLBACK_HINTS if p.search(log)]
    details["cpu_fallback_hints"] = hints
    details["log_tail"] = log[-3000:]

    if hints:
        reason = "ROCm could not use the GPU, so llama.cpp used the CPU"
    elif devices and not allocated:
        reason = ("a ROCm device was selected but no model memory was ever placed "
                  "on it")
    elif requested:
        reason = (f"the only offload line present, '{requested.group(0)}', reports "
                  "what -ngl asked for rather than what happened; no ROCm device "
                  "and no ROCm memory allocation were logged")
    else:
        reason = "no ROCm device and no ROCm memory allocation were logged"

    return (report.STATUS_FAIL,
            f"GPU was NOT used -- {reason}. This is silent at run time: the "
            "server still starts and still answers, just on the CPU.", details)


def _parts(body):
    """Split a reply into (reasoning, answer, stop reason).

    A model that thinks before answering has that thinking separated out by the
    server into its own field, so the two arrive apart rather than as one piece
    of text with markers in it.
    """
    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return ((message.get("reasoning_content") or ""),
            (message.get("content") or ""),
            choice.get("finish_reason"))


def _content(body):
    reasoning, answer, finish = _parts(body)
    return reasoning + answer, finish


def chat(base, prompt, max_tokens=192, seed=0, cache_prompt=True):
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "top_k": 1,
        "seed": seed,
    }
    if not cache_prompt:
        # Stops the second of two identical requests from reusing work cached
        # by the first, so comparing them is like for like.
        payload["cache_prompt"] = False
    return http_post_json(f"{base}/v1/chat/completions", payload)


def check_well_formed(base, state):
    # A model that thinks before answering spends most of its budget on the
    # thinking, so this needs enough room to reach an actual answer.
    body = chat(base, "In one short paragraph, what is the capital of France?",
                max_tokens=512)
    reasoning, answer, finish = _parts(body)
    text = reasoning + answer
    state.update(reasoning=reasoning, answer=answer, finish=finish)

    bad_chars = text.count("\ufffd")
    details = {
        "finish_reason": finish,
        "chars": len(text),
        "reasoning_chars": len(reasoning),
        "answer_chars": len(answer),
        "sample": text[:400],
        "replacement_chars": bad_chars,
    }
    problems = []
    if not text.strip():
        problems.append("response is empty")
    if bad_chars:
        problems.append(f"{bad_chars} U+FFFD replacement character(s) "
                        "-- the decoder produced invalid UTF-8")
    if 0 < len(text.strip()) < 8:
        problems.append(f"response is implausibly short ({len(text.strip())} chars)")
    if finish not in ("stop", "length"):
        problems.append(f"finish_reason is {finish!r}, expected 'stop' or 'length'")

    if problems:
        return (report.STATUS_FAIL, "; ".join(problems), details)
    return (report.STATUS_PASS, None, details)


def check_determinism(base):
    """Ask the same question twice with randomness off and compare the answers.

    Non-gating, and worth being honest about why. On a GPU thousands of threads
    add numbers in whatever order they happen to finish, so results can differ
    in the last decimal place from one run to the next. That only changes the
    reply when two candidate words are almost exactly tied -- then the pick
    flips, and everything after it diverges. So a difference here is a hint
    worth a look, not proof of a defect. Gross corruption is caught by T1.3.
    """
    prompt = "What is 2 + 2?"
    outputs = []
    for _ in range(2):
        body = chat(base, prompt, max_tokens=64, cache_prompt=False)
        outputs.append(_content(body)[0])

    identical = outputs[0] == outputs[1]

    print(f"Determinism check -- prompt: {prompt!r}")
    print(f"  run 1 ({len(outputs[0])} chars): {outputs[0]!r}")
    print(f"  run 2 ({len(outputs[1])} chars): {outputs[1]!r}")
    print(f"  verdict: {'IDENTICAL' if identical else 'DIFFERENT'}")

    details = {"prompt": prompt, "identical": identical,
               "first": outputs[0], "second": outputs[1]}

    if not outputs[0].strip():
        return (report.STATUS_WARN, "the greedy request returned nothing", details)
    if not identical:
        prefix = os.path.commonprefix(outputs)
        details["diverged_at_char"] = len(prefix)
        return (report.STATUS_WARN,
                f"the same question asked twice gave different answers, diverging "
                f"after {len(prefix)} characters. Usually harmless -- two words "
                "were near-tied and rounding decided between them -- but worth "
                "investigating if it happens on every run.", details)
    return (report.STATUS_PASS, None, details)


def check_reasoning_pass(state):
    """Did the model think first, then answer, and stop cleanly?

    Looks at the structure of the reply rather than searching it for the
    markers a thinking model writes around its reasoning. Those markers never
    reach us: the server strips them and returns the reasoning in its own
    field, so a check that searched the text for them could never pass.
    """
    reasoning = state.get("reasoning") or ""
    answer = state.get("answer") or ""
    finish = state.get("finish")
    details = {"reasoning_chars": len(reasoning), "answer_chars": len(answer),
               "finish_reason": finish,
               "reasoning_sample": reasoning[:200],
               "answer_sample": answer[:200]}

    if finish == "length":
        return (report.STATUS_WARN,
                "the reply ran out of tokens before the model finished, so a "
                "complete reasoning pass could not be observed", details)
    if reasoning.strip() and answer.strip():
        return (report.STATUS_PASS, None, details)
    if answer.strip():
        return (report.STATUS_WARN,
                "the model answered without a separate reasoning pass, which is "
                "normal for a model that does not think out loud", details)
    return (report.STATUS_WARN, "no answer was produced", details)


CHECKS_AFTER_BOOT = [
    ("T1.2", "GPU really did the work", True),
    ("T1.3", "response well-formed", True),
    ("T1.4", "greedy determinism", False),
    ("T1.5", "reasoning pass completed", False),
    ("T1.6", "ROCm loaded from the archive", True),
]


def main():
    parser = argparse.ArgumentParser(description="Tier 1 GPU functional inference")
    parser.add_argument("--meta", default=None,
                        help="qualify-meta-*.json written by the build job. Names the "
                             "archive to find under --archive-dir and supplies the "
                             "versions for the record.")
    parser.add_argument("--archive-dir", default=".",
                        help="where to look for the archive named by --meta")
    parser.add_argument("--archive", default=None,
                        help="release archive to test; extracted automatically")
    parser.add_argument("--model", default=None,
                        help="path to the .gguf model; downloaded if absent")
    parser.add_argument("--model-url", default=MODEL_URL)
    parser.add_argument("--gfx-target", default="gfx1151",
                        help="the gfx target of the GPU in this machine")
    parser.add_argument("--release-tag", default=None)
    parser.add_argument("--port", type=int, default=8231)
    parser.add_argument("--logs-dir", default=".")
    parser.add_argument("--ready-timeout", type=int, default=READY_TIMEOUT)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    parser.add_argument("--output", default="tier1-report.json")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    info = {}
    if args.meta:
        with open(args.meta, encoding="utf-8-sig") as handle:
            info = json.load(handle)
        if not args.archive and info.get("artifact"):
            args.archive = os.path.join(args.archive_dir, info["artifact"])
    if not args.archive:
        parser.error("one of --meta or --archive is required")
    if not os.path.exists(args.archive):
        raise SystemExit(f"::error::archive not found: {args.archive}")

    # Placed inside the runner's own temporary area when there is one. An
    # extracted archive plus the test model is a few gigabytes and these
    # machines may be reused between jobs; cleanup() removes it either way.
    workdir = tempfile.mkdtemp(prefix="tier1-", dir=os.environ.get("RUNNER_TEMP") or None)

    # Registered immediately, so the extracted archive and the downloaded model
    # are removed however this run ends, including the early exits below.
    def cleanup():
        if os.path.isdir(workdir):
            print(f"Removing working directory {workdir}")
            shutil.rmtree(workdir, ignore_errors=True)

    atexit.register(cleanup)

    root = os.path.abspath(extract_archive(args.archive, os.path.join(workdir, "x")))
    print(f"Extracted {args.archive} -> {root}")

    model = args.model or os.path.join(workdir, "Qwen3-0.6B-Q4_0.gguf")
    download_model(args.model_url, model)

    exe = os.path.join(root, "llama-server.exe" if os.name == "nt" else "llama-server")
    if not os.path.exists(exe):
        raise SystemExit(f"::error::llama-server not found at {exe}")
    if os.name != "nt":
        # Python's zip extraction does not restore permission bits.
        for entry in os.listdir(root):
            path = os.path.join(root, entry)
            if os.path.isfile(path):
                os.chmod(path, 0o755)

    environment = runner_environment()
    print("Runner environment:")
    print(json.dumps(environment, indent=2))

    meta = report.build_meta(
        artifact=os.path.basename(args.archive),
        os_name="windows" if os.name == "nt" else "ubuntu",
        gfx_family=info.get("gfx_family"),
        gpu_targets=families.split_targets(info.get("gpu_targets") or ""),
        rocm_version=info.get("rocm_version"),
        upstream_sha=info.get("upstream_sha"),
        release_tag=args.release_tag,
        hardware_validated=True,
        hardware_gfx_target=args.gfx_target,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        runner_env=environment,
    )
    tier = report.TierReport("tier1", meta)

    os.makedirs(args.logs_dir, exist_ok=True)
    log_path = os.path.join(args.logs_dir, f"llama-server-{meta['os']}-{args.gfx_target}.log")
    base = f"http://127.0.0.1:{args.port}"
    env = build_env(root, args.gfx_target)
    server = Server(exe, os.path.abspath(model), args.port, log_path, env, root)
    state = {}

    try:
        server.start()
        ready, err = server.wait_ready(args.ready_timeout)
        if ready:
            tier.add("T1.1", "server boot", report.STATUS_PASS,
                     details={"port": args.port, "args": ["-ngl", "99", *SERVER_ARGS]})
            tier.run("T1.2", "GPU really did the work",
                     lambda: check_gpu_offload(server, base))
            tier.run("T1.3", "response well-formed",
                     lambda: check_well_formed(base, state))
            tier.run("T1.4", "greedy determinism",
                     lambda: check_determinism(base), gating=False)
            tier.run("T1.5", "reasoning pass completed",
                     lambda: check_reasoning_pass(state), gating=False)
            # Last, so every library loaded on first use is already in place.
            tier.run("T1.6", "ROCm loaded from the archive",
                     lambda: check_loaded_from_archive(server, root))
        else:
            tier.add("T1.1", "server boot", report.STATUS_FAIL, error=err,
                     details={"log_tail": server.log_text()[-3000:]})
            for test_id, name, gating in CHECKS_AFTER_BOOT:
                tier.add(test_id, name, report.STATUS_SKIP,
                         gating=gating, error="server not ready")
    finally:
        server.stop()

    tier.write(args.output)
    tier.print_summary()
    print(f"\nWrote {args.output}")

    if args.fail_on_error and tier.rollup_status() == report.STATUS_FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
