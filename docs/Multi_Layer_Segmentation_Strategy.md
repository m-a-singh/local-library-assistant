# Multi-Layer Segmentation Strategy

## How to use this document

This document is a supporting design note for the segmentation framework.

Use it to answer these questions:
- Why do we need multiple segmentation layers?
- What is the role of StructuralRegion vs EvidenceUnit vs DerivedArtifact?
- How should retrieval and explanation move across those layers?

This file should be treated as an extension of the segmentation framework, not as a separate top-level source of truth.
If the framework and this document ever disagree, the framework should be updated so both stay aligned.

## Design stance

This system should not rely on a single flat chunk list.

Instead, it should represent the corpus in layers with different jobs:

- `StructuralRegion` preserves source structure
- `EvidenceUnit` provides retrieval-ready support
- `DerivedArtifact` provides specialized secondary views

This layered strategy is what makes the system resilient when segmentation is imperfect.

If one layer is weak:

- the structural layer can still preserve source order and hierarchy
- the evidence layer can stay conservative
- derived views can still point back to the best available support

The result is a system that degrades gracefully instead of forcing every use case through one brittle chunk boundary.

## Layered model

### 1. Structural regions

Purpose:

- preserve source-aware structure before retrieval shaping
- capture the document's native or reconstructed organization
- provide stable anchors for context expansion and recovery
- retain hierarchy and adjacency even when the ideal evidence boundary is unclear

Ideal granularity:

- section, subsection, list block, paragraph group
- function, class, method, config block
- log event, event sequence, stack trace
- shell command-output group or session block
- SQL statement, migration step, prose-plus-statement block
- PDF/docx block, heading-like span, paragraph-like span, caption-like span

Structural regions should usually be broader and more source-preserving than evidence units.

How it is produced:

- directly from normalized `ExtractedText`
- through the universal segmentation framework's structural pass
- using structural, syntactic, and layout cues
- with ambiguity preserved when the structure is only partially recoverable

What metadata it should carry:

- `structural_region_id`
- `source_document_id`
- `source_snapshot_id`
- `extracted_text_id`
- `region_type`
- `primary_span`
- `supporting_spans`
- `ordinal`
- `content_facets`
- `boundary_basis`
- `parent_region_id`
- `prev_region_id`
- `next_region_id`
- `label`
- `heading_path`
- `ambiguity`
- `quality_flags`
- `children_count`

Why this metadata matters:

- span fields preserve recoverability to source
- hierarchy fields preserve enclosing scope
- adjacency fields preserve local navigation
- content facets preserve mixed-modality truth
- ambiguity and quality fields prevent overstating confidence

How it connects to the other layers:

- it is derived from `ExtractedText`
- it provides the source-preserving context graph used to shape `EvidenceUnit`
- it remains the primary expansion target when retrieval needs more context
- it may also be used directly by `DerivedArtifact` when evidence-level shaping is not yet safe

### 2. Evidence units

Purpose:

- serve as the main retrieval object
- provide explanation-ready support units
- preserve source-faithful, bounded, locally coherent support
- stay small enough to rank and cite cleanly, but not so small that they become misleading

Ideal granularity:

- one section-local paragraph group with heading scope
- one list item or short list cluster
- one code symbol or semantically complete snippet
- one log event or tightly related event sequence
- one shell command plus its immediate output
- one SQL statement or one note-plus-statement pair
- one reconstructed paragraph or layout block when page-based extraction is degraded

Evidence units should usually be narrower than structural regions, but may inherit their context through parent, neighbor, or attachment links.

How it is produced:

- from one or more structural regions
- through a dedicated evidence-shaping pass after structure has already been identified
- by applying cohesion rules, context attachment rules, and ambiguity rules
- by requiring explicit provenance and a recoverable locator

What metadata it should carry:

- `evidence_unit_id`
- `source_document_id`
- `source_snapshot_id`
- `unit_type`
- `canonical_text`
- `support_links`
- `structural_region_ids`
- `ordinal`
- `boundary_rationale`
- `content_facets`
- `trust_state`
- `parent_evidence_unit_id`
- `prev_evidence_unit_id`
- `next_evidence_unit_id`
- `context_labels`
- `ambiguity`
- `integrity_flags`
- `keywords`
- `entity_refs`
- `conflict_refs`
- `expansion_policy_hint`

Why this metadata matters:

- `support_links` keep every unit auditable
- structural references keep retrieval grounded in larger context
- adjacency supports local expansion without over-merging
- ambiguity and integrity flags let explanation expose uncertainty
- keywords and entities improve retrieval without replacing provenance

How it connects to the other layers:

- it is produced from structural regions, not directly from raw source
- it is the primary retrieval and ranking unit
- it expands outward to structural parents, children, and neighbors when needed
- it is the primary input to most derived artifacts

### 3. Derived views and artifacts

Purpose:

- create specialized access paths over the corpus
- support tasks like command indexing, link indexing, query indexing, citation graphs, entity maps, and navigation summaries
- improve retrieval and inspection without replacing evidence as source-backed truth

Ideal granularity:

- one artifact per source snapshot, per source document, or per focused corpus slice
- one index entry per derived target, but always tied back to evidence or structure

Derived artifacts should be more task-shaped than evidence units, but they must remain traceable and non-authoritative.

How it is produced:

- from `EvidenceUnit` where evidence shaping is strong enough
- from `StructuralRegion` only when a specialized derived view can be supported there without inventing unsupported claims
- through deterministic extraction/indexing passes that emit explicit provenance

What metadata it should carry:

- `derived_artifact_id`
- `artifact_type`
- `artifact_schema_version`
- `source_scope`
- `derived_from`
- `payload`
- `producer_name`
- `producer_version`
- `artifact_hash`
- `target_refs`
- `ambiguity`
- `confidence`
- `refresh_state`
- `notes`
- `backfill_needed`
- `retrieval_tags`

Why this metadata matters:

- `derived_from` preserves the chain back to evidence or structure
- `source_scope` keeps artifact coverage explicit
- `target_refs` support navigation and downstream tools
- confidence and ambiguity keep artifacts useful without letting them masquerade as truth

How it connects to the other layers:

- it consumes evidence or structural anchors
- it never replaces the evidence layer as the primary explanation substrate
- it can accelerate retrieval or navigation, but every artifact must resolve back to evidence or structure, and then back to source

## Parent-child relationships

Parent-child relationships are required at more than one layer.

### Structural parent-child

Use structural hierarchy to preserve native or reconstructed document organization.

Examples:

- section -> subsection
- function -> docstring
- shell session -> command block
- log session -> event
- page block -> caption-like span

Why it matters:

- preserves scope
- allows retrieval to expand from a local unit to enclosing context
- helps explanation expose where a unit sits in the larger source

### Evidence parent-child

Use evidence hierarchy when there are multiple useful granularities inside one coherent support structure.

Examples:

- section-level evidence group -> paragraph-level evidence units
- function-level evidence group -> body-level evidence unit
- migration-step evidence group -> statement-level evidence units

Why it matters:

- allows coarse and fine retrieval to coexist
- avoids forcing one "perfect" segment size
- supports explanation that starts with one tight support unit and expands to a richer, still-grounded parent

### Cross-layer parentage

Every evidence unit should link back to one or more structural regions.

This is the core cross-layer relationship:

- structural regions preserve organization
- evidence units preserve supportability

When segmentation is imperfect, this cross-layer link is what keeps the system usable.

## Adjacency relationships

Adjacency should be explicit, not inferred only from offsets.

### Structural adjacency

Structural neighbors capture source-local movement.

Examples:

- previous and next paragraph group in a section
- previous and next command block in a shell session
- previous and next event in a log stream
- previous and next page block in a PDF extraction

Use:

- `prev_region_id`
- `next_region_id`

### Evidence adjacency

Evidence neighbors capture retrieval-safe local expansion.

Examples:

- previous and next paragraph evidence unit
- previous and next SQL statement in a migration note
- previous and next shell command-output unit

Use:

- `prev_evidence_unit_id`
- `next_evidence_unit_id`

Why adjacency matters:

- enables expansion without rescanning the entire document
- supports explanation that says "here is the exact support, and here is the immediate surrounding context"
- remains useful when hierarchy is weak or absent

## Retrieval expansion behavior

Retrieval should treat the layered model as a progressive expansion graph, not a one-shot chunk fetch.

### Default retrieval flow

1. Retrieve candidate `EvidenceUnit` objects.
2. Rank them using text relevance plus provenance, trust, ambiguity, and modality signals.
3. Expand only if needed using explicit relationships.

### Expansion targets

From an `EvidenceUnit`, retrieval may expand to:

- parent structural region
- sibling evidence units
- previous and next evidence units
- parent evidence unit
- attached context spans
- relevant derived artifacts

### Expansion rules

Expand upward when:

- the unit is locally correct but too narrow to interpret alone
- heading, symbol, or session scope is required
- the query asks for summary, comparison, or procedural context

Expand sideways when:

- the unit likely belongs to a short local sequence
- neighboring steps or events are necessary to interpret order or causality
- the unit has ambiguity that nearby context may resolve

Expand downward when:

- a matched parent evidence or structural group is too broad
- explanation needs a more exact citation
- the user asks for the specific line, command, or statement

Use derived artifacts when:

- the task is index-like, such as finding commands, links, entities, or queries
- the artifact can quickly route the system back to relevant evidence

### Robustness rule

When segmentation quality is uncertain:

- prefer evidence-first retrieval
- expand conservatively through structure
- do not allow derived artifacts to outrank source-backed evidence unless they resolve back to it immediately

## How explanation should use the layered model

Explanation should not treat all layers as equal.

### Primary explanation substrate

Explanation should be built primarily from `EvidenceUnit`.

Why:

- evidence units are the smallest objects intended to stand as support
- they carry explicit provenance and boundary rationale
- they expose ambiguity and trust state

### Context layer for explanation

`StructuralRegion` should provide the explanation context frame.

Use it to answer:

- what section or symbol this evidence belongs to
- what came before or after it
- whether there is an enclosing task, session, or document scope

### Supporting views

`DerivedArtifact` should support explanation only as a navigation or summarization aid.

It can help answer:

- where commands occur
- which links are present
- what entities or queries are referenced

But it should not be treated as final evidence unless it resolves back to evidence units in the response flow.

### Explanation workflow

1. Start from matched evidence units.
2. Cite their source spans and trust state.
3. Pull minimal structural context needed to make them interpretable.
4. Surface ambiguity where relevant.
5. Use derived artifacts only to organize or route, not to replace support.

## Robustness under imperfect segmentation

This layered model is specifically designed for imperfect real-world corpora.

### If structural segmentation is imperfect

- keep broader structural regions
- preserve adjacency and hierarchy where possible
- let evidence shaping stay conservative

### If evidence shaping is imperfect

- keep explicit provenance and ambiguity
- preserve links to structural parents and neighbors
- allow retrieval to widen or narrow context dynamically

### If derived extraction is imperfect

- keep artifacts secondary
- mark ambiguity and backfill needs
- always route back to evidence or structure

### Practical guardrails

- never collapse the corpus into one flat chunk list
- never require one layer to solve every downstream use case
- never let derived views become source truth
- never discard parent or neighbor relationships just because a unit is individually retrievable

## Summary model

The layered model should behave like this:

- `StructuralRegion` answers: "What source-preserving structure exists here?"
- `EvidenceUnit` answers: "What bounded support should retrieval and explanation use?"
- `DerivedArtifact` answers: "What specialized access path or index can help us navigate this evidence?"

This separation is what keeps the system trustworthy.

When segmentation is strong, the layers reinforce each other.

When segmentation is weak, the layers prevent a local mistake from becoming a system-wide loss of provenance, context, or explainability.
