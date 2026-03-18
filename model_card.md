# DocuBot Model Card

This model card is a short reflection on your DocuBot system. Fill it out after you have implemented retrieval and experimented with all three modes:

1. Naive LLM over full docs
2. Retrieval only
3. RAG (retrieval plus LLM)

Use clear, honest descriptions. It is fine if your system is imperfect.

---

## 1. System Overview

**What is DocuBot trying to do?**
DocuBot answers developer questions about a codebase by searching project documentation files. It supports three modes: generating answers from a raw LLM with no grounding, searching docs directly without an LLM, and combining retrieval with LLM generation (RAG) to produce grounded answers.

**What inputs does DocuBot take?**
A natural language question from a developer. It also reads all `.md` and `.txt` files from a `docs/` folder at startup, and optionally a `GEMINI_API_KEY` environment variable to enable LLM features.

**What outputs does DocuBot produce?**
One of: a generated text answer (Modes 1 and 3), raw document snippets with filenames (Mode 2), or a refusal message ("I do not know based on these docs.") when evidence is insufficient.

---

## 2. Retrieval Design

**How does your retrieval system work?**

- **Indexing**: At startup, each document is split into tokens (whitespace split, punctuation stripped, stop words excluded). An inverted index maps each token to the list of filenames where it appears.
- **Scoring**: For a given query, content words (stop words removed) are matched against each paragraph using whole-word comparison. Each matching term contributes `num_docs / document_frequency` to the score — a simple IDF weight that gives rare terms more influence than common ones.
- **Retrieval**: Candidate documents are found via index lookup. Each document is split into paragraph-level chunks (split on `\n\n`; section headings merged with the following paragraph to keep endpoint names with their descriptions). Paragraphs are scored individually. The top-k paragraphs are returned.
- **Guardrail**: If the highest-scoring paragraph scores below `MIN_SCORE_THRESHOLD = 1.5`, the retrieval returns an empty list, which causes all answer modes to refuse to respond.

**What tradeoffs did you make?**

- **Simplicity over accuracy**: No stemming, no embeddings, no TF-IDF library. Vocabulary must match exactly (minus stop words).
- **Paragraph granularity**: Smaller than whole-doc (reduces noise) but still coarser than sentence-level (avoids splitting related sentences).
- **IDF over TF**: Total term frequency caused high-frequency table names (e.g., "users") to dominate. IDF weighting made term rarity matter more.
- **Stop word filtering**: Without it, common words ("is", "of", "the") gave every paragraph a non-zero score, breaking the guardrail entirely.

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**

- **Naive LLM mode**: Always calls the LLM. Passes only the raw question — `all_text` is accepted as a parameter but ignored. The model answers from its training data with no access to actual project docs.
- **Retrieval only mode**: Never calls the LLM. Returns raw paragraph text from matching documents. Accurate when vocabulary matches, but unformatted and sometimes hard to read.
- **RAG mode**: Calls the LLM only after retrieval succeeds. If retrieval returns empty (below threshold), the answer is refused without calling the LLM at all.

**What instructions do you give the LLM to keep it grounded?**
The RAG prompt instructs the model to:
- Answer using **only** the information in the provided snippets
- Not invent functions, endpoints, or configuration values
- Reply exactly "I do not know based on the docs I have." if snippets are insufficient
- Briefly mention which files it relied on

---

## 4. Experiments and Comparisons

Run the **same set of queries** in all three modes. Fill in the table with short notes.

| Query | Naive LLM: helpful or harmful? | Retrieval only: helpful or harmful? | RAG: helpful or harmful? | Notes |
|------|---------------------------------|--------------------------------------|---------------------------|-------|
| Where is the auth token generated? | Harmful — generic JWT/OAuth essay, never mentions `generate_access_token` or `auth_utils.py` | Partially helpful — returns relevant AUTH.md paragraph but not the generate_access_token line | Partially helpful — vague ("when a user is authenticated"), misses the specific function name | Retrieval gets wrong paragraph (env vars section vs token generation section) |
| Which endpoint returns all users? | Harmful — invented `GET /api/v1/users` with pagination that doesn't exist | Helpful — returns `### GET /api/users` with description | Helpful — correctly answers `GET /api/users` (admin only) | Best RAG result in the test set |
| How does a client refresh an access token? | Harmful — describes generic OAuth refresh flows, never mentions `/api/refresh` | Helpful — returns the AUTH.md paragraph mentioning `/api/refresh` | Helpful — correctly answers with `/api/refresh` and cites AUTH.md | Clean end-to-end RAG success |
| Is there any mention of payment processing? | Harmful — fabricates plausible-sounding payment integration details not in the docs | Helpful — correctly refuses (guardrail triggers) | Helpful — correctly refuses | Important safety test; all modes should refuse but only retrieval-based modes do |
| Which fields are stored in the users table? | Harmful — invents a generic users schema with fields not in the docs | Harmful — retrieval fails (vocabulary mismatch: "fields" vs "Column"), refuses incorrectly | Harmful — refuses even though the answer is in DATABASE.md | Known limitation of keyword retrieval |

**What patterns did you notice?**

- **Naive LLM looks impressive but untrustworthy**: The model produces fluent, detailed, well-structured answers that sound authoritative. None of them were grounded in the actual project docs. On payment processing it would have confidently hallucinated an integration that doesn't exist.
- **Retrieval only is clearly better for safety**: It correctly refuses off-topic questions and never invents content. The output is raw and hard to read, but it is honest about what it knows.
- **RAG is clearly better than both when retrieval works**: It combines the readability of LLM output with the accuracy of retrieval. When the right paragraph is found, RAG produces short, correct, cited answers. When retrieval fails (vocabulary mismatch), RAG fails silently with a refusal instead of hallucinating.

---

## 5. Failure Cases and Guardrails

**Describe at least two concrete failure cases you observed.**

> **Failure case 1**: "Where is the auth token generated?"
> The system retrieved an AUTH.md paragraph about `TOKEN_LIFETIME_SECONDS` (contains the word "token") instead of the paragraph about `generate_access_token`. The RAG answer was vague ("generated when a user is authenticated") and never mentioned `generate_access_token` or `auth_utils.py`. The retrieval found the right document but the wrong paragraph within it — the scoring was not fine-grained enough to distinguish two paragraphs in the same file.

> **Failure case 2**: "Which fields are stored in the users table?"
> The answer is clearly present in DATABASE.md (the users table schema with user_id, email, password_hash, joined_at). However, the query uses "fields" and "stored" while the document uses "Column" and "Stores". The keyword retrieval found no strong match and refused to answer — a false negative caused by vocabulary mismatch between the question and the documentation language.

**When should DocuBot say "I do not know based on the docs I have"?**

1. When no retrieved paragraph scores above the minimum evidence threshold (e.g., payment processing, unrelated geography questions).
2. When the query uses vocabulary that doesn't appear in the docs (e.g., "fields" when the doc says "column") — though this is currently indistinguishable from a genuinely unanswerable question.
3. In RAG mode, when the model determines the provided snippets don't contain enough evidence — enforced by the prompt instruction.

**What guardrails did you implement?**

- **Score threshold refusal**: `retrieve()` returns `[]` if the best paragraph score is below `MIN_SCORE_THRESHOLD = 1.5`. This prevents low-confidence matches from reaching the LLM.
- **Stop word filtering**: Common words (is, of, the, what, etc.) are excluded from scoring so they can't accumulate into false positives.
- **Whole-word matching**: Prevents "is" from matching as a substring of "This", "missing", "description" — which was causing every paragraph to score non-zero.
- **LLM prompt refusal rule**: The RAG prompt explicitly instructs the model to reply "I do not know based on the docs I have." when snippets are insufficient, rather than guessing.

---

## 6. Limitations and Future Improvements

**Current limitations**

1. **Vocabulary mismatch**: Keyword retrieval requires exact word overlap. Synonyms, morphological variants ("fields" vs "columns", "stored" vs "contains"), and paraphrases cause retrieval failures.
2. **Paragraph granularity is still coarse**: Some answers span multiple paragraphs. Retrieving the right paragraph may miss context from adjacent sections.
3. **No ranking signal beyond IDF**: The scoring doesn't account for term position, section importance, or document structure. A term in a heading is treated the same as one in a footnote.
4. **Threshold is hand-tuned**: `MIN_SCORE_THRESHOLD = 1.5` was set by manual inspection of a few queries. It may not generalize well to different doc corpora.

**Future improvements**

1. **Semantic (embedding-based) retrieval**: Replace keyword matching with vector similarity so "fields" can match "columns" and "connect to the database" can match "DATABASE_URL configuration".
2. **Sentence-level chunking with overlap**: Split paragraphs into overlapping sentence windows so context from adjacent sentences is preserved without merging entire sections.
3. **Reranking**: After keyword retrieval, use a small model to rerank candidates by semantic relevance before passing to the generator.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**

Naive LLM mode (Mode 1) is the highest risk: it produces confident, detailed answers that are entirely ungrounded in the project docs. A developer who trusts a hallucinated endpoint path, authentication flow, or database connection string could introduce security vulnerabilities or data loss bugs. Even in RAG mode, if retrieval returns the wrong paragraph (as in the auth token example), the model may confidently state something that sounds correct but is incomplete or slightly wrong.

**What instructions would you give real developers who want to use DocuBot safely?**

- Always verify DocuBot answers against the source documentation before acting on them, especially for security-sensitive topics like authentication, credentials, or database access.
- Treat a refusal ("I do not know") as a signal to search the docs manually — it means the system could not find evidence, not that the answer doesn't exist.
- Do not use Naive LLM mode (Mode 1) for project-specific questions. It has no access to your actual docs and will generate plausible-sounding but fabricated answers.
- The retrieval system matches keywords, not meaning. If your question uses different vocabulary than the documentation, rephrase it using terms likely to appear in the docs.

---
