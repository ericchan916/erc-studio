"""Stable, content-free failure codes; details contain only explicit safe metadata."""
class BaseError(Exception):
    def __init__(self, code, *, unknown=False, category=None, details=None):
        super().__init__(code)
        self.code = code
        self.unknown = unknown
        self.category = category
        self.details = details or {}
