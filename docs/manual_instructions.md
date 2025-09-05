# 🔧 Manual Build Instructions

> **⚠️ Important Notice**
> 
> These manual build instructions are provided for reference purposes only and may not reflect the most current build process. We cannot guarantee that these instructions are up-to-date or will work in all environments. 
> 
> For the most reliable and current build process, please refer to our [automated GitHub Actions workflow](../.github/workflows/build-llamacpp-rocm.yml). The workflow represents our recommended approach for building Llama.cpp with ROCm support.

---

Choose your operating system:
- [🪟 Windows Build Instructions](#windows-build-instructions)
- [🐧 Ubuntu Build Instructions](#ubuntu-build-instructions)

## 🪟 Windows Build Instructions

If you prefer to build locally on Windows, follow these steps:

### Part 1: Download required software

I used chocolatey, but you can also install those manually.
  ```
  choco install visualstudio2022buildtools -y --params "--add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.VC.ATL --add Microsoft.VisualStudio.Component.Windows11SDK.22621"
  choco install cmake --version=3.31.0 -y
  choco install ninja -y
  choco install ccache -y
  choco install python -y
  choco install strawberryperl -y
  ```
> Note: cmake is not strictly needed, as we 

### Part 2: Organizing artifacts
* Step 1: Get the latest run id from main [here](https://github.com/ROCm/TheRock/actions/workflows/release_windows_packages.yml).
  * Example: [TheRock/actions/runs/16218534118/job/45793425858](https://github.com/ROCm/TheRock/actions/runs/16218534118/job/45793425858)
* Step 2: Look at the upload logs for your target GPU (e.g., `gfx1151`), and note the Windows URL:
  ```
  ://therock-nightly-tarball/therock-dist-windows-gfx1151-7.0.0rc20250711.tar.gz
  ```
* Step 4: Download the nightly tarball 
  * Example: `therock-nightly-tarball.s3.amazonaws.com/YOUR_FILE`
* Step 5: Extract the contents of this tar.gz file to `C:\opt\rocm`
* Setp 6: Add `C:\opt\rocm\lib\llvm\bin` to path
* Step 7: clone llamacpp

### Part 3: Updating llama.cpp

Open `C:\<YOUR_LLAMACPP_PATH>\ggml\src\ggml-cuda\vendors\hip.h` and replace `HIP_VERSION >= 70000000` with `HIP_VERSION >= 50600000`

### Part 4: Building Llama.cpp + ROCm

Open `x64 Native Tools Command Prompt` and run the following commands:

```
set HIP_PATH=C:/opt/rocm
set PATH=%HIP_PATH%/bin;%PATH%
set HIP_PLATFORM=amd
cd "C:\<YOUR_LLAMACPP_PATH>\llama.cpp"
mkdir build
cd build
cmake .. -G Ninja -DCMAKE_C_COMPILER="C:\opt\rocm\lib\llvm\bin\clang.exe" -DCMAKE_CXX_COMPILER="C:\opt\rocm\lib\llvm\bin\clang++.exe" -DCMAKE_CROSSCOMPILING=ON -DCMAKE_BUILD_TYPE=Release -DAMDGPU_TARGETS="gfx1151" -DBUILD_SHARED_LIBS=ON -DLLAMA_BUILD_TESTS=OFF -DGGML_HIP=ON -DGGML_OPENMP=OFF -DGGML_CUDA_FORCE_CUBLAS=OFF -DGGML_HIP_ROCWMMA_FATTN=ON -DGGML_HIP_FORCE_ROCWMMA_FATTN_GFX12=OFF -DLLAMA_CURL=OFF -DGGML_NATIVE=OFF -DGGML_STATIC=OFF -DCMAKE_SYSTEM_NAME=Windows
cmake --build . -j 24 2>&1 | findstr /i "error"
```

> **Note**: Adjust the `-DAMDGPU_TARGETS="gfx1151"` parameter for your specific GPU. See the [GPU Target Reference](#gpu-target-reference) section for details.

If you see no errors, that means that llama.cpp has correctly been built and files are available inside your `build\bin` folder. 

---

## 🐧 Ubuntu Build Instructions

If you prefer to build locally on Ubuntu, follow these steps:

### Part 1: Install required software

Update your package manager and install the build dependencies:
```bash
sudo apt update
sudo apt install -y cmake ninja-build git wget
```

### Part 2: Organizing artifacts

> **Note**: The process for finding and downloading the ROCm nightly tarball is similar to the [Windows Part 2](#part-2-organizing-artifacts) above, but with Linux-specific URLs.

* Step 1: Get the latest run id from main [here](https://github.com/ROCm/TheRock/actions/workflows/release_windows_packages.yml) (same as Windows).
* Step 2: Look at the upload logs for your target GPU (e.g., `gfx1151`), but note the Linux URLs:
  ```
  ://therock-nightly-tarball/therock-dist-linux-gfx1151-7.0.0rc20250711.tar.gz
  ```
* Step 3: Download the nightly tarball for Linux
  * Example: [therock-nightly-tarball.s3.amazonaws.com/YOUR_LINUX_FILE](https://therock-nightly-tarball.s3.amazonaws.com/therock-dist-linux-gfx1151-7.0.0rc20250711.tar.gz)
* Step 4: Extract the contents of this tar.gz file to `/opt/rocm`:
  ```bash
  sudo mkdir -p /opt/rocm
  sudo tar -xzf therock-dist-linux-gfx1151-7.0.0rc20250711.tar.gz -C /opt/rocm --strip-components=1
  ```
* Step 5: Set up ROCm environment variables:
  ```bash
  export HIP_PATH=/opt/rocm
  export ROCM_PATH=/opt/rocm
  export HIP_PLATFORM=amd
  export HIP_CLANG_PATH=/opt/rocm/llvm/bin
  export HIP_INCLUDE_PATH=/opt/rocm/include
  export HIP_LIB_PATH=/opt/rocm/lib
  export HIP_DEVICE_LIB_PATH=/opt/rocm/lib/llvm/amdgcn/bitcode
  export PATH=/opt/rocm/bin:/opt/rocm/llvm/bin:$PATH
  export LD_LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/lib64:/opt/rocm/llvm/lib:${LD_LIBRARY_PATH:-}
  export LIBRARY_PATH=/opt/rocm/lib:/opt/rocm/lib64:${LIBRARY_PATH:-}
  export CPATH=/opt/rocm/include:${CPATH:-}
  export PKG_CONFIG_PATH=/opt/rocm/lib/pkgconfig:${PKG_CONFIG_PATH:-}
  ```
* Step 6: Clone llama.cpp:
  ```bash
  git clone https://github.com/ggerganov/llama.cpp.git
  ```

### Part 3: Updating llama.cpp

> **Note**: This step is identical to the [Windows Part 3](#part-3-updating-llamacpp) above, with the same file modification.

Navigate to your llama.cpp directory and update the HIP version check:
```bash
cd llama.cpp
sed -i 's/HIP_VERSION >= 70000000/HIP_VERSION >= 50600000/g' ggml/src/ggml-cuda/vendors/hip.h
```

### Part 4: Building Llama.cpp + ROCm

Run the following commands to build llama.cpp with ROCm support:

```bash
# Navigate to llama.cpp directory
cd llama.cpp

# Create build directory
mkdir build
cd build

# Configure the project
cmake .. -G Ninja \
  -DCMAKE_C_COMPILER=/opt/rocm/llvm/bin/clang \
  -DCMAKE_CXX_COMPILER=/opt/rocm/llvm/bin/clang++ \
  -DCMAKE_CROSSCOMPILING=ON \
  -DCMAKE_BUILD_TYPE=Release \
  -DAMDGPU_TARGETS="gfx1151" \
  -DBUILD_SHARED_LIBS=ON \
  -DLLAMA_BUILD_TESTS=OFF \
  -DGGML_HIP=ON \
  -DGGML_OPENMP=OFF \
  -DGGML_CUDA_FORCE_CUBLAS=OFF \
  -DGGML_HIP_ROCWMMA_FATTN=ON \
  -DGGML_HIP_FORCE_ROCWMMA_FATTN_GFX12=OFF \
  -DLLAMA_CURL=OFF \
  -DGGML_NATIVE=OFF \
  -DGGML_STATIC=OFF \
  -DCMAKE_SYSTEM_NAME=Linux

# Build the project (adjust -j value based on your CPU cores)
cmake --build . -j $(nproc)
```

> **Note**: Adjust the `-DAMDGPU_TARGETS="gfx1151"` parameter for your specific GPU. See the [GPU Target Reference](#gpu-target-reference) section for details.

### Part 5: Copy required ROCm libraries

After successful compilation, copy the required ROCm libraries to the build directory:

```bash
# Navigate to the build/bin directory
cd bin

# Copy all required ROCm libraries
echo "Copying ROCm shared libraries..."

# Copy all shared libraries from main ROCm lib directories
cp -v /opt/rocm/lib/*.so* .
cp -v /opt/rocm/lib64/*.so* .
cp -v /opt/rocm/lib/llvm/lib/*.so* .
cp -v /opt/rocm/lib/rocm_sysdeps/lib/*.so* .

# Copy the rocblas library folder
mkdir -p rocblas
cp -r /opt/rocm/lib/rocblas/library rocblas/
```

If you see no errors during the build process, llama.cpp has been successfully compiled and all files are available in your `build/bin` folder.

---

## 🎯 GPU Target Reference

When building llama.cpp with ROCm, the `-DAMDGPU_TARGETS` parameter must be set based on your specific GPU architecture. Our automated workflow uses generic targets that get mapped to specific architectures:

- **`gfx120X`** maps to `gfx1200,gfx1201` (RDNA 3 series like RX 7900 XT/XTX)
- **`gfx110X`** maps to `gfx1100` (RDNA 2 series like RX 6000 series)  
- **`gfx1151`** remains as `gfx1151` (specific for RX 7600/7700 XT)

For a complete list of GPU targets and their mappings, see the [automated workflow](../.github/workflows/build-llamacpp-rocm.yml).

### How to Use

Replace the `-DAMDGPU_TARGETS="gfx1151"` parameter in your cmake command with the appropriate target for your GPU:

```bash
# For RDNA 3 series (RX 7900 XT/XTX)
-DAMDGPU_TARGETS="gfx1200,gfx1201"

# For RDNA 2 series (RX 6000 series) 
-DAMDGPU_TARGETS="gfx1100"

# For RX 7600/7700 XT
-DAMDGPU_TARGETS="gfx1151"
```
