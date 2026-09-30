"""Deterministic logic around the model: context packing, redaction, caps, style.

Nothing here calls a model or shells out except through the injected callable
that ``AgentBridge`` drives. The other pieces are pure functions or thin
readers over the local database so each can be tested without a model.
"""

from .bridge import AgentBridge, AgentRunner, AgentRunResult

__all__ = ["AgentBridge", "AgentRunResult", "AgentRunner"]
