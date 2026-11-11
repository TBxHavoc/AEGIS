# Resource Limits

The default design uses standard-library components, bounded executors, a 1024-token model context default, two inference threads, and lazy model loading. A resource snapshot reports resident memory where `/proc` is available. Multiple large models are not loaded by the application.
