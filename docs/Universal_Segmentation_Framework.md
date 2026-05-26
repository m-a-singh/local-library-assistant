# Universal Segmentation Framework

## How to use this document

This is the segmentation policy document.

Use this file to answer only these questions:
- How are segmentation decisions made?
- What kinds of evidence justify a boundary?
- What kinds of evidence justify keeping spans together?
- When should context attach instead of merge?
- What is universal across the system and what is source-specific?

This document defines the segmentation decision framework.
It should not be used as the working task tracker for implementation.

## Design stance

This system should not use one giant weighted heuristic engine.

Instead, it should use:

- one universal segmentation framework
- a fixed set of evidence classes
- a fixed processing order
- explicit ambiguity handling
- source-specific profiles that tune behavior without changing the contract

The framework operates over the existing canonical model:

`SourceDocument -> ExtractedText -> StructuralRegion -> EvidenceUnit -> DerivedArtifact`

Segmentation therefore has two outputs, not one:

- `StructuralRegion`: source-preserving structural spans
- `EvidenceUnit`: retrieval-ready, explanation-ready support units

The segmenter is a decision framework, not a scoring soup. It should decide boundaries by typed evidence and precedence, not by opaque blended weights.

## 1. Universal segmentation objectives

The universal framework should optimize for these objectives in this order:

1. Preserve source fidelity.
2. Preserve recoverable provenance.
3. Produce bounded units with explicit start and end conditions.
4. Preserve local coherence.
5. Preserve enough surrounding context to avoid misleading interpretation.
6. Make outputs useful for retrieval and grounded explanation.
7. Remain stable across reprocessing when source input is unchanged.
8. Expose ambiguity instead of hiding it.

Operationally, the framework should:

- segment by evidentiary usefulness, not token count
- preserve hierarchy and adjacency
- allow mixed-content spans without flattening them into one modality
- degrade gracefully when structure is weak
- separate structural preservation from retrieval shaping

## 2. Boundary evidence classes

Boundary evidence is any evidence that a segment should start, end, or split.

The framework should use typed boundary classes with precedence, not a blended score.

### A. Hard structural boundaries

These normally force a split unless a profile explicitly says otherwise.

- document section or subsection boundary
- heading transition
- fenced block start or end
- function, class, method, or config block boundary
- SQL statement boundary
- log event boundary
- shell prompt or command start
- page or block boundary when layout sources have no better structure

Why it matters:
Hard structural boundaries are the most stable and source-faithful segmentation cues.

### B. Syntactic closure boundaries

These indicate a semantically complete unit.

- paragraph termination
- balanced code construct
- completed SQL statement
- completed log record
- completed command plus its attached output

Why it matters:
They help avoid fragments that are traceable but too incomplete to explain anything correctly.

### C. Modality-shift boundaries

These indicate a change in content kind.

- prose to code
- prose to SQL
- command to output
- narrative text to quoted log or stack trace
- paragraph text to table-like text

Why it matters:
Large mixed-modality spans are often poor evidence units even if they are structurally adjacent.

### D. Provenance discontinuity boundaries

These reflect a break in reliable source mapping.

- span-map gap
- page-order uncertainty
- OCR block merge uncertainty
- extracted ordering conflict

Why it matters:
A segment should not silently cross a point where provenance becomes discontinuous or uncertain.

### E. Soft topical boundaries

These are weaker cues and should only refine segmentation after higher-confidence classes have been considered.

- clear topic shift within a section
- speaker or actor change
- changed request or response focus
- changed target object or system in technical notes

Why it matters:
They improve coherence, but should not override stronger source-preserving structure.

## 3. Cohesion evidence classes

Cohesion evidence is any evidence that adjacent material should stay together as one segment.

### A. Structural cohesion

- same heading scope
- same list item or same nested list subtree
- same function, class, or statement block
- same log event or same shell command-output group

Why it matters:
This is the strongest reason to keep neighboring content together.

### B. Syntactic cohesion

- open construct requires following lines to complete meaning
- multiline statement or expression
- continuation indent
- unclosed quote, bracket, block, or clause

Why it matters:
Splitting too early creates evidence that is precise but misleading.

### C. Referential cohesion

- immediate pronoun or shorthand dependency
- command output that only makes sense with the preceding command
- a code docstring that directly scopes the following symbol
- a heading that directly scopes the next paragraph or list

Why it matters:
Some spans are locally incomplete without a tightly attached neighboring span.

### D. Event cohesion

- multiple lines belong to one event instance
- exception plus stack trace
- request line plus immediately attached response details
- migration note plus the SQL block it governs

Why it matters:
Event-like data often spans multiple lines but should be retrieved as one support unit.

### E. Locality cohesion

- contiguous source order
- same page/block neighborhood
- no stronger intervening boundary

Why it matters:
Locality alone is weak, but it is a useful tie-breaker when stronger evidence is absent.

## 4. Context attachment rules

The framework should distinguish between:

- primary segment content
- attached context

Attached context is not the same as merging. It is context linked to a segment because removing it would risk misleading interpretation.

### Rule 1: Attach context only when it changes interpretation

Attach a local heading, label, symbol name, session label, or docstring when the primary span would otherwise be ambiguous.

### Rule 2: Prefer attachment over expansion

If a small primary span is valid but needs nearby scope, keep the primary span small and attach the scope as context through provenance links or parent structural references.

### Rule 3: Context must be local and explicit

Attach only context that is:

- structurally enclosing
- immediately preceding
- immediately following when the source type makes that natural
- explicitly paired by syntax or layout

Do not attach distant context based only on semantic similarity.

### Rule 4: Keep role distinctions explicit

Context should carry a typed role such as:

- heading_context
- label_context
- scope_context
- doc_context
- output_context
- disambiguating_prefix

This prevents the system from pretending all attached text is primary support.

### Rule 5: Preserve parent-child and adjacency instead of over-attaching

If neighboring context may matter later but is not required now, represent it through:

- `parent_region_id`
- `prev_region_id`
- `next_region_id`
- `parent_evidence_unit_id`
- `prev_evidence_unit_id`
- `next_evidence_unit_id`

That allows downstream retrieval and explanation to expand context without bloating the core unit.

### Rule 6: Allow source-specific paired attachments

Examples:

- shell command -> attached command output
- code symbol -> attached docstring
- SQL note -> attached SQL statement
- PDF paragraph -> attached page heading or caption

These are profile-tuned but use the same universal attachment contract.

## 5. Ambiguity handling rules

Ambiguity is a first-class output, not an implementation error.

### Rule 1: Never resolve uncertainty silently

If the segmenter is unsure whether content should split, merge, or attach, it must record the uncertainty explicitly.

### Rule 2: Prefer larger faithful spans over falsely precise spans

When evidence is insufficient for a precise split, the system should keep a slightly larger but still truthful segment rather than invent a crisp boundary.

### Rule 3: Preserve competing interpretations when materially different

If two plausible segmentations would lead to meaningfully different retrieval behavior, preserve one primary segmentation and emit ambiguity markers or alternates rather than pretending there was only one answer.

### Rule 4: Distinguish ambiguity classes

At minimum, ambiguity should distinguish:

- uncertain boundary
- uncertain attachment
- mixed modality
- extraction loss
- ordering uncertainty
- overlapping candidate units
- insufficient context

### Rule 5: Degrade by layer

When ambiguity is high:

- still emit `StructuralRegion` if structure is recoverable
- emit `EvidenceUnit` only if the unit remains source-faithful and explanation-safe
- otherwise preserve the region and defer evidence shaping

### Rule 6: Never fabricate recoverability

If exact lines or char offsets are missing, carry page/block or other truthful locators and mark the limitation.

## 6. Segmentation stages

The framework should run in ordered stages. Each stage has a narrow responsibility.

### Stage 0: Preconditions and intake

Input requirements:

- one `ExtractedText` tied to a single `SourceDocument` snapshot
- span mapping and best-available locators
- extractor fidelity and ambiguity metadata
- source profile selection

Output:

- segmentation job context

### Stage 1: Structural pass

Identify obvious structure without trying to optimize final evidence size.

Examples:

- headings and sections
- code blocks and symbols
- SQL statements
- log events
- shell commands and outputs
- page and block spans

Output:

- candidate `StructuralRegion` graph with hierarchy and adjacency

### Stage 2: Boundary classification

Evaluate candidate breaks using universal boundary evidence classes:

- hard structural
- syntactic closure
- modality shift
- provenance discontinuity
- soft topical

Output:

- typed candidate boundaries with strength class, not blended score

### Stage 3: Cohesion pass

Evaluate whether neighboring spans should remain together despite available split points.

Output:

- kept-together groups and blocked splits

### Stage 4: Context attachment pass

Determine which nearby spans are:

- primary support
- attached context
- merely adjacent and expandable later

Output:

- attachment graph and explicit context roles

### Stage 5: Evidence shaping

Transform structural-preserving groups into retrieval-ready `EvidenceUnit` candidates.

This stage should:

- keep units bounded
- preserve explicit support links
- attach only the minimum context needed
- preserve parent and neighbor references

### Stage 6: Ambiguity and fallback pass

Apply ambiguity rules where the segmenter cannot safely produce a crisp evidence unit.

Output:

- ambiguous units
- deferred units
- fallback larger units when needed

### Stage 7: Validation pass

Validate final outputs against the canonical invariants:

- every evidence unit is traceable to source
- every unit has a recoverable locator
- parent-child and adjacency links are consistent
- boundaries have rationale
- ambiguity is explicit where certainty is weak

## 7. What is universal vs what varies by source type

### Universal

These must be fixed across the system:

- the layered output model: `StructuralRegion` then `EvidenceUnit`
- the stage order
- the boundary evidence taxonomy
- the cohesion evidence taxonomy
- the context attachment contract
- the ambiguity contract
- provenance and recoverability invariants
- the definition of a good segment

### Source-specific

Profiles may tune:

- which concrete cues instantiate each evidence class
- which boundaries are treated as hard versus soft
- which context types are attachable
- default target granularity
- mixed-content handling
- fallback behavior when extraction quality is weak
- preferred evidence unit types

The profile should tune the same slots, not invent a new segmentation algorithm.

## 8. Source-profile model

A source profile is a configuration of the universal framework, not a replacement for it.

Each profile should define:

- `profile_id`
- `source_kind`
- `primary_structural_units`
- `hard_boundary_cues`
- `soft_boundary_cues`
- `cohesion_cues`
- `attachable_context_roles`
- `default_unit_targets`
- `ambiguity_patterns`
- `fallback_strategy`
- `mixed_content_policy`
- `min_recoverability_requirements`

### `markdown`

Primary units:

- section
- subsection
- list item or list block
- fenced code block
- table-like block
- paragraph group

Hard boundaries:

- heading changes
- fenced block boundaries
- table boundaries

Cohesion:

- heading scope
- list subtree
- paragraph continuity

Attachable context:

- heading path
- list label
- link target as metadata, not replacement text

Fallback:

- keep section-local groupings when paragraph-level cohesion is unclear

### `shell transcript`

Primary units:

- session
- command
- command plus immediate output
- error block

Hard boundaries:

- prompt line
- explicit session marker
- command switch

Cohesion:

- command plus directly resulting output
- contiguous multiline output

Attachable context:

- session label
- working directory
- prompt metadata

Fallback:

- keep command-output pairs together when exact output boundaries are uncertain

### `code`

Primary units:

- module section
- class
- function or method
- config block
- docstring plus symbol context

Hard boundaries:

- symbol definitions
- block delimiters
- top-level declaration changes

Cohesion:

- block scope
- multiline expression
- docstring-to-symbol relationship

Attachable context:

- symbol name
- signature
- docstring
- enclosing class or module

Fallback:

- prefer symbol-level units over arbitrary line windows

### `logs`

Primary units:

- event
- event with stack trace
- request-response cluster when explicitly correlated

Hard boundaries:

- timestamped event starts
- correlation-id changes
- parser-recognized event delimiters

Cohesion:

- multiline event continuation
- stack trace continuation
- same correlation scope

Attachable context:

- severity
- host/process/request metadata
- surrounding session label if explicit

Fallback:

- prefer whole event clusters when line-level attachment is uncertain

### `sql-heavy notes`

Primary units:

- prose note block
- SQL statement
- note plus statement pair
- migration step

Hard boundaries:

- SQL statement terminators
- fenced SQL block boundaries
- section changes

Cohesion:

- statement completeness
- prose line that directly scopes a following statement
- migration-step grouping

Attachable context:

- note label
- dialect hint
- surrounding heading

Fallback:

- preserve note-plus-statement groups when explanation would be misleading without both

### `pdf-extracted text`

Primary units:

- page block
- reconstructed paragraph
- heading-like block
- caption-like block

Hard boundaries:

- page changes
- layout block boundaries
- detected heading changes

Cohesion:

- same block or neighboring blocks with stable reading order
- same paragraph reconstruction when extraction supports it

Attachable context:

- page heading
- caption
- nearby label

Fallback:

- prefer page/block-grounded units over overconfident paragraph reconstruction

### `generic plain text`

Primary units:

- paragraph
- paragraph group
- delimiter-separated block
- short local sequence

Hard boundaries:

- blank-line runs
- explicit delimiter lines
- obvious section labels

Cohesion:

- paragraph continuity
- indentation continuity
- local referential continuity

Attachable context:

- nearest explicit label or heading-like line

Fallback:

- prefer paragraph groups over arbitrary sentence slicing when structure is weak

## Good segment definition

A good segment for this system is one that satisfies all five of these properties.

### Source fidelity

The segment does not materially distort source meaning.

Practical test:

- it can be justified by explicit source spans
- it does not silently rewrite or infer missing source content
- it does not cross a provenance discontinuity without marking it

### Coherence

The segment is understandable enough to stand as support.

Practical test:

- it is not a random fragment
- it captures one locally coherent unit of meaning, action, event, or structure
- it does not mix unrelated material just because it is nearby

### Context sufficiency

The segment includes or attaches the minimum local context needed to avoid misleading interpretation.

Practical test:

- a reader can tell what the segment is about
- scoped context such as heading, symbol, or command relationship is available
- essential nearby context is attached or reachable through parent/neighbor links

### Explainability

The segment can be cited and used in grounded explanation without hand-waving.

Practical test:

- the system can say why this segment exists
- the system can show what source span supports it
- the system can expose ambiguity or weakness when support is imperfect

### Recoverability

The segment can lead a user or downstream component back to source.

Practical test:

- it has a source document reference
- it has a source snapshot reference
- it has the best truthful locator available
- it has parent/child or adjacency links when local expansion matters

## Guardrail

The universal segmenter should not ask:

"What chunk size should we use?"

It should ask:

"What source-faithful unit can stand as retrievable evidence here, and what context must remain attached or reachable so explanation stays grounded?"
