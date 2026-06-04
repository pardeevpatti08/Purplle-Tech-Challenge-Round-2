import pytest
import sys

if __name__ == "__main__":
    print("Running pytest...")
    ret = pytest.main(["tests/"])
    print(f"Pytest exited with code {ret}")
    sys.exit(ret)
