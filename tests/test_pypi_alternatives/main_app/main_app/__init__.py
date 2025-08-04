"""Main application module."""


def main() -> str:
    """Run the main application logic."""
    from shared_lib import greet

    return f"Main app says: {greet()}"
