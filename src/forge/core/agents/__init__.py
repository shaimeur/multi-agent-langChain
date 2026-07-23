"""The agent nodes (cahier §4). Each is a small factory that closes over its
injected dependencies — the LLM, the retrieval resources — and returns a plain
``node(state) -> update`` callable the graph wires together."""
