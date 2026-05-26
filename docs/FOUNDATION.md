# Foundation And Scope of the Project

## How to use this document

This is the north-star document for the project.

Use this file to answer only these questions:
- What are we building?
- Why does it exist?
- What must stay true?
- What are we explicitly not trying to solve here?

This document should remain stable and high level.
It should not contain implementation details, algorithms, code structure, or task checklists.

If a future change does not affect the project mission, design principles, evidence-unit definition, or top-level success criteria, it probably does not belong here.

## 1. Problem statement

We need a local-first knowledge foundation that can ingest heterogeneous local content, preserve source truth, and convert raw material into trustworthy retrieval units for later search and explanation.

The core architecture problem is not file parsing or generic chunking. It is how to derive evidence units that remain faithful to source material while also being coherent enough for retrieval and explicit enough for grounded explanation. If this layer is poorly defined, the system drifts toward brittle embeddings, lossy summaries, unclear provenance, and explanations that cannot be audited.

The architecture must therefore establish a durable contract between:
* raw local sources,
* normalized source records,
* evidence units with provenance and boundaries,
* retrieval over those units,
* explanation that can cite and reconstruct source support.

## 2. Goals

* Support local-first, offline-capable operation as a primary constraint, not a fallback mode.
* Ingest mixed local formats into a common source model without erasing format-specific structure.
* Preserve source truth so every downstream unit can be traced back to exact source locations.
* Define evidence units as the primary retrieval and explanation substrate.
* Ensure evidence units are:
    * faithful to source,
    * coherent as standalone support,
    * bounded and traceable,
    * stable enough for indexing and reuse.
* Enable retrieval that returns supportable evidence, not merely semantically similar text.
* Enable explanations that are explicitly grounded in returned evidence units.
* Create architectural boundaries that reduce future drift in parsing, segmentation, indexing, and explanation behavior.

## 3. Non-goals

* Not designing the final parsing stack for each file type.
* Not selecting specific embedding models, vector databases, or ranking algorithms.
* Not optimizing for cloud-native, multi-tenant, or always-online operation.
* Not building an autonomous agent framework.
* Not solving general knowledge synthesis beyond what can be grounded in local evidence.
* Not treating summarization or note generation as source truth.
* Not defining UI, interaction flows, or product surface in detail.
* Not maximizing recall at the expense of provenance and faithfulness.

## 4. Design principles

**Source-first**
Raw source artifacts are the authority. Derived representations must never replace or obscure them.

**Evidence over chunks**
Segmentation should be governed by evidentiary usefulness and provenance, not fixed token windows.

**Traceability by construction**
Every evidence unit must preserve a verifiable link to source file, source version, and source span.

**Structure-aware normalization**
Normalization should preserve meaningful document structure where available: headings, code blocks, log events, SQL statements, page/section boundaries, tables-as-text, and extracted document hierarchy.

**Minimal irreversible transformation**
The pipeline should avoid transformations that cannot be audited or reconstructed.

**Stable identities**
Sources and evidence units should have durable identities so indexing and references remain consistent across reprocessing when content is unchanged.

**Separation of concerns**
Parsing, normalization, evidence construction, indexing, retrieval, and explanation should be distinct layers with explicit contracts.

**Grounded explanation only**
Explanation must be generated from retrieved evidence units and should expose support, uncertainty, and gaps.

**Offline-first operability**
Core ingestion, indexing, retrieval, and explanation should function without network dependence.

**Drift resistance**
Architecture should favor explicit schemas and invariants over ad hoc heuristics embedded across components.

## 5. Definition of an evidence unit

An evidence unit is the smallest retrieval-ready, explanation-ready representation of source content that is:
* **faithful:** does not alter substantive meaning of the source span,
* **traceable:** linked to exact origin and boundaries,
* **coherent:** understandable enough to stand as support in retrieval and explanation,
* **bounded:** has clear start and end conditions,
* **context-sufficient:** includes the minimum local context needed to avoid misleading interpretation,
* **stable:** can be re-derived consistently from unchanged source input.

An evidence unit is not just a text chunk. It is a structured record with at least:
* source identifier
* source version or content hash
* source type
* span reference or structural location
* normalized text payload
* structural context
* boundary rationale
* integrity metadata

Conceptually, an evidence unit should answer:
“What exact source material supports this claim or answer, and where did it come from?”

**Examples by source type:**
* **Markdown/doc text:** a section, subsection, list block, or paragraph group with heading context
* **Logs:** a single event or tightly related event sequence
* **Code:** a function, class, method, config block, or semantically complete snippet
* **SQL:** a full statement, view definition, migration step, or logically complete clause group
* **PDF/docx extracted text:** a section-like span reconstructed from extraction structure, with page/location references preserved

## 6. Primary downstream use cases

* **Retrieval for question answering:** Return source-grounded evidence units relevant to a user query.
* **Grounded explanation:** Generate answers, summaries, or comparisons using only retrieved evidence units, with visible provenance.
* **Evidence inspection:** Allow a user or system component to inspect the supporting unit and, when needed, expand back to source context.
* **Cross-source corroboration:** Retrieve multiple evidence units that support, refine, or conflict with one another across files.
* **Local knowledge navigation:** Use evidence units as navigable anchors into the source corpus, not just as ranking artifacts.
* **Change-aware reindexing:** When local files change, update affected evidence units without destabilizing the rest of the corpus.

## 7. Success criteria

The architecture is successful if it produces a system where:
* every answerable output can be tied to explicit source-backed evidence;
* evidence units are more reliable for retrieval and explanation than naïve chunking;
* provenance survives ingestion, normalization, indexing, and explanation;
* reprocessing unchanged files yields materially identical evidence units;
* mixed-format content can be represented in one retrieval architecture without flattening all structure away;
* the system works fully offline for core workflows;
* architecture decisions remain compatible with multiple later implementation choices;
* teams can detect when a result is unsupported, weakly supported, or ambiguous.

**Practical evaluation signals:**
* high citation accuracy to source spans,
* low rate of misleading boundary formation,
* strong retrieval usefulness for real tasks,
* low explanation hallucination when constrained to evidence,
* predictable incremental updates after source edits.

## 8. Risks and tradeoffs

**Coherence vs fidelity**
Larger units improve readability and retrieval context, but increase risk of mixed topics and weaker trace precision. Smaller units improve traceability, but can lose interpretive context.

**Uniform model vs source-specific handling**
A single evidence abstraction simplifies architecture, but aggressive unification can erase important distinctions between prose, code, logs, and SQL.

**Normalization vs source preservation**
More normalization improves downstream consistency, but can hide extraction artifacts, formatting cues, or original ordering needed for auditability.

**Stability vs adaptability**
Stable evidence identities are valuable, but overly rigid segmentation may resist improvements in evidence construction logic.

**Retrieval performance vs trustworthiness**
Fast approximate retrieval may surface plausible but weakly grounded units. Trustworthy retrieval may require richer metadata and more disciplined ranking.

**Extraction quality ceiling**
For PDF- and docx-extracted text, evidence quality is limited by extraction fidelity. The system must avoid overstating certainty when source structure is degraded.

**Explanation convenience vs grounding discipline**
It is tempting to synthesize across units too freely. Without strict grounding rules, explanations drift beyond what the evidence supports.

**Architecture drift risk**
If evidence-unit contracts are underspecified, later teams may reintroduce generic chunking under different names, weakening provenance and explanation quality.

***

A useful guardrail for the brief is this:

The system’s primary indexed object is not raw text and not summary text; it is a provenance-bearing evidence unit derived from source truth for retrieval and grounded explanation.