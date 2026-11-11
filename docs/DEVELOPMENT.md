# Development

Keep modules small and dependency-free while the project targets a Pentium Gold 4415U with 4 GB RAM. Use the standard library unless a later step explicitly justifies a dependency. Run the test suite with:

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

Do not add credentials, model weights, datasets, or generated databases to the repository. Step 1's REST API is intentionally narrow: `/api/health`, `/api/status`, and validated `/api/messages` handling.

