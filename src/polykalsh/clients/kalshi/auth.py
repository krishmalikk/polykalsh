"""
Kalshi RSA-PSS authentication.

Signs API requests using RSA-PSS with SHA-256.
"""

import base64
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class KalshiAuth:
    """Handles RSA-PSS authentication for Kalshi API requests."""

    def __init__(self, api_key_id: str, private_key_path: str):
        """
        Initialize Kalshi authenticator.

        Args:
            api_key_id: Kalshi API key ID
            private_key_path: Path to RSA private key PEM file
        """
        self.api_key_id = api_key_id
        self._private_key = self._load_private_key(private_key_path)

    def _load_private_key(self, path: str) -> rsa.RSAPrivateKey:
        """Load RSA private key from PEM file."""
        key_path = Path(path)
        if not key_path.exists():
            raise FileNotFoundError(f"Private key not found: {path}")

        with open(key_path, "rb") as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=None,
            )

        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise TypeError("Key must be an RSA private key")

        return private_key

    def sign(self, message: str) -> str:
        """
        Sign a message using RSA-PSS with SHA-256.

        Args:
            message: The message to sign

        Returns:
            Base64-encoded signature
        """
        signature = self._private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")

    def get_auth_headers(self, method: str, path: str) -> dict[str, str]:
        """
        Generate authentication headers for an API request.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: Request path (e.g., /trade-api/v2/markets)

        Returns:
            Dict with required auth headers
        """
        # Timestamp in milliseconds
        timestamp = str(int(time.time() * 1000))

        # Message to sign: timestamp + method + path
        message = f"{timestamp}{method.upper()}{path}"
        signature = self.sign(message)

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }
