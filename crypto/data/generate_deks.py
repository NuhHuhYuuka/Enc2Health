"""
Generate symmetric DEKs for AES-GCM, AES-SIV (DTE), and ORE, and save to data/keys.
"""
import os, base64, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crypto.dte import DTECipher
from crypto.ore import ORECipher
from crypto.gcm import AESGCMCipher

def main():
    os.makedirs("data/keys", exist_ok=True)
    dte_ma = DTECipher()
    dte_khoa = DTECipher()
    ore = ORECipher()
    gcm = AESGCMCipher()

    dte_ma.save_key("data/keys/dte_ma_benh.key")
    dte_khoa.save_key("data/keys/dte_khoa.key")
    ore.save_key("data/keys/ore.key")
    with open("data/keys/gcm_dek.key", "w") as f:
        f.write(base64.b64encode(gcm.key).decode())

    print("Saved DEKs to data/keys/")

if __name__ == '__main__':
    main()
