import os, base64, json
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

class AESGCMCipher:
    def __init__(self, key: bytes = None):
        self._key = key or os.urandom(32)  # 256-bit
        self._aes = AESGCM(self._key)

    @property
    def key(self) -> bytes:
        return self._key

    def encrypt(self, plaintext) -> str:
        if not isinstance(plaintext, bytes):
            plaintext = json.dumps(plaintext).encode()
        nonce = os.urandom(12)
        ct = self._aes.encrypt(nonce, plaintext, None)
        return base64.b64encode(nonce + ct).decode()

    def decrypt(self, ciphertext_b64: str):
        raw = base64.b64decode(ciphertext_b64)
        nonce, ct = raw[:12], raw[12:]
        return self._aes.decrypt(nonce, ct, None)

    def decrypt_json(self, ciphertext_b64: str):
        return json.loads(self.decrypt(ciphertext_b64))

    def decrypt_float(self, ciphertext_b64: str) -> float:
        return float(self.decrypt(ciphertext_b64))
