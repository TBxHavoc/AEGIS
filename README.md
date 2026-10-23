
# AEGIS

A locally run personal AI assistant project, starting with a tiny
character-level neural language model trained from random weights.

## Current status

- [x] Local Python environment
- [x] Randomly initialized trainable model
- [x] Character-level next-character prediction
- [x] Training loss tracking
- [x] Text generation
- [x] JSON checkpoint save/load
- [x] Automated tests

## Requirements

- Python 3.14
- No external AI APIs
- No third-party Python packages for the initial prototype

## Run the model

Activate the virtual environment:

```bash
source .venv/bin/activate
```

Train the model:

```bash
python scripts/train.py
```

Run tests:

```bash
python -m unittest discover -s tests -v
```

## Architecture

The initial TinyLM is a trainable neural bigram model. It learns
next-character probabilities using softmax and gradient descent.

It is an educational prototype, not yet a conversational assistant
or Transformer-based language model.

## Security

- Do not commit secrets or credentials.
- Keep virtual environments out of version control.
- Keep local model checkpoints out of version control.
- Review changes before pushing to the remote repository.

## Roadmap

- [ ] Improve model architecture
- [ ] Add reproducible training and evaluation
- [ ] Expand the local training corpus
- [ ] Add persistent memory with user-controlled storage
- [ ] Add permission-controlled tools
- [ ] Explore local voice interaction
