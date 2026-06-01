class VarNamer:
    _counters: dict[str, int]

    def __init__(self) -> None:
        self._counters = {}

    def __call__(self, basename: str) -> str:
        counter = self._counters.setdefault(basename, 1)
        self._counters[basename] += 1
        if counter == 1:
            return basename
        else:
            return f"{basename}.{counter}"
