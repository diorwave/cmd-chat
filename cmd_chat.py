import sys
import os

_venv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv")
_venv_python = os.path.join(_venv_dir, "bin", "python3")

if os.path.exists(_venv_python) and os.path.realpath(sys.prefix) != os.path.realpath(_venv_dir):
    os.execv(_venv_python, [_venv_python] + sys.argv)

if __name__ == "__main__":
    from cmd_chat import main
    main()
