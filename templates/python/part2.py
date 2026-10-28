def process(text: str) -> str:
    # Placeholder: total number of characters across all lines.
    return str(sum(len(line) for line in text.splitlines()))
