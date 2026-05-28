"""
Generate department asymmetric keypairs and save to data/keys/.
This script only creates keys and does not touch MongoDB or Vault.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crypto.asym import generate_all_department_keypairs

def main():
    os.makedirs("data/keys", exist_ok=True)
    keypairs = generate_all_department_keypairs("ECC")
    for dept, kp in keypairs.items():
        with open(f"data/keys/{dept}_public.pem", "w") as f:
            f.write(kp["public_pem"])
        with open(f"data/keys/{dept}_private.pem", "w") as f:
            f.write(kp["private_pem"])
    print("Saved keypairs to data/keys/")

if __name__ == '__main__':
    main()
