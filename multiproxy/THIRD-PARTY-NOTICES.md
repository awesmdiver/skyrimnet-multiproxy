# Third-party notices

What MultiProxy's Higgs voice and local decision model download and build on. Nothing here is bundled with or re-hosted by MultiProxy: the engines
and the models are downloaded by your own PC from their publishers (Higgs after you agree to its model's license). The same list is shown
in the dashboard (Settings, About, Credits and notices); `notices.py` is its source and a test keeps the two in step.

## Higgs TTS 3

Built with Higgs TTS 3 licensed from Boson AI USA, Inc.

Boson Higgs TTS 3 is licensed under the Boson Higgs TTS 3 Research and Non-Commercial License

Source: https://huggingface.co/audio-cpp/audio.cpp-gguf

## audio.cpp

The engine powering the Higgs voice, by ShugoAI LLC (Apache License 2.0). We use the model exactly as the audio.cpp project publishes it on Hugging Face.

Source: https://github.com/0xShug0/audio.cpp

## SKSE (Skyrim Script Extender)

The Skyrim mod file (`SkyrimNetMultiProxy.zip`, one file for everyone) is part of MultiProxy and MIT licensed like the rest of it. It is written against SKSE's public plugin interface: the mod is loaded by SKSE and talks to it through the interface SKSE documents. The few interface structures it uses are written out again in the mod's own source, following the comments in SKSE64's public `PluginAPI.h`; no SKSE code is linked into the mod, and it needs no Address Library.

SKSE is by Ian Patterson, Stephen Abel, Paul Connelly, Adam Rippon and Brendan Borthwick.

Source: https://skse.silverlock.org/ and https://github.com/ianpatt/skse64

## PandorumLLM

PandorumLLM by Pt0l3my inspired several ideas in MultiProxy's Routing: the drag-and-drop picture of jobs and models, the breakdown of where the time goes, settings per job, the priority order and the live view. These are ideas only: no code of PandorumLLM is used.

Source: https://github.com/Pt0l3my/PandorumLLM

## gputaskman

gputaskman by Scott Hanselman (MIT) inspired two ideas in the Benchmarks tab: showing which programs are using the graphics card next to a test, and showing the model, cache and working-space memory the engine itself reports beside our own estimate.

**Code taken from it:** the Windows graphics-counter reading in `gpu_counters.py` is ported from `src/gputaskman/pdh.py`: the structures and `_load_pdh` (loading the counter library), the open / add (by English name) / collect / read-an-array sequence (`GpuCounters.__init__`, `_read`, `sample`), the process, engine and adapter instance-name patterns, `luid_key`, the engine-type grouping (`_norm_engine`, ours is `engine_kind`), the order of the counters to try (`Local Usage` and `Non Local Usage` first, the older names as the fallback) and the "busiest engine wins" rule. The rest of that file is our own. The licence notice is kept at the top of `gpu_counters.py`.

The list of graphics cards in `gpu_adapters.py` is ported from `src/gputaskman/dxgi.py`: the `_LUID`, `_DESC1` and `_GUID` structures, the `IDXGIFactory1` interface id, `_vcall` (calling a COM method by its table position) and the adapter loop of `enumerate_adapters` (create the factory, `EnumAdapters1`, `GetDesc1`, `Release`). The vendor words, the software-adapter rule, the Linux reader and the cache are our own. The licence notice is kept at the top of `gpu_adapters.py`.

The NVIDIA card reading in `gpu_nvml.py` follows `src/gputaskman/nvml.py`. That file uses the `pynvml` package; ours calls the driver library directly, so no code line is copied, but these parts are taken from it: the table of clock-throttle reason bits (`THROTTLE`), the error codes that mean the library or card is gone (`RESET_ERRORS`), the rule that a fault inside the library ends all reading of it and the library is never shut down afterwards, and the choice of which device figures to read (name, memory, utilisation, temperature, power, clocks, fan, throttle reasons). The licence notice is kept at the top of `gpu_nvml.py`.

The AMD card readings in `gpu_amd.py` are ported from `src/gputaskman/adl.py`: the `_AdapterInfo`, `_Sensor` and `_Metrics` structures, the memory callback through the C runtime's `malloc`, the call sequence (`ADL2_Main_Control_Create`, `ADL2_Adapter_NumberOfAdapters_Get`, `ADL2_Adapter_AdapterInfo_Get`, `ADL2_New_QueryPMLogData_Get`), the matching of an adapter by PCI bus, device and function, and the sensor numbers (core clock, memory clock, temperature, utilisation, chip power, board power). The PCI address read in `gpu_adapters.py` (`_pci_address`) is ported from `adapter_pci_address` in `dxgi.py`. The licence notice is kept at the top of `gpu_amd.py`. These AMD readings are new and could not be tested on a real AMD card.

The model-file reader in `gguf_tensors.py` follows `src/gputaskman/gguf.py` (`_read_cached` and `GgufInfo.gpu_weight_bytes`): a tensor's size is the gap to the next tensor's offset in the file's tensor table (so no table of quantisation types is needed), the tensors are grouped by block, output and input embeddings, the input embeddings stay in system memory, and a model that ties its output to the input embeddings puts a copy on the card. The reading code is our own, and the rule for which layers go up is corrected against the engine's own memory lines (the output layer counts as one of the layers). The licence notice is kept at the top of `gguf_tensors.py`.

Source: https://github.com/shanselman/gputaskman

```
MIT License

Copyright (c) 2026 Scott Hanselman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## SkyrimNet's Routing page

SkyrimNet's own Routing page inspired how we pass a job to the next model if one fails, give a failing model a rest, and pick the fastest one. We only borrowed the ideas, not SkyrimNet's code.

## NVIDIA CUDA runtime

For NVIDIA graphics cards, the engine download includes NVIDIA's CUDA runtime files (NVIDIA Corporation) from the same release.

Source: https://developer.nvidia.com/cuda-toolkit

## cleanestpoison and Elbios

Thanks to cleanestpoison (higgs3-tts-skyrimnet) and Elbios (higgs3-whisper) for showing Higgs works with SkyrimNet. MultiProxy's Higgs voice uses our own code, not theirs.

## llama.cpp decision models

The engine that handles Quick decisions, along with the converted model files, is by ggml-org and the llama.cpp contributors (MIT License; models keep their authors' licenses). The questions and answers follow TypeSafe's System One format.

Source: https://github.com/ggml-org/llama.cpp

## Gemma 4

The chat models MultiProxy installs and suggests (Gemma 4 E2B, E4B, 12B, 26B A4B and 31B) are by Google (Apache License 2.0, per Google's Gemma 4 license page, read 2026-10-26), downloaded as GGUF conversions from lmstudio-community on Hugging Face. MultiProxy does not change the models.

Source: https://ai.google.dev/gemma

## Kev-4B

The recommended decision model, by jaredpalmer on Hugging Face (Apache License 2.0), converted for llama.cpp by ggml-org.

Source: https://huggingface.co/jaredpalmer/kev-4b

## Laya

The small decision model, by convaiinnovations on Hugging Face (Apache License 2.0), converted for llama.cpp by ggml-org.

Source: https://huggingface.co/convaiinnovations/laya

## Lev

The alternative decision model, by interfaze-ai on Hugging Face (Apache License 2.0), converted for llama.cpp by ggml-org.

Source: https://huggingface.co/interfaze-ai/lev

## Qwen

The Qwen3.5 base model that Kev-4B and Lev are built on, by the Qwen team at Alibaba Cloud (Apache License 2.0).

Source: https://huggingface.co/Qwen

## ModernBERT

The base model Laya is built on, by Answer.AI and LightOn (Apache License 2.0).

Source: https://huggingface.co/answerdotai/ModernBERT-large
