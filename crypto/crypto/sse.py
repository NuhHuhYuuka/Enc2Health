"""Static SSE helper for keyword search over encrypted EHR metadata.

This is a compact encrypted inverted-index baseline:
- search token = HMAC-SHA256(K_token, normalized keyword)
- posting list = AES-GCM(K_postings, JSON patient ids)

It intentionally exposes search pattern and result volume, which is what the
project measures as SSE leakage.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import unicodedata
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


TOKEN_KEY_SIZE = 32
POSTING_KEY_SIZE = 32
KEY_SIZE = TOKEN_KEY_SIZE + POSTING_KEY_SIZE


def normalize_keyword(text: str) -> str:
    """Normalize Vietnamese/English clinical text into stable keyword tokens."""
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    return re.sub(r"\s+", " ", text).strip()


def tokenize_text(text: str) -> list[str]:
    normalized = normalize_keyword(text)
    tokens = re.findall(r"[\w]+", normalized, flags=re.UNICODE)
    # Keep single-character ICD fragments out, but preserve ICD codes like E11.
    return sorted({token for token in tokens if len(token) >= 2})


class StaticSSECipher:
    """HMAC-token + AES-GCM posting-list static SSE baseline."""

    def __init__(self, key: bytes | None = None):
        key = key or os.urandom(KEY_SIZE)
        if len(key) != KEY_SIZE:
            raise ValueError(f"SSE key must be {KEY_SIZE} bytes")
        self._key = key
        self._token_key = key[:TOKEN_KEY_SIZE]
        self._posting_key = key[TOKEN_KEY_SIZE:]
        self._aes = AESGCM(self._posting_key)

    @property
    def key(self) -> bytes:
        return self._key

    def token(self, keyword: str) -> str:
        normalized = normalize_keyword(keyword)
        digest = hmac.new(self._token_key, normalized.encode("utf-8"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def encrypt_postings(self, postings: list[dict[str, Any]]) -> str:
        nonce = os.urandom(12)
        plaintext = json.dumps(postings, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ciphertext = self._aes.encrypt(nonce, plaintext, None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt_postings(self, postings_enc: str) -> list[dict[str, Any]]:
        raw = base64.b64decode(postings_enc)
        nonce, ciphertext = raw[:12], raw[12:]
        plaintext = self._aes.decrypt(nonce, ciphertext, None)
        data = json.loads(plaintext)
        if not isinstance(data, list):
            raise ValueError("SSE postings payload must be a list")
        return data

    def save_key(self, path: str):
        with open(path, "w") as f:
            f.write(base64.b64encode(self._key).decode("ascii"))

    @classmethod
    def load_key(cls, path: str) -> "StaticSSECipher":
        with open(path) as f:
            key = base64.b64decode(f.read().strip())
        return cls(key)
