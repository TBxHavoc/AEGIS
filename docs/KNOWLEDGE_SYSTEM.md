# Knowledge system

`KnowledgeIndex` ingests approved local text/code documents, tracks their source paths, and performs bounded local retrieval. Supported formats are plain text, Markdown, reStructuredText, Python, JSON, YAML, HTML, CSS, and JavaScript. Paths must remain below `AEGIS_KNOWLEDGE_ROOT`.

Knowledge storage is separate from model weights and training data. Research sessions record query/source history over this local index and do not access the network in Step 4.

