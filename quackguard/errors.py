class AuditError(Exception):
    def __init__(self, code, detail=""):
        self.code = code
        self.detail = (str(detail) or code).replace("\r", " ").replace("\n", " ")[:200]
        super().__init__(self.detail)
