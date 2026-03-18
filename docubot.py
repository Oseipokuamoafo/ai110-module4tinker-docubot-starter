"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob

# Minimum IDF-weighted score for a paragraph to be considered relevant.
# Paragraphs scoring below this are treated as noise and trigger a refusal.
MIN_SCORE_THRESHOLD = 1.5

# Common words that appear everywhere and carry no discriminating signal.
# Excluding them prevents "what is the capital of France?" from matching docs
# solely on "is", "the", "of".
STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "i", "you", "he", "she", "it", "we", "they", "my", "your", "our",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "into",
    "and", "or", "but", "not", "so", "if", "as", "do", "does", "did",
    "what", "which", "where", "when", "who", "how", "why", "any", "some",
    "there", "this", "that", "these", "those", "all", "also", "can",
}

class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Build a retrieval index (implemented in Phase 1)
        self.index = self.build_index(self.documents)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, documents):
        """
        TODO (Phase 1):
        Build a tiny inverted index mapping lowercase words to the documents
        they appear in.

        Example structure:
        {
            "token": ["AUTH.md", "API_REFERENCE.md"],
            "database": ["DATABASE.md"]
        }

        Keep this simple: split on whitespace, lowercase tokens,
        ignore punctuation if needed.
        """
        index = {}
        for filename, text in documents:
            seen = set()
            for word in text.lower().split():
                token = word.strip('.,!?;:\'"()[]{}/-')
                if not token or token in seen:
                    continue
                seen.add(token)
                index.setdefault(token, []).append(filename)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        TODO (Phase 1):
        Return a simple relevance score for how well the text matches the query.

        Suggested baseline:
        - Convert query into lowercase words
        - Count how many appear in the text
        - Return the count as the score
        """
        tokens = {
            w.strip('.,!?;:\'"()[]{}/-') for w in query.lower().split()
            if w.strip('.,!?;:\'"()[]{}/-') and w.strip('.,!?;:\'"()[]{}/-') not in STOP_WORDS
        }
        # Use a word set for whole-word matching (avoids "is" matching "This", etc.)
        text_words = set(w.strip('.,!?;:\'"()[]{}/-') for w in text.lower().split())
        num_docs = max(len(self.documents), 1)
        score = 0.0
        for t in tokens:
            if t in text_words:
                df = len(self.index.get(t, [])) or 1
                score += num_docs / df  # rarer term = higher weight
        return score

    def extract_paragraphs(self, filename, text):
        """
        Split document text into paragraph-level chunks separated by blank lines.
        Markdown section headings (lines starting with #) are merged with the
        paragraph that follows them so endpoint names stay with their descriptions.
        Returns a list of (filename, paragraph_text).
        """
        raw = [p.strip() for p in text.split('\n\n')]
        chunks = []
        i = 0
        while i < len(raw):
            chunk = raw[i]
            # Merge heading with the next paragraph to keep context together
            if chunk.startswith('#') and i + 1 < len(raw):
                merged = chunk + '\n\n' + raw[i + 1]
                chunks.append((filename, merged))
                i += 2
            elif len(chunk) > 10:
                chunks.append((filename, chunk))
                i += 1
            else:
                i += 1
        return chunks

    def retrieve(self, query, top_k=3):
        """
        Use the index to find candidate documents, then split each into paragraphs
        and score each paragraph individually. Returns top_k paragraphs sorted by
        relevance score descending.

        Guardrail: returns [] if the best paragraph score falls below MIN_SCORE_THRESHOLD,
        which causes callers to return "I do not know based on these docs."
        """
        # Find candidate filenames that contain at least one query word
        candidates = set()
        for word in query.lower().split():
            token = word.strip('.,!?;:\'"()[]{}/-')
            for filename in self.index.get(token, []):
                candidates.add(filename)

        # Score every paragraph from every candidate document
        scored = []
        for filename, text in self.documents:
            if filename in candidates:
                for fname, para in self.extract_paragraphs(filename, text):
                    score = self.score_document(query, para)
                    if score > 0:
                        scored.append((score, fname, para))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Guardrail: refuse if no paragraph meets the minimum evidence threshold
        if not scored or scored[0][0] < MIN_SCORE_THRESHOLD:
            return []

        return [(fname, para) for _, fname, para in scored[:top_k]]

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)
