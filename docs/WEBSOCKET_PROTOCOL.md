# WebSocket protocol

Connect to `ws://127.0.0.1:8765/` for compatibility with the restored visualizer, or `/api/ws` for the chat client. Text frames contain JSON objects:

```json
{"type":"conversation.message","request_id":"unique-id","payload":{"message":"hello"}}
```

Every event has `type`, `request_id`, and `payload`. Supported event types include `client.connected`, `client.disconnected`, `conversation.message`, `conversation.response.started`, `conversation.response.chunk`, `conversation.response.completed`, `conversation.response.failed`, `conversation.response.cancel`, `conversation.response.interrupt`, `audio.session.start`, `audio.chunk`, `audio.session.end`, `audio.session.cancel`, `system.status`, and `system.error`.

Voice clients explicitly enable the microphone first, then send `audio.session.start`, bounded `audio.chunk` messages containing base64 mono PCM16 at 16 kHz, and `audio.session.end`. Use a unique request ID for each event; the end event ID also identifies the transcript conversation. `audio.session.cancel` discards the current audio. Audio is limited to 500 ms per chunk, 30 seconds per utterance, and 64 KiB per WebSocket frame. See [VOICE_RECOGNITION.md](VOICE_RECOGNITION.md) for details.

Request IDs are required for client-originated events and are deduplicated in a bounded cache. Duplicate requests produce `system.error` with `duplicate_request`. Invalid events produce `system.error` with `invalid_event`.

Conversation responses stream from a worker so the connection can continue reading status and cancel/interrupt events while generation is running. Each response event is serialized on the socket, and cancel/interrupt only targets an active request on that connection. Cancellation is cooperative between provider chunks. With no provider configured the server emits `conversation.response.failed` with `inference_not_configured`; it does not invent a response.

