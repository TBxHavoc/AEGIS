# Voice behaviour contract

- One explicit `speaking_rate` applies to the entire synthesis request.
- Commas and sentence punctuation are passed to the provider as ordinary text. They may produce the provider's natural short pause, but never alter rate, pitch, or request boundaries.
- AEGIS does not inject SSML breaks, split comma-separated clauses into separate synthesis requests, or change rate based on commas, full stops, questions, or exclamations.
- TTS receives the intended assistant text unchanged. It never adds `boss`, a greeting, filler, or an unwanted form of address.
- A request ID owns at most one TTS synthesis/playback operation. New playback cancels old playback; stop cancels synthesis and playback.
- Muted output is a deliberate no-op, not a fake successful audio result.

