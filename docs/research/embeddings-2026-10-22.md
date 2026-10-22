# Embeddings, rerankers and the linked models: what EchoSpeak should use, 2026-10-22

**What this covers:** research on the models the owner linked, newer alternatives, and whether and how EchoSpeak should adopt them. Nothing here is implemented yet.

**Visual report:** [artifacts/research/embeddings-2026-10-22.html](../../artifacts/research/embeddings-2026-10-22.html).

## 1. What EchoSpeak uses today

All of this is verified in the repository code.

| Job | Code | Model now | Notes |
|---|---|---|---|
| Memory search by meaning | `agent/memory.py` via `agent/embeddings.py` | Settings choice: OpenAI, LM Studio's `/v1/embeddings`, or the private local model **all-MiniLM-L6-v2** (ONNX, 384-d, about 90 MB, installed from Settings) | Falls back to keyword recall when none is available. `records.json` is the source of truth, and the index **rebuilds itself** when the vector size changes |
| Document search | `agent/document_store.py` | Same embedder, plus BM25 keyword search | Detects a size mismatch and can `rebuild_index()` from the stored text |
| Document reranking | `document_store._get_reranker` | `cross-encoder/ms-marco-MiniLM-L-6-v2` through **sentence-transformers (PyTorch)** | Off by default (`DOC_RERANK_ENABLED`). The installed app doesn't ship PyTorch, so it **can't actually turn on** there |
| Tool and setup-guide search by meaning | `agent/semantic.py` (`find_tools`, `find_integrations`) | The local ONNX MiniLM, cached | Keyword matching when the model isn't installed |

**Limits of MiniLM-L6-v2:**
- **It's from 2021 and English only.**
- **It truncates input at 256 word pieces.** It was trained at 128 tokens, so the ends of longer chunks (documents are split at about 950 characters) are cut off.
- **It's weak on code.**

[Model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).

## 2. The linked models

| Model | What it is | Size | Licence | Fit for EchoSpeak |
|---|---|---|---|---|
| [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) | Sentence embedder, 384-d, English | 22.7M parameters, about 90 MB in ONNX | Apache-2.0 | **Already the local default.** Keep it as the fast option |
| [ms-marco-MiniLM-L6-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) | Cross-encoder reranker, English | 22.7M parameters. ONNX 91 MB, **int8 23 MB**. NDCG@10 74.30 on TREC DL 2019 | Apache-2.0 | **Already configured, but needs PyTorch.** Run its ONNX int8 file on the onnxruntime EchoSpeak already ships, and reranking works in the installed app |
| [EmbeddingGemma 2](https://huggingface.co/google/embeddinggemma-2) | Multimodal embedder (text, image, audio), 768-d with Matryoshka 512/256/128, 8K context, 100+ languages | 740M in total (270M text-only). BF16; float16 can give NaN | Apache-2.0 per the page | Strong, but multimodal weight and no first-party ONNX on the page. **Watch it, don't adopt yet.** For text, EmbeddingGemma-300M is the practical sibling |
| [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B) | **A chat LLM, not an embedder.** 32K context, thinking on by default | 0.6B | Apache-2.0 | Not for search. Its siblings **Qwen3-Embedding-0.6B** and **Qwen3-Reranker-0.6B** are the search models (below). It could serve as a tiny side model through LM Studio. That's optional and unmeasured |
| [Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) | **Image generation and editing**, not embeddings. Text-to-image, edits with up to 10 references, transparent images | 7B DiT, BF16 | **Qwen Research License** (check before any commercial use) | Belongs with `create_media`'s local (ComfyUI) path, not search. Needs PyTorch and Diffusers from GitHub, and a large GPU. Not necessary now |

## 3. Newer and better options, by job

### Embedders (search by meaning)

| Model | Quality (MTEB v2 mean) | Size on disk | Context | Languages | How EchoSpeak would run it |
|---|---|---|---|---|---|
| all-MiniLM-L6-v2 (now) | not reported on the v2 tables cited here; well below the models below | about 90 MB | 256 | English | ONNX (now) |
| **[EmbeddingGemma-300M](https://huggingface.co/google/embeddinggemma-300m)** | English 69.67, multilingual 61.15, code 68.76. QAT Q4: 60.62 multilingual | **q4 ONNX about 197 MB**, int8 about 309 MB ([onnx-community](https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX/tree/main/onnx)) | 2,048 | 100+ | ONNX on the existing runtime. It outputs `sentence_embedding` directly and uses task prefixes |
| **[Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)** | English 70.70, multilingual 64.33 | GGUF Q8 639 MB ([Qwen GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF)); ONNX int8 614 MB | 32K | 100+ | **Best through LM Studio**, which EchoSpeak already supports as an embedding provider. Last-token pooling and query instructions |
| Nomic Embed Text v2 (475M), Stella-400M-v5 | Reported strong in sub-500M rankings (secondary sources) | — | — | — | Possible, but not better enough to justify a third path |

Google says EmbeddingGemma runs in under 200 MB of RAM when quantized, takes under 15 ms for 256 tokens on an EdgeTPU, and works with ONNX, LM Studio, Ollama and llama.cpp ([Google blog](https://developers.googleblog.com/en/introducing-embeddinggemma/), Sept 2025).

Licences:
- **EmbeddingGemma-300M:** Gemma terms. Google's repo asks you to accept them; the onnx-community copy showed no gate on its page.
- **Qwen3-Embedding:** Apache-2.0.

### Rerankers (re-order the top document hits)

| Model | Notes |
|---|---|
| **ms-marco-MiniLM-L6-v2 int8 ONNX** | 23 MB, English, fast on CPU. The cheapest way to make reranking real |
| [mxbai-rerank-xsmall-v1](https://huggingface.co/mixedbread-ai/mxbai-rerank-xsmall-v1) | 70.8M, English, ONNX. BEIR NDCG@10 43.9 vs bge-reranker-base 41.6 |
| bge-reranker-v2-m3 | The usual multilingual default (secondary sources). 568M, so heavier on CPU |
| [Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | Top open quality class (MTEB-R 65.80). An LLM scoring yes/no logits, so it isn't a simple cross-encoder. Too heavy for CPU reranking per search |

A 2026 study found a fine-tuned 109M cross-encoder beat a 4B Qwen3-Reranker on its task, and served on commodity CPUs ([arXiv 2608.09650](https://arxiv.org/pdf/2608.09650)). For a personal app, a small cross-encoder is the right size.

## 4. Is it necessary?

**Not urgent. Worth doing, and cheap.**
- **For English-only owners** with short memories, MiniLM is adequate.
- **Where it falls short:**
  - non-English text;
  - longer document chunks, cut off at 256 word pieces;
  - code;
  - document reranking, which today cannot run in the installed app.
- **Search quality matters more than it looks.** Memory recall, document answers and `find_tools` (which picks among connected-app tools) all depend on it, and a 4B chat model benefits most from being handed the right context.

## 5. Recommendation

1. **Make reranking real, without PyTorch.**
   - Run `ms-marco-MiniLM-L6-v2`'s int8 ONNX (23 MB) on onnxruntime.
   - Keep sentence-transformers only as a fallback.
   - Small change, immediate effect on document answers.
2. **Add one "Better, 100+ languages" local embedder: EmbeddingGemma-300M q4 ONNX (about 200 MB).**
   - It's the best quality per megabyte that runs on the existing runtime with no new dependencies.
   - Pin the revision like MiniLM.
   - Use its query and document prefixes.
   - If the Gemma terms gate the download, fall back to offering it through LM Studio.
3. **Recommend Qwen3-Embedding-0.6B through LM Studio** for owners who already run LM Studio and want the best quality. No new code path: EchoSpeak's LM Studio embedding provider already exists. The new part is detecting a loaded embedding model and suggesting it.
4. **Don't adopt now:**
   - EmbeddingGemma 2: multimodal weight with no text-only ONNX yet. Revisit for screenshot and image memory.
   - Qwen3-0.6B: a chat model.
   - Qwen-Image-2.1: image generation; a ComfyUI workflow candidate later.
   - Qwen3-Reranker: too heavy per search on CPU.

## 6. Making it easy for the owner

Replace today's "Memory search" select, model text field and separate installer with one card in **Settings › Memory & documents**: **Search by meaning**.

| Choice | Shown as | Size | What happens |
|---|---|---|---|
| Off | "Keywords only" | — | Today's fallback |
| Fast | "Fast · English" (all-MiniLM-L6-v2) | about 90 MB | Today's local model |
| **Better** (recommended) | "Better · 100+ languages, longer text" (EmbeddingGemma-300M) | about 200 MB | One download, then automatic re-indexing |
| Use LM Studio | "Use the embedding model loaded in LM Studio" | Your choice | Lists LM Studio's loaded embedding models. Suggests Qwen3-Embedding-0.6B if none is loaded |
| Cloud | "OpenAI" | — | Hidden in Private and Offline modes |

**Also on the card:**
- **A switch:** "Sharper document results (+23 MB)", for the reranker.
- **One button:** **Download and switch**, with a progress bar. Afterwards a status line reads "Re-indexing 1,240 memories and 18 documents… done". Both index rebuilds already exist (memory rebuilds itself on a size change; documents have `rebuild_index()`).
- **Plain status:**
  - "Search by meaning: Better (EmbeddingGemma), ready".
  - "Not installed: using keywords".
  - "LM Studio has no embedding model loaded".
- **Privacy:** downloads already go through `privacy.require("downloads")`, so Offline mode blocks them and says so. Nothing leaves the PC with the local options.

## 7. Engineering plan (about a day; not started)

1. **`agent/embeddings.py`:**
   - Replace the single `LOCAL_MODEL_*` constants with a small catalog. Each entry has repo, pinned revision, files, dimension, pooling (mean, or the model's own `sentence_embedding` output) and query/document prefixes.
   - Parameterise `OnnxEmbeddings` by catalog entry.
   - `embed_query` and `embed_documents` already exist separately, so prefixes slot in.
2. **`OnnxCrossEncoder` in the same module.** `document_store._get_reranker` tries it first, then sentence-transformers.
3. **Settings API:**
   - One status endpoint (catalog, installed, active, LM Studio's embedding models).
   - A download-and-switch action that reports progress.
   - Re-index memory (automatic) and documents (`rebuild_index`) after a switch.
4. **`semantic.py`:** key its cache by model, so a switch doesn't mix vectors.
5. **Tests:**
   - catalog pooling and prefixes against tiny fixtures;
   - the size-change rebuild;
   - reranker ordering;
   - Offline mode blocking downloads.
6. **Manual check:**
   - Switch to Better; confirm memories and documents still answer.
   - Ask in another language.
   - Turn on sharper results and compare a document answer.

**Not done:** no model was downloaded or benchmarked here. Quality numbers are the model cards' own MTEB and BEIR figures, plus secondary sources where marked.
