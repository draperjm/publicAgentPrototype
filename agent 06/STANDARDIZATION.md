# Agent Standardization — Removing Hardcoded Domain Logic

This document identifies every place where domain-specific knowledge is baked into agent code rather than driven by process definitions, and shows the concrete refactoring for each.

The goal is agents that are **domain-ignorant**: given a new process definition JSON, any agent should behave correctly without code changes.

---

## What Changed in Process Definitions

Three new machine-readable blocks were added to each `process/*.json` file. These replace logic that was previously hardcoded in agent source files:

| New field | What it replaces | Used by |
|---|---|---|
| `route_to_step` | `if cat == "TAL": route=6` etc in reviewer + orchestrator | Orchestrator, Document Reviewer |
| `force_category_for_extensions` | `SPREADSHEET_EXTENSIONS` constant + RULE 0 in reviewer | Document Reviewer |
| `extraction_categories[]` | `tag_map`, `LOW_THRESHOLD_TAGS`, `CHECKLIST_TAGS`, hardcoded keywords in extractor prompts | Document Extractor |
| `validation_directives[]` | `TC-HV`, `TC-LV`, `TC-SUBSTATION` etc in validator SYSTEM_PROMPT | Step Validator |
| `conditional_rules[]` | 1.5MVA → Siemens RLR described only in free-text step descriptions | Document Extractor |
| `preferred_model` | `if process_id == "proc-extract-asset-spreadsheet": model = ASSET_EXTRACTION_MODEL` | Document Extractor |

All new fields are **optional** — agents fall back to defaults if absent, so existing process files without these fields continue to work.

---

## Refactoring 1 — Document Reviewer: Routing and Category Assignment

### Before
```python
# document_reviewer.py  (lines 651–699)
# Hardcoded: category strings, step numbers, SPREADSHEET_EXTENSIONS

SPREADSHEET_EXTENSIONS = {'.xlsx', '.xls', '.xlsm', '.xlsb'}

# RULE 0 enforced in code — always TAL regardless of LLM output
if Path(filename).suffix.lower() in SPREADSHEET_EXTENSIONS:
    result["document_category"] = "TAL"

# route_to_step hardcoded per category
if cat == "TAL":
    result["route_to_step"] = 6
    result["requires_chunking"] = False
    result["chunk_strategy"] = "none"
elif cat == "Site Plan":
    result["route_to_step"] = 5
elif cat == "Design Brief":
    result["route_to_step"] = 4
else:
    result["route_to_step"] = None
```

### After
```python
# document_reviewer.py (refactored)
from common.knowledge import KnowledgeRegistry

_knowledge = KnowledgeRegistry()

def _apply_routing(result: dict, filename: str) -> dict:
    cat = result.get("document_category")

    # Force category from process-defined extension rules (replaces RULE 0 + SPREADSHEET_EXTENSIONS)
    ext = Path(filename).suffix.lower()
    forced = _knowledge.category_for_extension(ext)
    if forced:
        cat = forced
        result["document_category"] = forced

    # route_to_step from process definition (replaces hardcoded if/elif)
    result["route_to_step"] = _knowledge.route_for_category(cat)

    # Chunking overrides from process definition
    proc = _knowledge.process_for_category(cat)
    if proc and ext in proc.force_category_for_extensions:
        result["requires_chunking"] = False
        result["chunk_strategy"] = "none"
        result["estimated_chunks"] = 1

    return result
```

**Adding a new document category** now requires only:
1. Create `process/process_my_new_type.json` with `route_to_step`, `document_categorisation`, and `force_category_for_extensions`
2. Add the corresponding step to `tasks.json`
3. Zero code changes

---

## Refactoring 2 — Document Reviewer: Categorisation Rules Prompt

### Before
```python
# document_reviewer.py  plan_document_processing()
# Manually builds the rules string from process contexts passed in

categorization_rules = ""
if request.process_contexts:
    rules_parts = []
    for proc_id, proc_data in request.process_contexts.items():
        dc = proc_data.get("document_categorisation", {})
        if not dc:
            continue
        category = dc.get("category", "")
        pos = dc.get("positive_indicators", {})
        neg = dc.get("negative_indicators", {})
        decision = dc.get("decision_rule", "")
        rules_parts.append(
            f"Category '{category}':\n"
            f"  Positive filename patterns: {pos.get('filename_patterns', [])}\n"
            ...
        )
    categorization_rules = "\n\n".join(rules_parts)
```

### After
```python
# Uses KnowledgeRegistry — one line
from common.knowledge import KnowledgeRegistry

_knowledge = KnowledgeRegistry()

categorization_rules = _knowledge.categorisation_rules_string(
    process_ids=list(request.process_contexts.keys()) if request.process_contexts else []
)
```

The registry also injects `route_to_step` and `force_category_for_extensions` into the rules string automatically, so the LLM planner learns about them too.

---

## Refactoring 3 — Orchestrator: Knowledge-ID to Category Mapping

### Before
```python
# orchestrator.py  (lines 146–159)
# Hardcoded mapping: knowledge_id string → category string

if knowledge_id == "proc-extract-design-brief-info":
    target_category = "Design Brief"
elif knowledge_id == "proc-extract-site-plan-info":
    target_category = "Site Plan"
elif knowledge_id == "proc-extract-asset-spreadsheet":
    target_category = "TAL"
```

### After
```python
from common.knowledge import KnowledgeRegistry

_knowledge = KnowledgeRegistry()

# Works for any process, including comma-separated lists
target_category = _knowledge.category_for_knowledge_id(knowledge_id)
```

**Adding a new process** requires only adding its JSON file — the orchestrator routing updates automatically.

---

## Refactoring 4 — Document Extractor: Extraction Categories and Tag Map

### Before
```python
# document_extractor.py  (lines 599–640)
# Hardcoded for electricity domain only

tag_map = {
    "hv":          "hv",
    "lv":          "lv",
    "sl":          "sl",
    "earthing":    "earthing",
    "easement":    "easement",
    "substation":  "substation",
    "funding":     "funding",
    "transformer": "transformer",
    ...
}

LOW_THRESHOLD_TAGS = {"funding", "easement"}
CHECKLIST_TAGS = {"easement", "earthing"}
```

And in LLM prompt:
```python
"- Tag 'funding' for any section mentioning: determination of funding, "
"  capital contribution, contestable works, non-contestable works..."
"- Tag 'easement' for any section mentioning: land interests, easement, LIG..."
```

### After
```python
from common.knowledge import KnowledgeRegistry

_knowledge = KnowledgeRegistry()

def _get_tag_config(process_id: str) -> dict:
    """Derive tag map and threshold config from process definition."""
    proc = _knowledge.get(process_id)
    if not proc or not proc.extraction_categories:
        return {"tag_map": {}, "low_threshold": set(), "checklist": set()}

    tag_map = {cat["id"]: cat["id"] for cat in proc.extraction_categories}
    low_threshold = set(proc.low_threshold_categories())
    checklist = set(proc.checklist_categories())
    return {"tag_map": tag_map, "low_threshold": low_threshold, "checklist": checklist}

def _build_tagging_prompt_section(process_id: str) -> str:
    """Build the tagging keyword rules section from process definition."""
    proc = _knowledge.get(process_id)
    if not proc:
        return ""
    return proc.tagging_rules_prompt_block()
    # Returns:
    # "Extraction categories for this process:
    #  - Tag 'hv' (High-Voltage Works): sections mentioning ["HV", "11kV", ...]
    #  - Tag 'funding' (Funding Determination): sections mentioning ["$", "funding", ...]"
```

The tag map, thresholds, and keyword rules now come from `extraction_categories[]` in the process JSON. Adding a new extraction category requires only editing the process file.

---

## Refactoring 5 — Document Extractor: Model Selection

### Before
```python
# document_extractor.py  (lines 310–314)
# Hardcoded process ID check

if request.process_id == "proc-extract-asset-spreadsheet":
    _req_context.model = ASSET_EXTRACTION_MODEL
```

### After
```python
from common.knowledge import KnowledgeRegistry

_knowledge = KnowledgeRegistry()

# Model from process definition's preferred_model field
model = _knowledge.model_for_process(
    process_id=request.process_id,
    default=settings.llm_model,
)
```

`proc-extract-asset-spreadsheet` now declares `"preferred_model": "claude-sonnet-4-6"` in its JSON. Any process can opt into a different model without code changes.

---

## Refactoring 6 — Document Extractor: Conditional Rules

### Before
The 1.5MVA → Siemens RLR rule was described **only in free text** inside step descriptions:
```
"A conditional rule is applied: if the transformer size is 1.5MVA, the HV Switchgear
must be recorded as Siemens RLR regardless of what is annotated in the drawing."
```
The extractor had to infer this from the LLM prompt — no guarantee it was applied consistently.

### After
The rule is now a machine-readable `conditional_rules[]` entry in `process_extract_site_plan_information.json`:
```json
"conditional_rules": [
  {
    "id": "rule-siemens-rlr",
    "condition": {"field": "transformer_size", "operator": "equals", "value": "1.5MVA"},
    "action":    {"field": "hv_switchgear", "set_value": "Siemens RLR"},
    "flag_field": "hv_switchgear_override_applied"
  }
]
```

Applied in the extractor post-LLM, not relying on the LLM to follow a prose instruction:
```python
proc = _knowledge.get(request.process_id)
if proc:
    extraction_result = proc.apply_conditional_rules(extraction_result)
```

`ProcessDefinition.apply_conditional_rules()` is in `common/knowledge.py` and handles any number of rules without code changes.

---

## Refactoring 7 — Step Validator: Domain-Specific Test Directives

### Before
```python
# step_validator_agent.py  SYSTEM_PROMPT  (lines 114–169)
# 50 lines of electricity-domain test directives hardcoded in the prompt

SYSTEM_PROMPT = """...
TC-HV: High-voltage assets extraction
  Search `source_pdf_raw_text` for keywords: "HV", "high voltage", "11kV", "22kV"...
  Verify that `step_extraction` contains a corresponding HV category/key...

TC-LV: Low-voltage assets extraction
  Search `source_pdf_raw_text` for keywords: "LV", "low voltage", "415V"...

TC-SUBSTATION: Substation extraction
  Search `source_pdf_raw_text` for keywords: "substation", "padmount", "kVA"...
...
"""
```

These 50 lines make the validator **only useful for the electricity domain**. Any other domain would need code changes.

### After
Remove the hardcoded TC-* directives from SYSTEM_PROMPT. The orchestrator injects process-specific directives at call time:

```python
# orchestrator.py — when calling the step validator

from common.knowledge import KnowledgeRegistry

_knowledge = KnowledgeRegistry()

def _call_validator(step, output_data, process_id=None):
    # Build injectable directives from the process definition
    directive_block = ""
    if process_id:
        directive_block = _knowledge.validation_prompt_block_for_process(process_id)
        # Returns the TC-HV, TC-LV etc block — but now from process JSON, not hardcoded

    form_data = {
        "step_name":           step.name,
        "step_description":    step.description,
        "output_data_json":    json.dumps(output_data),
        "validation_criteria": step.validation.criteria or "",
        "validation_directives": directive_block,   # ← injected per-process
    }
    return requests.post(VALIDATOR_URL, data=form_data, timeout=60)
```

```python
# step_validator_agent.py — simplified SYSTEM_PROMPT

SYSTEM_PROMPT = """You are an independent Quality Assurance Validator.
...
[standard test case categories: FORMAT, COMPLETENESS, CORRECTNESS, CONSISTENCY, DATA_INTEGRITY]
...
If PROCESS-SPECIFIC TEST DIRECTIVES are provided in the request, execute those additional
test cases after the standard ones. Directives will be clearly labelled.
"""

# In the endpoint: append directives to the prompt if present
if validation_directives:
    prompt_parts.append(f"\n## PROCESS-SPECIFIC TEST DIRECTIVES\n{validation_directives}")
```

**Adding validation for a new domain** (e.g., medical records, financial reports) requires only adding `validation_directives[]` to the process JSON — the validator code is unchanged.

---

## Summary of Changes Required

### Files to modify

| File | Change | Lines affected |
|---|---|---|
| `document_reviewer.py` | Replace `SPREADSHEET_EXTENSIONS` + RULE 0 + hardcoded `route_to_step` with `KnowledgeRegistry` calls | ~651–699 |
| `document_reviewer.py` | Replace inline categorisation rules builder with `registry.categorisation_rules_string()` | ~722–741 |
| `document_extractor.py` | Replace `tag_map`, `LOW_THRESHOLD_TAGS`, `CHECKLIST_TAGS` with `proc.extraction_categories` | ~599–640 |
| `document_extractor.py` | Replace hardcoded tagging keyword block in prompt with `proc.tagging_rules_prompt_block()` | ~1149–1168 |
| `document_extractor.py` | Replace `process_id == "proc-extract-asset-spreadsheet"` model check with `registry.model_for_process()` | ~310–314 |
| `document_extractor.py` | Add post-extraction `proc.apply_conditional_rules()` call | after extraction |
| `orchestrator.py` | Replace `knowledge_id → category` if/elif with `registry.category_for_knowledge_id()` | ~146–159 |
| `step_validator_agent.py` | Remove TC-HV through TC-FUNDING hardcoded directives from SYSTEM_PROMPT; accept `validation_directives` form field | ~114–169 |

### Files already updated (additive only)

| File | What was added |
|---|---|
| `process/process_extract_design_brief_information.json` | `route_to_step`, `extraction_categories`, `validation_directives`, `conditional_rules` |
| `process/process_extract_site_plan_information.json` | Same + 1.5MVA conditional rule |
| `process/process_extract_asset_spreadsheet.json` | Same + `preferred_model`, `force_category_for_extensions` |
| `common/knowledge.py` | `KnowledgeRegistry` + `ProcessDefinition` — new file |

### No code changes needed in
- `document_chunker.py` — already generic
- `responder.py` — already data-driven via `tasks.json`
- `common/` package — already generic by design

---

## What Becomes Possible

After these refactoring changes, the system can handle a completely new document domain (e.g., planning permit applications, medical records, financial reports) by:

1. **Writing a new `process/process_my_domain.json`** with:
   - `document_categorisation` rules
   - `extraction_categories` with domain keywords
   - `validation_directives` for the validator
   - `route_to_step` pointing to the correct task step
   - `preferred_model` if the domain needs a specific model

2. **Adding a task template to `tasks.json`** with the appropriate steps

3. **Zero changes to any agent code**

The current system requires code edits in at least 4 files to add a new document type. The refactored system requires only a new JSON file.

---

## Process Definition Schema Reference

```jsonc
{
  "process_id":   "proc-my-process",       // Unique ID referenced in tasks.json knowledge_id
  "process_name": "My Process Name",
  "version":      "1.0",
  "worker_id":    "dw-junior-engineer",

  // ── Routing (new) ──────────────────────────────────────────────────────
  "route_to_step": 4,                       // Which orchestrator step number handles this category
  "preferred_model": null,                  // Override model (null = use pipeline default)
  "force_category_for_extensions": [        // These extensions → always this category (skip LLM)
    ".xlsx", ".xls", ".xlsm", ".xlsb"
  ],

  // ── Content classification (existing) ────────────────────────────────
  "search_context": "...",                  // Passed to Document Reviewer for document discovery
  "document_categorisation": {
    "category": "TAL",                      // The category label assigned to matching documents
    "positive_indicators": { ... },
    "negative_indicators": { ... },
    "decision_rule": "..."
  },

  // ── Extraction config (new) ──────────────────────────────────────────
  "extraction_categories": [
    {
      "id":                  "asset_records",   // Tag applied to sections and step_extraction key
      "name":                "Asset Records",   // Human-readable label
      "keywords":            ["Asset ID", "Condition Rating", ...],  // Tagging trigger words
      "relevance_threshold": 0.3,               // Min score for a section to be included (0.0–1.0)
      "behavior":            "tabular"          // "standard" | "checklist" | "tabular"
    }
  ],

  // ── Validation (new) ────────────────────────────────────────────────
  "validation_directives": [
    {
      "id":                "TC-ASSET-COMPLETENESS",
      "name":              "Asset record row completeness",
      "trigger":           "extraction",          // When this directive activates
      "search_keywords":   ["Asset ID", "Serial Number"],
      "expected_category": "asset_records",
      "description":       "Count rows in source vs extracted records..."
    }
  ],

  // ── Post-extraction rules (new) ──────────────────────────────────────
  "conditional_rules": [
    {
      "id":          "rule-my-override",
      "description": "Human-readable explanation",
      "condition":   { "field": "transformer_size", "operator": "equals", "value": "1.5MVA" },
      "action":      { "field": "hv_switchgear", "set_value": "Siemens RLR" },
      "flag_field":  "hv_switchgear_override_applied"   // Optional: set to true when rule fires
    }
  ],

  // ── Existing process definition content (unchanged) ──────────────────
  "process_input":  { ... },
  "process_output": { ... },
  "process_steps":  [ ... ]
}
```
