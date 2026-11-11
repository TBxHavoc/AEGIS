# Voice testing

`tests/test_step3.py` contains mocked tests for punctuation preservation, global speaking-rate consistency, preference persistence, provider-unavailable behavior, microphone state, duplicate request suppression, and playback stop ownership.

These tests do not synthesize or listen to audio. They cannot prove acoustic naturalness, actual real-time factor, pronunciation, pause duration, or microphone permissions. No real audio test is claimed as passed until a user-approved provider, voice model, audio device, and capture/playback setup are installed and exercised on the target laptop.

To run the current mocked and backend suite:

```sh
/usr/bin/python3 -m unittest discover -s tests -v
```

