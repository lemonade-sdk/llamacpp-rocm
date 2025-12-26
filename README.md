# llamacpp-rocm

<a href="https://github.com/aigdat/llamacpp-rocm/releases/latest" title="Download the latest release">
  <img src="https://img.shields.io/github/v/release/aigdat/llamacpp-rocm?logo=github&logoColor=white" alt="GitHub release (latest by date)" />
</a>
<a href="https://github.com/aigdat/llamacpp-rocm/releases/latest" title="View latest release date">
  <img src="https://img.shields.io/github/release-date/aigdat/llamacpp-rocm?logo=github&logoColor=white" alt="Latest release date" />
</a>
<a href="LICENSE" title="View license">
  <img src="https://img.shields.io/github/license/aigdat/llamacpp-rocm?logo=opensourceinitiative&logoColor=white&cacheBust=1)" alt="License" />
</a>
<a href="https://github.com/ROCm/ROCm" title="Powered by ROCm 7.0">
  <img src="https://img.shields.io/badge/ROCm-7.0-blue?logo=amd&logoColor=white" alt="ROCm 7.0" />
</a>
<a href="https://github.com/ggerganov/llama.cpp" title="Powered by llama.cpp">
  <img src="https://img.shields.io/badge/🦙Powered%20by-llama.cpp-blue?logo=llama&logoColor=white" alt="Powered by llama.cpp" />
</a>
<a href="#-supported-devices" title="Platform support">
  <img src="https://img.shields.io/badge/OS-Windows%20%7C%20Ubuntu-0078D6?logo=windows&logoColor=white" alt="Platform: Windows | Ubuntu" />
</a>
<a href="#-supported-devices" title="GPU targets">
  <img src="https://img.shields.io/badge/GPU-gfx110X%20%7Cgfx1150%20%7C%20gfx1151%20%7C%20gfx120X-00B04F?logo=amd&logoColor=white" alt="GPU Targets" />
</a>


We provide nightly builds of **llama.cpp** with **AMD ROCm™ 7** acceleration based on TheRock - delivering the freshest, cutting-edge builds available. Our automated pipeline specifically targets seamless integration with [**🍋 Lemonade**](https://github.com/lemonade-sdk/lemonade) and similar AI applications requiring high-performance GPU inference.

> [!IMPORTANT]  
> **Contribution & Support Notice**: While this project currently focuses on integrating llama.cpp+ROCm in a specific production context, our broader goal is to contribute meaningfully to the llama.cpp+ROCm ecosystem. We're not set up to provide comprehensive technical support, but we welcome collaborations, idea exchanges, or contributions that help advance this space.

## 🎯 Supported Devices

This build specifically targets the following GPU architectures:
- **gfx1151** (STX Halo APU) - Ryzen AI MAX+ Pro 395
- **gfx1150** (STX Point APU) - Ryzen AI 300
- **gfx120X** (RDNA4 GPUs) - includes AMD Radeon RX 9070 XT/GRE/9070, RX 9060 XT/9060
- **gfx110X** (RDNA3 GPUs) - includes AMD Radeon PRO W7900/W7800/W7700/W7600, RX 7900 XTX/XT/GRE, RX 7800 XT, RX 7700 XT/7700, RX 7600 XT/7600

**All builds include ROCm™ 7 built-in** - no separate ROCm™ installation required!

## 🚀 Automated Builds

Our automated GitHub Actions workflow creates nightly builds for:
- **Windows** and **Ubuntu** operating systems
- **Multiple GPU targets**: `gfx1151`, `gfx1150`, `gfx110X`, `gfx120X`
- **ROCm™ 7 built-in** - complete runtime libraries included


| GPU Target | Ubuntu | Windows |
|-------------|--------|---------|
| **gfx110X** | [![Download Ubuntu gfx110X](https://img.shields.io/badge/Download-Ubuntu%20gfx110X-blue)](https://github.com/aigdat/llamacpp-rocm/releases/latest) | [![Download Windows gfx110X](https://img.shields.io/badge/Download-Windows%20gfx110X-green)](https://github.com/aigdat/llamacpp-rocm/releases/latest) |
| **gfx1150** | [![Download Ubuntu gfx1150](https://img.shields.io/badge/Download-Ubuntu%20gfx1150-blue)](https://github.com/aigdat/llamacpp-rocm/releases/latest) | [![Download Windows gfx1150](https://img.shields.io/badge/Download-Windows%20gfx1150-green)](https://github.com/aigdat/llamacpp-rocm/releases/latest) |
| **gfx1151** | [![Download Ubuntu gfx1151](https://img.shields.io/badge/Download-Ubuntu%20gfx1151-blue)](https://github.com/aigdat/llamacpp-rocm/releases/latest) | [![Download Windows gfx1151](https://img.shields.io/badge/Download-Windows%20gfx1151-green)](https://github.com/aigdat/llamacpp-rocm/releases/latest) |
| **gfx120X** | [![Download Ubuntu gfx120X](https://img.shields.io/badge/Download-Ubuntu%20gfx120X-blue)](https://github.com/aigdat/llamacpp-rocm/releases/latest) | [![Download Windows gfx120X](https://img.shields.io/badge/Download-Windows%20gfx120X-green)](https://github.com/aigdat/llamacpp-rocm/releases/latest) |

> **⚡ Ready to Run**: All releases include complete ROCm™ 7 runtime libraries - just download and go!

---

## 🧪 Quick Smoketest

To verify your download is working correctly:

1. **Download** the appropriate build for your GPU target from our [latest releases](https://github.com/aigdat/llamacpp-rocm/releases/latest)
2. **Extract** the archive to your preferred directory
3. **Test** with any GGUF model from Hugging Face:

```bash
llama-server -m YOUR_GGUF_MODEL_PATH -ngl 99
```

> **💡 Tip**: Use `-ngl 99` to offload all layers to GPU for maximum acceleration. The exact number of layers may vary by model, but 99 ensures all available layers are offloaded.

> **🍋 Lemonade Integration**: You can also test these builds directly with [**Lemonade**](https://github.com/lemonade-sdk/lemonade) for a seamless AI application experience *(coming soon!)*

---

## 📦 Dependencies

This project relies on the following external software and tools:

### Core Dependencies
- **[Llama.cpp](https://github.com/ggerganov/llama.cpp)** - Efficient, cross-platform inference engine for running GGUF models locally.
- **[ROCm SDK (TheRock)](https://github.com/ROCm/TheRock)** - AMD’s open-source platform for GPU-accelerated computing.
- **[HIP](https://github.com/ROCm/HIP)** - C++ API for writing portable GPU code within the ROCm ecosystem.

### Build Tools & Compilers
- **[Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022)** - Microsoft C++ build tools
- **[CMake](https://cmake.org/)** - Cross-platform build system (version 3.31.0)
- **[Ninja](https://ninja-build.org/)** - Small build system with focus on speed
- **[Clang/Clang++](https://clang.llvm.org/)** - C/C++ compiler (bundled with ROCm)

---

## 🏗️ Code and Artifact Structure

> [!NOTE]  
> **Active Development**: This project is under active development. Code and artifact structure are subject to change as we continue to improve and expand functionality.

### Key Components

- **`docs/`** - Contains build documentation and setup guides
- **`utils/`** - Houses utility scripts for build automation and dependency management
- **GitHub Actions Workflows** - Located in `.github/workflows/` (automated build pipeline)
- **Build Artifacts** - Generated during CI/CD and published as releases

The build process is primarily handled through GitHub Actions, with the repository serving as the source for automated compilation and packaging of llama.cpp with ROCm™ 7 support.

---

## 📋 Manual Build Instructions

For detailed manual build instructions, please see: **[docs/manual_instructions.md](docs/manual_instructions.md)**

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
