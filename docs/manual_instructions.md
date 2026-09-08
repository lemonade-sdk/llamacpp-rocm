# Manual Build Instructions

> These steps are a simplified local-build path. For the exact flags, packaging, and GPU matrix used in releases, see the [GitHub Actions workflow](../.github/workflows/build-llamacpp-rocm.yml).
>
> Prefer a [prebuilt release](https://github.com/lemonade-sdk/llamacpp-rocm/releases/latest) unless you need to compile locally.

- [Windows](#windows)
- [Ubuntu](#ubuntu)
- [GPU targets](#gpu-targets)

## Download a ROCm nightly tarball

TheRock publishes nightlies at [rocm.nightlies.amd.com/tarball-multi-arch](https://rocm.nightlies.amd.com/tarball-multi-arch/). Open that index, pick the newest tarball for your OS and GPU family, and download it.

| GPU family | Filename slot |
| --- | --- |
| gfx1151 | `gfx1151` |
| gfx1150 | `gfx1150` |
| gfx120X | `gfx120X-all` |
| gfx110X | `gfx110X-all` |
| gfx103X | `gfx103X-all` |
| gfx90a | `gfx90a` |
| gfx908 | `gfx908` |

Examples (replace the version with the newest file on the index):

```
https://rocm.nightlies.amd.com/tarball-multi-arch/therock-dist-windows-gfx1151-<version>.tar.gz
https://rocm.nightlies.amd.com/tarball-multi-arch/therock-dist-linux-gfx1151-<version>.tar.gz
```

Skip files with `-tests-` in the name.

## Windows

### 1. Install build tools

Chocolatey is optional; you can install the same tools by hand.

```
choco install visualstudio2022buildtools -y --params "--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.VC.ATL --add Microsoft.VisualStudio.Component.Windows11SDK.22621"
choco install cmake ninja python strawberryperl -y
```

### 2. Extract ROCm

Download the Windows tarball for your GPU (see [above](#download-a-rocm-nightly-tarball)), then:

```
mkdir C:\opt\rocm
tar -xzf therock-dist-windows-<family>-<version>.tar.gz -C C:\opt\rocm --strip-components=1
```

Clone llama.cpp:

```
git clone --depth 1 --single-branch --branch master https://github.com/ggerganov/llama.cpp.git
```

### 3. Build

Open **x64 Native Tools Command Prompt** and run:

```
set HIP_PATH=C:\opt\rocm
set HIP_PLATFORM=amd
set PATH=%HIP_PATH%\lib\llvm\bin;%HIP_PATH%\bin;%PATH%

cd llama.cpp
mkdir build
cd build

cmake .. -G Ninja ^
  -DCMAKE_C_COMPILER="C:\opt\rocm\lib\llvm\bin\clang.exe" ^
  -DCMAKE_CXX_COMPILER="C:\opt\rocm\lib\llvm\bin\clang++.exe" ^
  -DCMAKE_CXX_FLAGS="-IC:\opt\rocm\include" ^
  -DCMAKE_CROSSCOMPILING=ON ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DGPU_TARGETS="gfx1151" ^
  -DBUILD_SHARED_LIBS=ON ^
  -DLLAMA_BUILD_TESTS=OFF ^
  -DGGML_HIP=ON ^
  -DGGML_OPENMP=OFF ^
  -DGGML_CUDA_FORCE_CUBLAS=OFF ^
  -DGGML_RPC=ON ^
  -DGGML_HIP_ROCWMMA_FATTN=OFF ^
  -DLLAMA_BUILD_BORINGSSL=ON ^
  -DGGML_NATIVE=OFF ^
  -DGGML_STATIC=OFF ^
  -DCMAKE_SYSTEM_NAME=Windows

cmake --build . -j %NUMBER_OF_PROCESSORS%
```

Adjust `-DGPU_TARGETS` for your GPU (see [GPU targets](#gpu-targets)). Binaries land in `llama.cpp\build\bin`. Keep `C:\opt\rocm\bin` on `PATH` when you run them.

## Ubuntu

### 1. Install build tools

```bash
sudo apt update
sudo apt install -y cmake ninja-build git wget
```

### 2. Extract ROCm

Download the Linux tarball for your GPU (see [above](#download-a-rocm-nightly-tarball)), then:

```bash
sudo mkdir -p /opt/rocm
sudo tar -xzf therock-dist-linux-<family>-<version>.tar.gz -C /opt/rocm --strip-components=1
```

```bash
export HIP_PATH=/opt/rocm
export ROCM_PATH=/opt/rocm
export HIP_PLATFORM=amd
export PATH=/opt/rocm/bin:/opt/rocm/llvm/bin:$PATH
export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/lib64:/opt/rocm/llvm/lib:${LD_LIBRARY_PATH:-}
```

```bash
git clone --depth 1 --single-branch --branch master https://github.com/ggerganov/llama.cpp.git
```

### 3. Build

```bash
cd llama.cpp
mkdir build
cd build

cmake .. -G Ninja \
  -DCMAKE_C_COMPILER=/opt/rocm/llvm/bin/clang \
  -DCMAKE_CXX_COMPILER=/opt/rocm/llvm/bin/clang++ \
  -DCMAKE_CXX_FLAGS="-I/opt/rocm/include" \
  -DCMAKE_CROSSCOMPILING=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DGPU_TARGETS="gfx1151" \
  -DBUILD_SHARED_LIBS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DGGML_HIP=ON \
  -DGGML_OPENMP=OFF \
  -DGGML_CUDA_FORCE_CUBLAS=OFF \
  -DGGML_RPC=ON \
  -DGGML_HIP_ROCWMMA_FATTN=OFF \
  -DLLAMA_BUILD_BORINGSSL=ON \
  -DGGML_NATIVE=OFF \
  -DGGML_STATIC=OFF \
  -DCMAKE_SYSTEM_NAME=Linux

cmake --build . -j $(nproc)
```

Adjust `-DGPU_TARGETS` for your GPU (see [GPU targets](#gpu-targets)). Binaries land in `llama.cpp/build/bin`. Keep the ROCm `LD_LIBRARY_PATH` set when you run them.

To produce a portable folder with ROCm libraries copied next to the binaries (as in releases), copy the corresponding steps from the [workflow](../.github/workflows/build-llamacpp-rocm.yml) rather than duplicating that list here.

## GPU targets

Use the mapped architectures in `-DGPU_TARGETS`, not the tarball family name:

| Family | `-DGPU_TARGETS` | Examples |
| --- | --- | --- |
| gfx120X | `gfx1200;gfx1201` | RX 9070 XT/GRE/9070, RX 9060 XT/9060 |
| gfx110X | `gfx1100;gfx1101;gfx1102;gfx1103` | RX 7900/7800/7700/7600, Radeon 780M/760M/740M |
| gfx103X | `gfx1030;gfx1031;gfx1032;gfx1034` | RX 6800/6700/6600/6500 |
| gfx1150 | `gfx1150` | Strix Point |
| gfx1151 | `gfx1151` | Strix Halo |
| gfx90a | `gfx90a` | Instinct MI210 |
| gfx908 | `gfx908` | Instinct MI100 |
