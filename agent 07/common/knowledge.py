"""
Process Knowledge Registry
==========================
Loads all process definition files from the process/ directory and provides
typed accessors for agents and the orchestrator to query them.

A "process" now defines everything agents previously hardcoded:
  - What documents to target      (search_context)
  - How to classify them          (document_categorisation + force_category_for_extensions)
  - Which step routes to it       (route_to_step)
  - What to extract               (extraction_categories — id, keywords, thresholds, behavior)
  - What model to use             (preferred_model)
  - What to validate              (validation_directives)
  - Any post-extraction rules     (conditional_rules)

Usage:
    from common.knowledge import KnowledgeRegistry

    registry = KnowledgeRegistry()

    # Routing
    category = registry.category_for_extension(".xlsx")      # → "TAL"
    step     = registry.route_for_category("Design Brief")   # → 4

    # Extraction
    proc = registry.get("proc-extract-design-brief-info")
    cats = proc.extraction_category_ids()                    # → ["hv", "lv", ...]
    kwds = proc.get_category_keywords("hv")                  # → ["HV", "11kV", ...]
    low  = proc.low_threshold_categories()                   # → ["funding", "easement"]

    # Validation
    directives = registry.validation_directives_for_process("proc-extract-design-brief-info")

    # Prompt building (for Document Reviewer)
    rules_str = registry.categorisation_rules_string(["proc-extract-design-brief-info", ...])
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ProcessDefinition:
    """
    Typed wrapper around a single process JSON file.

    Gracefully degrades when newer fields (extraction_categories,
    validation_directives, etc.) are absent — agents can check
    `proc.extraction_categories` and get an empty list rather than
    a KeyError.
    """

    def __init__(self, data: dict):
        self._data = data

    # ── Core identity ───────────────────────────────────────────────────────────

    @property
    def process_id(self) -> str:
        return self._data["process_id"]

    @property
    def process_name(self) -> str:
        return self._data.get("process_name", "")

    @property
    def search_context(self) -> str:
        return self._data.get("search_context", "")

    @property
    def summary(self) -> str:
        return self._data.get("summary", "")

    # ── Classification & routing ────────────────────────────────────────────────

    @property
    def document_category(self) -> Optional[str]:
        """The category label assigned to matching documents (e.g. 'Design Brief')."""
        return self._data.get("document_categorisation", {}).get("category")

    @property
    def route_to_step(self) -> Optional[int]:
        """
        Which task step number in the orchestrator plan processes documents
        of this category.  Previously hardcoded in orchestrator.py and
        document_reviewer.py.
        """
        return self._data.get("route_to_step")

    @property
    def force_category_for_extensions(self) -> List[str]:
        """
        File extensions that unconditionally map to this process's category,
        bypassing LLM-based classification entirely.
        Example: [".xlsx", ".xlsm", ".xls", ".xlsb"] → "TAL"

        Previously implemented as RULE 0 / SPREADSHEET_EXTENSIONS in
        document_reviewer.py.
        """
        return self._data.get("force_category_for_extensions") or []

    @property
    def document_categorisation(self) -> dict:
        return self._data.get("document_categorisation", {})

    # ── Model selection ─────────────────────────────────────────────────────────

    @property
    def preferred_model(self) -> Optional[str]:
        """
        Override the pipeline-default LLM model for this process.
        None means use the agent's configured default.
        Previously hardcoded per-process in document_extractor.py.
        """
        return self._data.get("preferred_model")

    # ── Extraction configuration ────────────────────────────────────────────────

    @property
    def extraction_categories(self) -> List[dict]:
        """
        Machine-readable definitions for each content category this process
        extracts.  Each entry:
          {
            "id":                  "hv",
            "name":                "High-Voltage Works",
            "keywords":            ["HV", "11kV", "22kV", ...],
            "relevance_threshold": 0.4,
            "behavior":            "standard" | "checklist" | "tabular"
          }
        Previously the keywords were hardcoded in the extractor's LLM prompt
        and the thresholds in LOW_THRESHOLD_TAGS / CHECKLIST_TAGS constants.
        """
        return self._data.get("extraction_categories") or []

    def extraction_category_ids(self) -> List[str]:
        """Return just the IDs for quick set-membership tests."""
        return [c["id"] for c in self.extraction_categories]

    def low_threshold_categories(self) -> List[str]:
        """IDs of categories that use a lower relevance threshold (< 0.35)."""
        return [
            c["id"] for c in self.extraction_categories
            if c.get("relevance_threshold", 0.4) < 0.35
        ]

    def checklist_categories(self) -> List[str]:
        """IDs of categories that use checklist (force-include first sections) behavior."""
        return [
            c["id"] for c in self.extraction_categories
            if c.get("behavior") == "checklist"
        ]

    def get_category_keywords(self, category_id: str) -> List[str]:
        for cat in self.extraction_categories:
            if cat["id"] == category_id:
                return cat.get("keywords") or []
        return []

    def get_category_threshold(self, category_id: str) -> float:
        for cat in self.extraction_categories:
            if cat["id"] == category_id:
                return cat.get("relevance_threshold", 0.4)
        return 0.4

    def tagging_rules_prompt_block(self) -> str:
        """
        Build the extraction category section of a tagging LLM prompt
        from this process's machine-readable categories.

        Replaces the hardcoded keyword-to-tag mapping block previously
        embedded in document_extractor.py.
        """
        if not self.extraction_categories:
            return ""
        lines = ["Extraction categories for this process (assign these tags):"]
        for cat in self.extraction_categories:
            kwds = ", ".join(f'"{k}"' for k in cat.get("keywords", []))
            lines.append(
                f"- Tag '{cat['id']}' ({cat['name']}): "
                f"sections mentioning any of [{kwds}]"
            )
        return "\n".join(lines)

    # ── Validation directives ───────────────────────────────────────────────────

    @property
    def validation_directives(self) -> List[dict]:
        """
        Test case templates the Step Validator generates and executes for
        this process.  Each entry:
          {
            "id":              "TC-HV",
            "name":            "High-voltage assets extraction",
            "trigger":         "extraction",
            "search_keywords": ["HV", "high voltage", "11kV", ...],
            "expected_category": "hv"
          }
        Previously these were hardcoded in step_validator_agent.py SYSTEM_PROMPT.
        """
        return self._data.get("validation_directives") or []

    def validation_directives_prompt_block(self) -> str:
        """
        Build the injectable test directive block for the Step Validator prompt.
        Returns empty string if no directives are defined.
        """
        if not self.validation_directives:
            return ""
        lines = [
            "PROCESS-SPECIFIC TEST DIRECTIVES (generated from process definition):",
            "Execute each of the following test cases in addition to the standard ones:",
            "",
        ]
        for d in self.validation_directives:
            kwds = ", ".join(f'"{k}"' for k in d.get("search_keywords", []))
            lines.append(
                f"{d['id']}: {d['name']}\n"
                f"  Search source text for keywords: [{kwds}].\n"
                f"  Verify step_extraction contains '{d['expected_category']}' "
                f"key with matching items.\n"
            )
        return "\n".join(lines)

    # ── Conditional rules ───────────────────────────────────────────────────────

    @property
    def conditional_rules(self) -> List[dict]:
        """
        If-then rules applied post-extraction to override or augment values.
        Each entry:
          {
            "id":          "rule-siemens-rlr",
            "description": "If transformer size is 1.5MVA, HV switchgear = Siemens RLR",
            "condition":   {"field": "transformer_size", "operator": "equals", "value": "1.5MVA"},
            "action":      {"field": "hv_switchgear", "set_value": "Siemens RLR"},
            "flag_field":  "hv_switchgear_override_applied"
          }
        Previously described only in free-text step descriptions.
        """
        return self._data.get("conditional_rules") or []

    def apply_conditional_rules(self, extraction: dict) -> dict:
        """
        Apply all conditional rules to an extraction result dict in-place.
        Returns the (possibly mutated) extraction.
        """
        for rule in self.conditional_rules:
            cond = rule.get("condition", {})
            field_val = extraction.get(cond.get("field", ""))
            op = cond.get("operator", "equals")
            target = cond.get("value", "")

            matched = False
            if op == "equals" and str(field_val).strip().lower() == str(target).strip().lower():
                matched = True
            elif op == "contains" and target.lower() in str(field_val).lower():
                matched = True

            if matched:
                action = rule.get("action", {})
                extraction[action["field"]] = action["set_value"]
                if rule.get("flag_field"):
                    extraction[rule["flag_field"]] = True
                logger.info(f"[Rules] Applied '{rule['id']}': {action}")

        return extraction

    # ── Raw access ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        return f"<ProcessDefinition id={self.process_id} category={self.document_category}>"


# ── Registry ────────────────────────────────────────────────────────────────────

class KnowledgeRegistry:
    """
    Loads all process/*.json files and provides lookup methods used by
    agents to replace their hardcoded domain logic.
    """

    def __init__(self, process_dir: Optional[str] = None):
        base = (
            Path(process_dir)
            if process_dir
            else Path(__file__).parent.parent / "process"
        )
        self._processes: Dict[str, ProcessDefinition] = {}
        self._load(base)
        logger.info(
            f"[KnowledgeRegistry] Loaded {len(self._processes)} process(es): "
            f"{list(self._processes.keys())}"
        )

    def _load(self, process_dir: Path) -> None:
        if not process_dir.exists():
            logger.warning(f"[KnowledgeRegistry] Process dir not found: {process_dir}")
            return
        for path in sorted(process_dir.glob("*.json")):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                proc = ProcessDefinition(data)
                self._processes[proc.process_id] = proc
            except Exception as exc:
                logger.error(f"[KnowledgeRegistry] Failed to load {path.name}: {exc}")

    # ── Lookup ──────────────────────────────────────────────────────────────────

    def get(self, process_id: str) -> Optional[ProcessDefinition]:
        """Return a ProcessDefinition by its process_id, or None."""
        return self._processes.get(process_id)

    def all(self) -> List[ProcessDefinition]:
        return list(self._processes.values())

    def all_categories(self) -> List[str]:
        """Return all known document category names."""
        return [p.document_category for p in self._processes.values() if p.document_category]

    # ── Extension-based forced classification ───────────────────────────────────

    def category_for_extension(self, extension: str) -> Optional[str]:
        """
        Return the forced document category for a file extension, if any
        process defines a force_category_for_extensions rule.

        Example: ".xlsx" → "TAL"
        Replaces RULE 0 / SPREADSHEET_EXTENSIONS in document_reviewer.py.
        """
        ext = extension.lower()
        for proc in self._processes.values():
            if ext in proc.force_category_for_extensions:
                return proc.document_category
        return None

    def all_forced_extensions(self) -> Dict[str, str]:
        """Return {extension: category} for all forced-extension rules."""
        result = {}
        for proc in self._processes.values():
            for ext in proc.force_category_for_extensions:
                result[ext] = proc.document_category
        return result

    # ── Routing ─────────────────────────────────────────────────────────────────

    def process_for_category(self, category: str) -> Optional[ProcessDefinition]:
        for proc in self._processes.values():
            if proc.document_category == category:
                return proc
        return None

    def route_for_category(self, category: str) -> Optional[int]:
        """
        Return the route_to_step number for a document category.
        Replaces hardcoded if/elif chain in document_reviewer.py and orchestrator.py.
        """
        proc = self.process_for_category(category)
        return proc.route_to_step if proc else None

    def category_for_knowledge_id(self, knowledge_id: str) -> Optional[str]:
        """
        Given a knowledge_id string (possibly comma-separated), return the
        primary document category.  Used by orchestrator to build step inputs.
        Replaces hardcoded if/elif in orchestrator.py.
        """
        for pid in knowledge_id.split(","):
            proc = self.get(pid.strip())
            if proc and proc.document_category:
                return proc.document_category
        return None

    # ── Model selection ─────────────────────────────────────────────────────────

    def model_for_process(self, process_id: str, default: str) -> str:
        """
        Return the preferred model for a process, falling back to the default.
        Replaces hardcoded process_id == "proc-extract-asset-spreadsheet" checks.
        """
        proc = self.get(process_id)
        return proc.preferred_model if proc and proc.preferred_model else default

    # ── Validation ──────────────────────────────────────────────────────────────

    def validation_directives_for_process(
        self, process_id: str
    ) -> List[dict]:
        """
        Return the validation directives for a process.
        Used by orchestrator to inject directives into step validator calls.
        """
        proc = self.get(process_id)
        return proc.validation_directives if proc else []

    def validation_prompt_block_for_process(self, process_id: str) -> str:
        """
        Build a ready-to-inject validation directive prompt section.
        Returns empty string if the process has no directives.
        """
        proc = self.get(process_id)
        return proc.validation_directives_prompt_block() if proc else ""

    # ── Prompt building helpers ─────────────────────────────────────────────────

    def categorisation_rules_string(self, process_ids: List[str]) -> str:
        """
        Build the categorisation rules string used by Document Reviewer's
        LLM planner — driven entirely by registry data, replacing the
        inline string-building code in document_reviewer.py.
        """
        parts = []
        for pid in process_ids:
            proc = self.get(pid.strip())
            if not proc:
                continue
            dc = proc.document_categorisation
            pos = dc.get("positive_indicators", {})
            neg = dc.get("negative_indicators", {})
            entry = (
                f"Category '{proc.document_category}':\n"
                f"  Positive filename patterns: {pos.get('filename_patterns', [])}\n"
                f"  Document type values:        {pos.get('document_type_values', [])}\n"
                f"  Negative filename patterns:  {neg.get('filename_patterns', [])}\n"
                f"  Decision rule:               {dc.get('decision_rule', '')}"
            )
            if proc.route_to_step:
                entry += f"\n  Route to step:               {proc.route_to_step}"
            if proc.force_category_for_extensions:
                entry += f"\n  Force category for extensions: {proc.force_category_for_extensions}"
            parts.append(entry)
        return "\n\n".join(parts)
