from __future__ import annotations
import argparse, subprocess, sys

def main() -> int:
    parser = argparse.ArgumentParser(description="运行 EvoHarnessAlert 工程检查。")
    parser.add_argument("--pytest", action="store_true", help="运行 pytest")
    args = parser.parse_args()
    if args.pytest: return subprocess.call([sys.executable, "-m", "pytest", "-q"])
    print("EvoHarnessAlert 工程检查：使用 --pytest 运行测试。")
    return 0

if __name__ == "__main__": raise SystemExit(main())
