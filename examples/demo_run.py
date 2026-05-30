from pathlib import Path

from chainalpha.pipeline import run_scan

if __name__ == "__main__":
    vault = Path("./vault")
    signals, markdown, saved = run_scan(vault, out_dir="./outputs/reports")
    print(f"signals={len(signals)}")
    print(f"saved={saved}")
