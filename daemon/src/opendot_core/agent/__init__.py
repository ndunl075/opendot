"""Deterministic logic around the model: context packing, redaction, caps, style.

Nothing here calls a model or shells out. The pieces are pure functions or thin
readers over the local database so each can be tested without a model.
"""
