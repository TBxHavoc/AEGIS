# Intelligence architecture

The intelligence package defines contracts for intent detection, context interpretation, reasoning, planning, decomposition, response generation, tool selection, and tool-result interpretation. `IntelligenceOrchestrator` coordinates preparation without owning model inference.

The included deterministic helpers are explicitly labelled heuristics and context bookkeeping. They do not claim language-model reasoning and do not generate assistant answers. A configured inference provider remains the only source of model-generated response text.

