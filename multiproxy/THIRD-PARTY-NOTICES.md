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
