"""Stable certificate error codes, also usable by stdlib-only offline tools."""


class CertificateError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
