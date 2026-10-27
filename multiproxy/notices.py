"""The credits and license notices for what Higgs voice and the local decision model download and build on (2026-10-02).

One list feeds the dashboard (Settings, About, "Credits and notices") and is kept word for word in THIRD-PARTY-NOTICES.md; a test checks
the two agree. Each entry: `name`, `text` (what the player reads), `url` (or ""), and `notice` (a license line that must be shown as written).
"""

BUILT_WITH = "Built with Higgs TTS 3 licensed from Boson AI USA, Inc."
BOSON_LICENSE_NOTICE = "Boson Higgs TTS 3 is licensed under the Boson Higgs TTS 3 Research and Non-Commercial License"

NOTICES = [
    {"name": "Higgs TTS 3", "url": "https://huggingface.co/audio-cpp/audio.cpp-gguf",
     "text": BUILT_WITH, "notice": BOSON_LICENSE_NOTICE},
    {"name": "audio.cpp", "url": "https://github.com/0xShug0/audio.cpp",
     "text": "The engine powering the Higgs voice, by ShugoAI LLC (Apache License 2.0). We use the model exactly as the audio.cpp project publishes it on Hugging Face.", "notice": ""},
    {"name": "PandorumLLM", "url": "https://github.com/Pt0l3my/PandorumLLM",
     "text": "PandorumLLM by Pt0l3my inspired several ideas in MultiProxy's Routing tab.", "notice": ""},
    {"name": "SkyrimNet's Routing page", "url": "",
     "text": "SkyrimNet's own Routing page inspired how we pass a job to the next model if one fails, give a failing model a rest, and pick the fastest one. We only borrowed the ideas, not SkyrimNet's code.", "notice": ""},
    {"name": "NVIDIA CUDA runtime", "url": "https://developer.nvidia.com/cuda-toolkit",
     "text": "For NVIDIA graphics cards, the engine download includes NVIDIA's CUDA runtime files (NVIDIA Corporation) from the same release.", "notice": ""},
    {"name": "cleanestpoison and Elbios", "url": "",
     "text": "Thanks to cleanestpoison (higgs3-tts-skyrimnet) and Elbios (higgs3-whisper) for showing Higgs works with SkyrimNet. MultiProxy's Higgs voice uses our own code, not theirs.", "notice": ""},
    {"name": 'llama.cpp decision models', "url": 'https://github.com/ggml-org/llama.cpp',
     "text": "The engine that handles Quick decisions, along with the converted model files, is by ggml-org and the llama.cpp contributors (MIT License; models keep their authors' licenses). The questions and answers follow TypeSafe's System One format.", "notice": ""},
    {"name": 'Kev-4B', "url": 'https://huggingface.co/jaredpalmer/kev-4b',
     "text": 'The recommended decision model, by jaredpalmer on Hugging Face (Apache License 2.0), converted for llama.cpp by ggml-org.', "notice": ""},
    {"name": 'Laya', "url": 'https://huggingface.co/convaiinnovations/laya',
     "text": 'The small decision model, by convaiinnovations on Hugging Face (Apache License 2.0), converted for llama.cpp by ggml-org.', "notice": ""},
    {"name": 'Lev', "url": 'https://huggingface.co/interfaze-ai/lev',
     "text": 'The alternative decision model, by interfaze-ai on Hugging Face (Apache License 2.0), converted for llama.cpp by ggml-org.', "notice": ""},
    {"name": 'Qwen', "url": 'https://huggingface.co/Qwen',
     "text": 'The Qwen3.5 base model that Kev-4B and Lev are built on, by the Qwen team at Alibaba Cloud (Apache License 2.0).', "notice": ""},
    {"name": 'ModernBERT', "url": 'https://huggingface.co/answerdotai/ModernBERT-large',
     "text": 'The base model Laya is built on, by Answer.AI and LightOn (Apache License 2.0).', "notice": ""},
]
