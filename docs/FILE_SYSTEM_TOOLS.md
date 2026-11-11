# File-system tools

All file operations use `ToolRegistry` and canonical paths. `AEGIS_FILE_ROOTS` is a platform-path-separated list of approved roots; if unset, only the knowledge-document root is available. Every path must remain under one configured root. Symlink components and paths containing `.git`, `.agents`, `.codex`, or `.aws` are rejected. Listings omit those protected directories and do not follow symlinks.

| Tool | Behavior |
| --- | --- |
| `file.metadata`, `document.read`, `file.list`, `file.find`, `file.search`, `file.hash` | Read-only metadata, bounded UTF-8 text, directory entries, filename/content search, and SHA-256. |
| `file.create`, `directory.create`, `file.move`, `file.copy` | Local writes; require an allowlist entry, `WRITE_LOCAL` policy, and explicit loopback confirmation. Create/copy/move do not overwrite existing targets. |
| `file.replace` | Destructive explicit confirmation; makes a one-time `.aegis-backup` before atomic replacement. Replacement is limited to 1 MB. |
| `file.delete` | Deletes one file only, with explicit confirmation. Recursive deletion is not provided. |

Text reads are limited to 1 MB on disk and return at most 12 KB. Content search scans at most 3,000 files and returns at most 100 matches. Hashing is limited to 100 MB. Writes use a same-directory temporary file, flush/fsync, and atomic replacement. Audit records include the tool and affected path fields, never file contents.

Configure `AEGIS_ALLOWED_TOOLS` and `AEGIS_MAX_PERMISSION=WRITE_LOCAL` to enable ordinary writes. `file.replace` and `file.delete` also require explicit confirmation. Knowledge ingestion uses `knowledge.ingest`, is text-only and size-bounded, and requires local confirmation through `POST /api/knowledge/ingest`.
